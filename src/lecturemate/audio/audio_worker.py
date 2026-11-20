"""AudioCaptureWorker: thread riêng đọc nguồn âm thanh, ghi WAV, đo mức âm và đẩy frame
qua hàng đợi có giới hạn cho ASR. Không bao giờ chạy trên UI thread.

Mốc thời gian của frame = số mẫu đã GHI vào WAV (không tính lúc tạm dừng), nên timestamp
segment khớp đúng vị trí trong file WAV → tua audio chính xác.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import SAMPLE_RATE
from .sources import AudioSource, DeviceError, EndOfStream
from .wav_writer import WavWriter

log = logging.getLogger(__name__)


@dataclass
class AudioFrame:
    samples: np.ndarray  # float32 mono 16k
    start_sample: int  # vị trí trong timeline WAV của phiên
    captured_ns: int  # time.monotonic_ns() khi đọc xong


FLUSH = object()  # tín hiệu cho ASR: chốt câu đang dở (khi pause)
END = object()  # tín hiệu cho ASR: hết luồng


class RingBuffer:
    """Bộ đệm vòng cố định cho N giây gần nhất (dùng cho đo mức âm / test 5 giây)."""

    def __init__(self, seconds: float):
        self.buf = np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)
        self.pos = 0
        self.filled = 0

    def push(self, x: np.ndarray) -> None:
        n = len(x)
        if n >= len(self.buf):
            self.buf[:] = x[-len(self.buf):]
            self.pos, self.filled = 0, len(self.buf)
            return
        end = self.pos + n
        if end <= len(self.buf):
            self.buf[self.pos:end] = x
        else:
            k = len(self.buf) - self.pos
            self.buf[self.pos:] = x[:k]
            self.buf[:n - k] = x[k:]
        self.pos = end % len(self.buf)
        self.filled = min(len(self.buf), self.filled + n)

    def get(self) -> np.ndarray:
        if self.filled < len(self.buf):
            return self.buf[:self.filled].copy()
        return np.concatenate([self.buf[self.pos:], self.buf[:self.pos]])


def rms_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20 * np.log10(max(r, 1e-6))


class AudioCaptureWorker(threading.Thread):
    def __init__(
        self,
        source: AudioSource,
        out_queue: "queue.Queue | None",
        wav_writer: WavWriter | None = None,
        block_ms: int = 100,
        on_level: Callable[[float], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_end: Callable[[], None] | None = None,
        max_queue_drop_log: int = 5,
    ):
        super().__init__(name="AudioCapture", daemon=True)
        self.source = source
        self.q = out_queue
        self.wav = wav_writer
        self.block = SAMPLE_RATE * block_ms // 1000
        self.on_level = on_level
        self.on_error = on_error
        self.on_end = on_end
        self._stop_evt = threading.Event()
        self._paused = threading.Event()
        self._frame_lock = threading.RLock()
        self.opened = threading.Event()
        self.open_error: str | None = None
        self.samples_kept = 0
        self.dropped_frames = 0
        self.ring = RingBuffer(5.0)
        self._max_drop_log = max_queue_drop_log

    # ----------------------------------------------------------- control
    def pause(self) -> None:
        # Publish FLUSH only after the current frame has reached WAV and ASR.
        with self._frame_lock:
            if not self._paused.is_set():
                self._paused.set()
                self._put(FLUSH)

    def resume(self) -> None:
        with self._frame_lock:
            self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    @property
    def elapsed_ms(self) -> int:
        return self.samples_kept * 1000 // SAMPLE_RATE

    # -------------------------------------------------------------- loop
    def _put(self, item) -> None:
        if self.q is None:
            return
        try:
            self.q.put_nowait(item)
        except queue.Full:
            # ASR quá tải: bỏ frame cũ nhất để giữ realtime; WAV vẫn đầy đủ.
            try:
                self.q.get_nowait()
                self.dropped_frames += 1
                if self.dropped_frames <= self._max_drop_log:
                    log.warning("ASR queue đầy, bỏ 1 frame (tổng %d)", self.dropped_frames)
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(item)
            except queue.Full:
                pass

    def run(self) -> None:
        try:
            self.source.open()
        except Exception as exc:
            self.open_error = str(exc)
            self.opened.set()
            if self.on_error:
                self.on_error(self.open_error)
            self._finish()
            return
        self.opened.set()
        try:
            while not self._stop_evt.is_set():
                try:
                    x = self.source.read(self.block)
                except EndOfStream:
                    break
                except DeviceError as exc:
                    log.error("Lỗi thiết bị: %s", exc)
                    if self.on_error:
                        self.on_error(str(exc))
                    break
                now = time.monotonic_ns()
                if self.on_level:
                    self.on_level(rms_dbfs(x))
                with self._frame_lock:
                    if self._paused.is_set():
                        continue  # vẫn đọc để không tràn buffer thiết bị, nhưng bỏ dữ liệu
                    self.ring.push(x)
                    start = self.samples_kept
                    if self.wav:
                        try:
                            self.wav.write(x)
                        except OSError as exc:  # hết dung lượng...
                            log.error("Không ghi được WAV: %s", exc)
                            if self.on_error:
                                self.on_error(f"Không ghi được file âm thanh: {exc}")
                            self.wav = None
                    self.samples_kept += len(x)
                    self._put(AudioFrame(x, start, now))
        except Exception as exc:  # không để thread chết im lặng
            log.exception("AudioCaptureWorker lỗi")
            if self.on_error:
                self.on_error(str(exc))
        finally:
            self._finish()

    def _finish(self) -> None:
        try:
            self.source.close()
        except Exception:
            pass
        if self.wav:
            try:
                self.wav.close()
            except Exception:
                log.exception("Đóng WAV lỗi")
        if self.q is not None:
            # END phải tới được ASR kể cả khi queue đầy
            while True:
                try:
                    self.q.put(END, timeout=0.5)
                    break
                except queue.Full:
                    try:
                        self.q.get_nowait()
                    except queue.Empty:
                        pass
        if self.on_end:
            self.on_end()
