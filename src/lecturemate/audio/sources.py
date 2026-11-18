"""Nguồn âm thanh. Tất cả trả float32 mono 16 kHz.

- ``SoundCardSource``: micro hoặc WASAPI loopback (âm thanh máy) qua thư viện SoundCard.
  Recorder phải được mở và đọc trên CÙNG một thread (yêu cầu COM của Windows), nên
  ``AudioCaptureWorker`` gọi ``open/read/close`` trong thread riêng của nó.
- ``FileAudioSource``: phát lại file WAV (kiểm thử, benchmark, phiên âm sau buổi).
- ``FakeAudioSource``: tín hiệu tổng hợp cho unit test.
"""
from __future__ import annotations

import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from . import SAMPLE_RATE
from .resample import Resampler, to_mono
from .wav_writer import read_wav_mono


class DeviceError(RuntimeError):
    """Thiết bị mất / không có quyền / không mở được."""


class EndOfStream(Exception):
    pass


class AudioSource(Protocol):
    name: str

    def open(self) -> None: ...
    def read(self, frames: int) -> np.ndarray: ...  # float32 mono 16 kHz, len == frames
    def close(self) -> None: ...


@dataclass
class DeviceInfo:
    id: str
    name: str
    kind: str  # "microphone" | "loopback"
    is_default: bool = False


def list_devices() -> list[DeviceInfo]:
    """Liệt kê micro và thiết bị loopback (loa). Trả [] nếu SoundCard không khả dụng."""
    try:
        import soundcard as sc
    except Exception:
        return []
    out: list[DeviceInfo] = []
    try:
        default_mic = sc.default_microphone()
    except Exception:
        default_mic = None
    try:
        default_spk = sc.default_speaker()
    except Exception:
        default_spk = None
    try:
        for m in sc.all_microphones(include_loopback=True):
            loop = bool(getattr(m, "isloopback", False))
            if loop:
                is_def = default_spk is not None and m.name == default_spk.name
            else:
                is_def = default_mic is not None and m.id == default_mic.id
            out.append(DeviceInfo(str(m.id), m.name, "loopback" if loop else "microphone", is_def))
    except Exception as exc:  # backend lỗi
        raise DeviceError(f"Không liệt kê được thiết bị: {exc}") from exc
    return out


