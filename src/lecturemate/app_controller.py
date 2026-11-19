"""Nối các tầng: Audio → ASR → SegmentCommitter/SQLite → TranslationQueue → callback UI.

Không phụ thuộc Qt: mọi callback được gọi từ thread worker; tầng UI (``ui/bridge.py``)
chuyển chúng về UI thread bằng signal. Nhờ vậy có thể chạy end-to-end headless (test,
benchmark, phiên âm sau buổi).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .ai.glossary import Glossary
from .ai.translation_queue import TranslationQueue
from .asr.asr_worker import ASRWorker, StreamingASR
from .asr.segment_committer import SegmentCommitter
from .asr.transcriber import Transcriber
from .audio.audio_worker import AudioCaptureWorker, AudioFrame
from .audio.sources import AudioSource, EndOfStream, FileAudioSource
from .audio.wav_writer import WavWriter, repair_wav
from .config import AppConfig
from .domain.models import SessionState, TranscriptEvent, TranslationResult, is_vietnamese
from .persistence.repository import SessionRepository
from .session_controller import SessionController

log = logging.getLogger(__name__)


@dataclass
class Callbacks:
    on_transcript: Callable[[TranscriptEvent], None] = lambda e: None
    on_translation: Callable[[str, TranslationResult], None] = lambda sid, r: None
    on_level: Callable[[float], None] = lambda db: None
    on_state: Callable[[SessionState, SessionState], None] = lambda a, b: None
    on_error: Callable[[str], None] = lambda msg: None
    on_api_status: Callable[[str], None] = lambda s: None
    on_info: Callable[[str], None] = lambda msg: None


class AppServices:
    """Dịch vụ dùng chung toàn ứng dụng (một DB, một hàng đợi dịch)."""

    def __init__(self, cfg: AppConfig, repo: SessionRepository, provider_getter, callbacks: Callbacks | None = None):
        self.cfg = cfg
        self.repo = repo
        self.cb = callbacks or Callbacks()
        self.provider_getter = provider_getter
        self._gloss_cache: dict[str, Glossary] = {}
        self.tq = TranslationQueue(
            repo,
            self._provider_if_enabled,
            self.glossary_for_session,
            on_result=lambda sid, r: self.cb.on_translation(sid, r),
            on_status=lambda s: self.cb.on_api_status(s),
            max_in_flight=cfg.api_max_in_flight,
            max_retries=cfg.api_max_retries,
            timeout_s=cfg.api_timeout_s,
            max_tokens=cfg.max_output_tokens,
            context_sentences=cfg.context_sentences,
            price_in=cfg.price_in_per_mtok,
            price_out=cfg.price_out_per_mtok,
            monthly_budget_usd=cfg.monthly_budget_usd,
            asr_model=cfg.asr_model,
        )
        self.tq.start()

    def _provider_if_enabled(self):
        if not self.cfg.translation_enabled:
            return None
        return self.provider_getter()

    def glossary_for_session(self, session_id: str) -> Glossary:
        if session_id not in self._gloss_cache:
            s = self.repo.get_session(session_id)
            self._gloss_cache[session_id] = Glossary.from_json(self.repo.get_glossary(s and s.get("course_id")))
        return self._gloss_cache[session_id]

    def invalidate_glossary(self) -> None:
        self._gloss_cache.clear()

    def startup_maintenance(self) -> list[dict]:
        """Dọn file chờ xóa, đưa in_flight về pending; trả danh sách phiên ghi dở cần phục hồi."""
        self.repo.retry_pending_cleanup()
        self.repo.reset_in_flight()
        return SessionController.find_interrupted(self.repo)

    def recover_session(self, session_id: str) -> None:
        s = self.repo.get_session(session_id)
        if s and s.get("audio_path"):
            repair_wav(s["audio_path"])
        SessionController.recover(self.repo, session_id)

    def shutdown(self) -> None:
        self.tq.stop(drain_s=3.0)


class LiveSession:
    """Một buổi ghi. ``start`` → (``pause``/``resume``)* → ``stop``."""

    def __init__(self, services: AppServices, source: AudioSource, transcriber: Transcriber | None,
                 audio_root: Path, record_only: bool | None = None):
        self.sv = services
        self.cfg = services.cfg
        self.repo = services.repo
        self.source = source
        self.tr = transcriber
        self.audio_root = Path(audio_root)
        self.record_only = (self.cfg.asr_mode == "record_only") if record_only is None else record_only
        if self.tr is None:
            self.record_only = True
        self.sc = SessionController(self.repo, elapsed_fn=self.elapsed_ms)
        self.sc.on_change(lambda a, b: self.sv.cb.on_state(a, b))
        self.audio: AudioCaptureWorker | None = None
        self.asr: ASRWorker | None = None
        self.engine: StreamingASR | None = None
        self._stopping = threading.Lock()
        self._stopped = threading.Event()
        self._preview_state: dict[str, dict] = {}
        self.device_error: str | None = None

    @property
    def session_id(self) -> str | None:
        return self.sc.session_id

    @property
    def state(self) -> SessionState:
        return self.sc.state

    def elapsed_ms(self) -> int:
        return self.audio.elapsed_ms if self.audio else 0

    # ----------------------------------------------------------------- start
    def start(self, title: str, course_id: str | None = None, open_timeout_s: float = 8.0) -> str:
        sid = self.sc.begin(title, self.cfg.source_type, course_id, self.cfg.asr_model)
        wav = None
        if self.cfg.save_audio:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.audio_root / f"{stamp}_{sid[:8]}.wav"
            wav = WavWriter(path)
            self.repo.set_audio_path(sid, str(path))
        translate = self.cfg.translation_enabled and not self.record_only
        aq: queue.Queue | None = None
        if not self.record_only:
            aq = queue.Queue(maxsize=300)  # ~30 giây audio
            committer = SegmentCommitter(self.repo, sid, translate=translate)
            if self.cfg.translation_mode == "fast":
                partial_interval_s = min(self.cfg.partial_interval_s, 0.5)
                silence_to_final_s = min(self.cfg.silence_to_final_s, 0.4)
                max_segment_s = min(self.cfg.max_segment_s, 8.0)
            else:
                partial_interval_s = self.cfg.partial_interval_s
                silence_to_final_s = self.cfg.silence_to_final_s
                max_segment_s = self.cfg.max_segment_s
            self.engine = StreamingASR(
                self.tr, committer, self._on_transcript,
                partial_interval_s=partial_interval_s,
                silence_to_final_s=silence_to_final_s,
                max_segment_s=max_segment_s,
                use_prompt=False,
                on_error=self.sv.cb.on_error,
                mic_assist=self.cfg.mic_assist and self.cfg.source_type == "microphone",
            )
            self.asr = ASRWorker(aq, self.engine, on_error=self.sv.cb.on_error)
            self.asr.start()
        self.sv.tq.active_session = sid
        self.audio = AudioCaptureWorker(self.source, aq, wav, on_level=self.sv.cb.on_level,
                                        on_error=self._on_device_error, on_end=self._on_audio_end)
        self.audio.start()
        if not self.audio.opened.wait(open_timeout_s) or self.audio.open_error:
            msg = self.audio.open_error or "Thiết bị không phản hồi"
            self.sc.device_failed()
            self._teardown_after_failure()
            raise RuntimeError(msg)
        self.sc.device_ok()
        return sid

    def _teardown_after_failure(self) -> None:
        if self.asr:
            self.asr.abort()
        try:
            self.sc.stop()
            self.sc.complete()
        except Exception:
            pass

    # --------------------------------------------------------------- events
    def _on_transcript(self, ev: TranscriptEvent) -> None:
        # Publish text before submitting translations, so even an instant provider
        # cannot return a translation for a row that has not been created yet.
        self.sv.cb.on_transcript(ev)
        if ev.is_final or ev.type == "discard":
            self.sv.tq.cancel_previews(ev.segment_id)
            self._preview_state.pop(ev.segment_id, None)
        if ev.is_final and self.cfg.translation_enabled and not self.record_only and not is_vietnamese(ev.language):
            self.sv.tq.submit(ev.segment_id, ev.session_id, ev.boundary_ns, ev.asr_done_ns)
        elif (ev.type == "partial" and self.cfg.translation_enabled and not self.record_only
              and self.cfg.translation_mode == "fast" and not is_vietnamese(ev.language)):
            words = ev.text_en.split()
            now = time.monotonic()
            state = self._preview_state.setdefault(
                ev.segment_id, {"words": 0, "text": "", "sent_at": 0.0}
            )
            # Translate the full current hypothesis rather than joining translations
            # of isolated words. Whisper can correct earlier words as it hears more.
            changed = ev.text_en != state["text"]
            enough_words = len(words) >= state["words"] + 2
            correction = bool(state["text"]) and now - state["sent_at"] >= 1.0
            language_reliable = ev.language not in ("", "und") and ev.language_probability >= 0.55
            if changed and language_reliable and (enough_words or correction):
                queued = self.sv.tq.submit_preview(ev.segment_id, ev.session_id, ev.text_en, ev.language,
                                                  ev.version)
                if queued:
                    state.update(words=len(words), text=ev.text_en, sent_at=now)

    def _on_device_error(self, msg: str) -> None:
        self.device_error = msg
        self.sv.cb.on_error(f"{msg}. Đã dừng an toàn; phần đã ghi được giữ lại.")

    def _on_audio_end(self) -> None:
        # Nguồn kết thúc ngoài ý muốn (mất thiết bị / hết file) → dừng phiên an toàn ở thread khác
        if self.sc.state in (SessionState.RECORDING, SessionState.PAUSED) and not self._stopping.locked():
            threading.Thread(target=self.stop, name="AutoStop", daemon=True).start()

    # ------------------------------------------------------------- controls
    def pause(self) -> None:
        if self.sc.state == SessionState.RECORDING and self.audio:
            self.audio.pause()
            self.sc.pause()

    def resume(self) -> None:
        if self.sc.state == SessionState.PAUSED and self.audio:
            self.audio.resume()
            self.sc.resume()

    def checkpoint(self) -> None:
        if self.session_id and self.sc.state in (SessionState.RECORDING, SessionState.PAUSED):
            self.repo.checkpoint(self.session_id, self.elapsed_ms())

    def stop(self, asr_timeout_s: float = 30.0, drain_s: float = 4.0) -> None:
        if not self._stopping.acquire(blocking=False):
            self._stopped.wait(asr_timeout_s + drain_s + 5)
            return
        try:
            if self.sc.state not in (SessionState.RECORDING, SessionState.PAUSED, SessionState.ERROR):
                return
            self.sc.stop()
            if self.audio:
                self.audio.stop()  # đóng WAV, gửi END cho ASR
            if self.asr:
                self.asr.join(asr_timeout_s)  # ASR chốt câu cuối
                if self.asr.is_alive():
                    self.asr.abort()
                    self.sv.cb.on_info("ASR chưa xử lý hết; phần âm thanh còn lại vẫn có trong file WAV.")
            # chờ ngắn các bản dịch đang chạy; phần còn lại giữ pending để dịch sau
            deadline = time.monotonic() + drain_s
            while self.sv.tq.backlog() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.repo.update_status(self.session_id, SessionState.STOPPING, self.elapsed_ms())
            self.sc.complete()
            self.repo.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            self._stopped.set()
            self._stopping.release()


def transcribe_file_job(services: AppServices, session_id: str, transcriber: Transcriber,
                        on_progress: Callable[[float], None] | None = None,
                        on_event: Callable[[TranscriptEvent], None] | None = None,
                        cancel: threading.Event | None = None) -> int:
    """Phương án dự phòng (P1.3.B): phiên âm WAV sau buổi, rồi xếp hàng dịch.

    Idempotent: segment đã có (cùng session + sequence) không bị ghi đè; chạy lại sau khi bị
    dừng sẽ chỉ thêm phần còn thiếu (bỏ qua audio trước ``end_ms`` của segment cuối).
    """
    repo = services.repo
    s = repo.get_session(session_id)
    if not s or not s.get("audio_path") or not Path(s["audio_path"]).exists():
        raise FileNotFoundError("Buổi học không có file âm thanh")
    repair_wav(s["audio_path"])
    existing = repo.list_segments(session_id)
    resume_ms = existing[-1].end_ms if existing else 0
    committer = SegmentCommitter(repo, session_id, translate=services.cfg.translation_enabled)
    if existing:
        committer.prev_text, committer.prev_end = existing[-1].en_text, existing[-1].end_ms
    count = [0]

    def ev_cb(ev: TranscriptEvent) -> None:
        if ev.is_final:
            count[0] += 1
        if on_event:
            on_event(ev)

    eng = StreamingASR(transcriber, committer, ev_cb, partial_interval_s=1e9,
                       silence_to_final_s=services.cfg.silence_to_final_s,
                       max_segment_s=services.cfg.max_segment_s, use_prompt=False,
                       on_error=services.cb.on_error,
                       mic_assist=services.cfg.mic_assist and s["source_type"] == "microphone")
    eng.allow_partials = False
    src = FileAudioSource(s["audio_path"])
    src.open()
    total = max(1, src.total_samples)
    pos = 0
    block = 1600
    skip_to = resume_ms * 16
    while True:
        if cancel and cancel.is_set():
            break
        try:
            x = src.read(block)
        except EndOfStream:
            break
        if pos + block > skip_to:
            eng.feed(AudioFrame(x, pos, time.monotonic_ns()))
        pos += block
        if on_progress and pos % (block * 50) == 0:
            on_progress(min(1.0, pos / total))
    eng.flush()
    if on_progress:
        on_progress(1.0)
    if services.cfg.translation_enabled:
        services.tq.retry_pending(session_id, include_skipped=True)
    return count[0]


def ensure_course(repo: SessionRepository, name: str | None) -> str | None:
    name = (name or "").strip()
    return repo.upsert_course(name) if name else None
