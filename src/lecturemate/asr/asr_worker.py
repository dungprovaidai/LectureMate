"""Bộ điều khiển ASR streaming: rolling buffer + VAD → partial (hiển thị) / final (lưu, dịch).

Nguyên tắc (02-thiet-ke/04-kien-truc.md):
- Chunk ~100 ms chỉ để cập nhật buffer; KHÔNG dịch từng chunk.
- Partial phát định kỳ khi đang nói, cùng ``segment_id`` với final sắp tới → UI cập nhật
  cùng một dòng, không nhân bản.
- Final khi im lặng đủ lâu (``silence_to_final_s``) hoặc câu quá dài (``max_segment_s``,
  cắt ở chỗ năng lượng thấp nhất và mang phần còn lại sang câu sau, không chồng lấn audio).
- Khi ASR tụt lại phía sau (hàng đợi > ``max_lag_s``), bỏ partial, chỉ làm final.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..audio.audio_worker import END, FLUSH, AudioFrame
from ..domain.models import TranscriptEvent
from .segment_committer import SegmentCommitter
from .transcriber import Transcriber
from .vad import FRAME, EnergyVAD

log = logging.getLogger(__name__)
SR = 16_000


@dataclass
class ASRStats:
    audio_ms: int = 0
    asr_busy_ms: float = 0.0
    partials: int = 0
    finals: int = 0
    skipped_partials: int = 0
    errors: int = 0

    @property
    def realtime_factor(self) -> float:
        """Thời gian xử lý / thời lượng audio. < 1 nghĩa là theo kịp realtime."""
        return self.asr_busy_ms / self.audio_ms if self.audio_ms else 0.0


class StreamingASR:
    """Logic thuần (không thread) — dễ test. ``feed`` từng AudioFrame theo thứ tự."""

    def __init__(
        self,
        transcriber: Transcriber,
        committer: SegmentCommitter,
        on_event: Callable[[TranscriptEvent], None],
        partial_interval_s: float = 1.0,
        silence_to_final_s: float = 0.6,
        max_segment_s: float = 12.0,
        min_speech_s: float = 0.25,
        preroll_s: float = 0.3,
        use_prompt: bool = True,
        vad: EnergyVAD | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        on_error: Callable[[str], None] | None = None,
    ):
        self.tr = transcriber
        self.committer = committer
        self.on_event = on_event
        self.partial_interval = partial_interval_s
        self.silence_final = int(silence_to_final_s * SR)
        self.max_len = int(max_segment_s * SR)
        self.min_speech = int(min_speech_s * SR)
        self.preroll = int(preroll_s * SR)
        self.use_prompt = use_prompt
        self.vad = vad or EnergyVAD()
        self.clock_ns = clock_ns
        self.on_error = on_error
        self._reported_error = False
        self.stats = ASRStats()
        self.allow_partials = True
        self._reset(0)
        self._rem = np.zeros(0, np.float32)
        self._rem_start = 0

    # ---------------------------------------------------------------- state
    def _reset(self, start_sample: int) -> None:
        self.buf: list[np.ndarray] = []
        self.buf_len = 0
        self.buf_start = start_sample
        self.in_utt = False
        self.speech_samples = 0
        self.silence_run = 0
        self.last_speech_ns = 0
        self.seg_id = uuid.uuid4().hex
        self.version = 0
        self.since_partial = 0
        self.frame_energy: list[float] = []

    def _audio(self) -> np.ndarray:
        return np.concatenate(self.buf) if self.buf else np.zeros(0, np.float32)

    # ----------------------------------------------------------------- feed
    def feed(self, frame: AudioFrame) -> None:
        x = frame.samples
        self.stats.audio_ms += len(x) * 1000 // SR
        # ghép phần dư < 30ms của lần trước để VAD luôn chạy trên khung 30ms đầy đủ
        if self._rem.size:
            x = np.concatenate([self._rem, x])
            start = self._rem_start
        else:
            start = frame.start_sample
        n_full = (len(x) // FRAME) * FRAME
        self._rem = x[n_full:]
        self._rem_start = start + n_full
        for i in range(0, n_full, FRAME):
            self._feed_sub(x[i:i + FRAME], start + i, frame.captured_ns)
        # nhịp partial tính theo thời gian AUDIO (không theo đồng hồ tường) → ổn định khi
        # xử lý nhanh hơn realtime (phiên âm file) lẫn khi chạy trực tiếp
        if self.in_utt:
            self.since_partial += len(frame.samples)
            if self.allow_partials and self.since_partial >= self.partial_interval * SR:
                self._partial()
                self.since_partial = 0

    def _feed_sub(self, sub: np.ndarray, sub_start: int, captured_ns: int) -> None:
        speech = self.vad.is_speech(sub)
        if not self.in_utt:
            if not self.buf:
                self.buf_start = sub_start
            self.buf.append(sub)
            self.buf_len += len(sub)
            self.frame_energy.append(float(np.mean(sub * sub)))
            # chỉ giữ preroll khi chưa có giọng nói
            while self.buf_len > self.preroll and len(self.buf) > 1:
                dropped = self.buf.pop(0)
                self.frame_energy.pop(0)
                self.buf_len -= len(dropped)
                self.buf_start += len(dropped)
            if speech:
                self.in_utt = True
                self.speech_samples = len(sub)
                self.silence_run = 0
                self.last_speech_ns = captured_ns
                self.since_partial = 0
            return
        self.buf.append(sub)
        self.buf_len += len(sub)
        self.frame_energy.append(float(np.mean(sub * sub)))
        if speech:
            self.speech_samples += len(sub)
            self.silence_run = 0
            self.last_speech_ns = captured_ns
        else:
            self.silence_run += len(sub)
        if self.silence_run >= self.silence_final:
            self.finalize("silence")
        elif self.buf_len >= self.max_len:
            self._cut_long()

    # ------------------------------------------------------------- emitters
    def _partial(self) -> None:
        audio = self._audio()
        if len(audio) < self.min_speech:
            return
        t0 = time.perf_counter()
        try:
            segs = self.tr.transcribe(audio, self.buf_start * 1000 // SR, final=False)
        except Exception:
            self.stats.errors += 1
            log.exception("ASR partial lỗi")
            self._report_error("nhận dạng tạm thời thất bại")
            return
        self.stats.asr_busy_ms += (time.perf_counter() - t0) * 1000
        text = " ".join(s.text for s in segs).strip()
        if not text:
            return
        self.version += 1
        self.stats.partials += 1
        self.on_event(TranscriptEvent("partial", self.committer.session_id, self.seg_id,
                                      self.buf_start * 1000 // SR,
                                      (self.buf_start + self.buf_len) * 1000 // SR, text, self.version))

    def finalize(self, reason: str = "flush") -> TranscriptEvent | None:
        if not self.in_utt or self.speech_samples < self.min_speech:
            seg_id, had_partial = self.seg_id, self.version > 0
            self._reset(self.buf_start + self.buf_len)
            if had_partial:
                self.on_event(TranscriptEvent("discard", self.committer.session_id, seg_id, 0, 0, ""))
            return None
        audio = self._audio()
        # bỏ bớt im lặng cuối (giữ 200ms) để timestamp end chính xác hơn
        trail = max(0, self.silence_run - int(0.2 * SR))
        if trail:
            audio = audio[:len(audio) - trail]
        speech_ratio = self.speech_samples / max(1, len(audio))
        result = self._transcribe_final(audio, self.buf_start, speech_ratio)
        next_start = self.buf_start + self.buf_len
        self._reset(next_start)
        return result

    def _transcribe_final(self, audio: np.ndarray, start_sample: int, speech_ratio: float) -> TranscriptEvent | None:
        seg_id, boundary_ns = self.seg_id, self.last_speech_ns
        offset_ms = start_sample * 1000 // SR
        prompt = self.committer.prev_text[-200:] if (self.use_prompt and self.committer.prev_text) else None
        t0 = time.perf_counter()
        try:
            segs = self.tr.transcribe(audio, offset_ms, final=True, prompt=prompt)
        except Exception as exc:
            self.stats.errors += 1
            log.exception("ASR final lỗi")
            self._report_error(f"nhận dạng thất bại: {exc}")
            segs = []
        self.stats.asr_busy_ms += (time.perf_counter() - t0) * 1000
        # loại đoạn Whisper tự tin là không có tiếng nói
        segs = [s for s in segs if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(s.text for s in segs).strip()
        end_ms = offset_ms + len(audio) * 1000 // SR
        start_ms = offset_ms
        if segs:
            start_ms = max(offset_ms, min(s.start_ms for s in segs))
            end_ms = min(end_ms, max(s.end_ms for s in segs)) if max(s.end_ms for s in segs) > start_ms else end_ms
        ev = TranscriptEvent("final", self.committer.session_id, seg_id, start_ms, end_ms, text,
                             self.version + 1, boundary_ns=boundary_ns, asr_done_ns=self.clock_ns())
        committed = self.committer.commit(ev, speech_ratio) if text else None
        if committed is None:
            if self.version > 0:
                self.on_event(TranscriptEvent("discard", self.committer.session_id, seg_id, 0, 0, ""))
            return None
        self.stats.finals += 1
        self.on_event(committed)
        return committed

    def _report_error(self, message: str) -> None:
        if self.on_error and not self._reported_error:
            self._reported_error = True
            self.on_error(f"Lỗi ASR — {message}")

    def _cut_long(self) -> None:
        """Câu quá dài không có khoảng lặng: cắt tại khung 30ms năng lượng thấp nhất trong 3s cuối."""
        n = len(self.frame_energy)
        lo = max(1, n - int(3 * SR / FRAME))
        hi = n - 1
        if hi <= lo:
            cut_idx = n
        else:
            cut_idx = lo + int(np.argmin(self.frame_energy[lo:hi])) + 1
        audio = self._audio()
        cut = min(len(audio), cut_idx * FRAME)
        head, tail = audio[:cut], audio[cut:]
        start = self.buf_start
        speech_ratio = self.speech_samples / max(1, len(audio))
        tail_energy = self.frame_energy[cut_idx:]
        self.silence_run = 0
        self._transcribe_final(head, start, speech_ratio)
        # phần còn lại thành đầu câu mới (vẫn đang nói)
        self._reset(start + cut)
        if tail.size:
            self.buf = [tail]
            self.buf_len = len(tail)
            self.frame_energy = list(tail_energy) or [0.0]
            self.in_utt = True
            self.speech_samples = len(tail)

    def flush(self) -> TranscriptEvent | None:
        if self._rem.size:
            self.buf.append(self._rem)
            self.buf_len += len(self._rem)
            self.frame_energy.append(float(np.mean(self._rem * self._rem)))
            self._rem = np.zeros(0, np.float32)
        return self.finalize("flush")


class ASRWorker(threading.Thread):
    """Thread tiêu thụ hàng đợi AudioFrame từ AudioCaptureWorker."""

    def __init__(self, in_queue: "queue.Queue", engine: StreamingASR, max_lag_s: float = 2.0,
                 on_done: Callable[[], None] | None = None, on_error: Callable[[str], None] | None = None):
        super().__init__(name="ASRWorker", daemon=True)
        self.q = in_queue
        self.engine = engine
        self.max_lag_frames = int(max_lag_s * 10)  # frame 100 ms
        self.on_done = on_done
        self.on_error = on_error
        self._abort = threading.Event()
        self.ready = threading.Event()

    def abort(self) -> None:
        self._abort.set()

    def run(self) -> None:
        try:
            load = getattr(self.engine.tr, "load", None)
            if load:
                load()  # tải model trước khi audio đổ về
        except Exception as exc:
            log.exception("Không tải được model ASR")
            if self.on_error:
                self.on_error(f"Không tải được model ASR: {exc}")
        self.ready.set()
        try:
            while not self._abort.is_set():
                try:
                    item = self.q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is END:
                    self.engine.flush()
                    break
                if item is FLUSH:
                    self.engine.flush()
                    continue
                lagging = self.q.qsize() > self.max_lag_frames
                self.engine.allow_partials = not lagging
                if lagging:
                    self.engine.stats.skipped_partials += 1
                self.engine.feed(item)
        except Exception as exc:
            log.exception("ASRWorker lỗi")
            if self.on_error:
                self.on_error(str(exc))
        finally:
            if self.on_done:
                self.on_done()