def _com_init_this_thread() -> None:
    """Windows: thread worker cần tự khởi tạo COM (MTA) trước khi dùng WASAPI.
    UI thread của Qt là STA nên không thể dựa vào lần khởi tạo của SoundCard lúc import."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED; bỏ qua nếu đã init
    except Exception:
        pass


class SoundCardSource:
    def __init__(self, source_type: str = "microphone", device_id: str = "",
                 capture_rate: int = 48_000, block_ms: int = 100):
        if source_type not in ("microphone", "loopback"):
            raise ValueError(source_type)
        self.source_type = source_type
        self.device_id = device_id
        self.capture_rate = capture_rate
        self.block = capture_rate * block_ms // 1000
        self.name = ""
        self._ctx = None
        self._rec = None
        self._resampler = Resampler(capture_rate, SAMPLE_RATE)
        self._pending = np.zeros(0, dtype=np.float32)

    def _pick(self):
        import soundcard as sc

        if self.source_type == "loopback":
            if self.device_id:
                return sc.get_microphone(self.device_id, include_loopback=True)
            spk = sc.default_speaker()
            return sc.get_microphone(str(spk.name), include_loopback=True)
        if self.device_id:
            return sc.get_microphone(self.device_id)
        return sc.default_microphone()

    def open(self) -> None:
        _com_init_this_thread()
        try:
            import soundcard as sc  # noqa: F401
        except Exception as exc:
            raise DeviceError("Chưa cài thư viện SoundCard (pip install soundcard)") from exc
        try:
            dev = self._pick()
            self.name = dev.name
            self._ctx = dev.recorder(samplerate=self.capture_rate, blocksize=self.block)
            self._rec = self._ctx.__enter__()
        except Exception as exc:
            raise DeviceError(f"Không mở được thiết bị: {exc}") from exc

    def read(self, frames: int) -> np.ndarray:
        if self._rec is None:
            raise DeviceError("Thiết bị chưa mở")
        while self._pending.size < frames:
            need_src = int(np.ceil((frames - self._pending.size) * self.capture_rate / SAMPLE_RATE)) + 1
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # SoundcardRuntimeWarning: data discontinuity
                    data = self._rec.record(numframes=max(need_src, self.block))
            except Exception as exc:
                raise DeviceError(f"Mất thiết bị âm thanh: {exc}") from exc
            self._pending = np.concatenate([self._pending, self._resampler.process(to_mono(data))])
        out, self._pending = self._pending[:frames], self._pending[frames:]
        return out

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass
        self._ctx = self._rec = None


class FileAudioSource:
    """Đọc WAV như một luồng. ``realtime=True`` để mô phỏng tốc độ nói thật."""

    def __init__(self, path: Path | str, realtime: bool = False, speed: float = 1.0, loop: bool = False):
        self.path = Path(path)
        self.realtime = realtime
        self.speed = speed
        self.loop = loop
        self.name = self.path.name
        self._x = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._t0 = 0.0

    def open(self) -> None:
        if not self.path.exists():
            raise DeviceError(f"Không thấy file {self.path}")
        x, rate = read_wav_mono(self.path)
        self._x = Resampler(rate, SAMPLE_RATE).process(x) if rate != SAMPLE_RATE else x
        self._pos = 0
        self._t0 = time.monotonic()

    @property
    def total_samples(self) -> int:
        return len(self._x)

    def read(self, frames: int) -> np.ndarray:
        if self._pos >= len(self._x):
            if self.loop and len(self._x):
                self._pos = 0
            else:
                raise EndOfStream()
        out = self._x[self._pos:self._pos + frames]
        self._pos += len(out)
        if len(out) < frames:
            out = np.concatenate([out, np.zeros(frames - len(out), np.float32)])
        if self.realtime:
            due = self._t0 + (self._pos / SAMPLE_RATE) / self.speed
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return out

    def close(self) -> None:
        pass


class FakeAudioSource:
    """Phát tín hiệu từ danh sách (duration_s, amplitude). amplitude 0 = im lặng."""

    def __init__(self, pattern: list[tuple[float, float]], realtime: bool = False,
                 fail_after_s: float | None = None, freq: float = 220.0):
        self.name = "Fake"
        rng = np.random.default_rng(0)
        parts = []
        for dur, amp in pattern:
            n = int(dur * SAMPLE_RATE)
            t = np.arange(n) / SAMPLE_RATE
            sig = amp * np.sin(2 * np.pi * freq * t) if amp > 0 else np.zeros(n)
            sig = sig + rng.normal(0, 0.0005, n)
            parts.append(sig.astype(np.float32))
        self._x = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        self._pos = 0
        self.realtime = realtime
        self.fail_after = int(fail_after_s * SAMPLE_RATE) if fail_after_s is not None else None
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True
        self._t0 = time.monotonic()

    def read(self, frames: int) -> np.ndarray:
        if self.fail_after is not None and self._pos >= self.fail_after:
            raise DeviceError("Thiết bị bị rút (mô phỏng)")
        if self._pos >= len(self._x):
            raise EndOfStream()
        out = self._x[self._pos:self._pos + frames]
        self._pos += len(out)
        if len(out) < frames:
            out = np.concatenate([out, np.zeros(frames - len(out), np.float32)])
        if self.realtime:
            delay = self._t0 + self._pos / SAMPLE_RATE - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return out

    def close(self) -> None:
        self.closed = True


def make_source(source_type: str, device_id: str = "") -> AudioSource:
    return SoundCardSource(source_type, device_id)
