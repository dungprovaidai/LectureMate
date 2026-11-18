"""Ghi WAV PCM16 mono 16 kHz an toàn khi crash.

Header được cập nhật định kỳ (mặc định mỗi 2 giây âm thanh) nên nếu tiến trình bị kill,
file vẫn phát được tới gần thời điểm cuối. ``repair_wav`` sửa header theo kích thước file.
Kích thước: 16000 × 2 byte × 3600 s = 115,2 MB/giờ.
"""
from __future__ import annotations

import os
import struct
import threading
from pathlib import Path

import numpy as np

HEADER_SIZE = 44
BYTES_PER_HOUR_16K_MONO = 16_000 * 2 * 3600


def _header(data_bytes: int, rate: int, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = rate * channels * bits // 8
    block = channels * bits // 8
    return (
        b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, block, bits)
        + b"data" + struct.pack("<I", data_bytes)
    )


def float_to_pcm16(x: np.ndarray) -> bytes:
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


class WavWriter:
    def __init__(self, path: Path | str, rate: int = 16_000, header_every_s: float = 2.0):
        self.path = Path(path)
        self.rate = rate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "wb")
        self._f.write(_header(0, rate))
        self._data = 0
        self._since_header = 0
        self._header_every = int(header_every_s * rate * 2)
        self._lock = threading.Lock()
        self.closed = False

    @property
    def samples_written(self) -> int:
        return self._data // 2

    @property
    def duration_ms(self) -> int:
        return self.samples_written * 1000 // self.rate

    def write(self, samples: np.ndarray) -> None:
        if self.closed:
            return
        b = float_to_pcm16(samples)
        with self._lock:
            self._f.write(b)
            self._data += len(b)
            self._since_header += len(b)
            if self._since_header >= self._header_every:
                self._update_header()
                self._since_header = 0

    def _update_header(self) -> None:
        pos = self._f.tell()
        self._f.seek(0)
        self._f.write(_header(self._data, self.rate))
        self._f.seek(pos)
        self._f.flush()

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self._update_header()
            try:
                os.fsync(self._f.fileno())
            except OSError:
                pass
            self._f.close()
            self.closed = True


def repair_wav(path: Path | str) -> bool:
    """Sửa header RIFF/data theo kích thước thật (dùng khi phục hồi phiên bị crash)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < HEADER_SIZE:
        return False
    size = path.stat().st_size
    data = size - HEADER_SIZE
    data -= data % 2
    with open(path, "r+b") as f:
        head = f.read(HEADER_SIZE)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return False
        rate = struct.unpack("<I", head[24:28])[0]
        f.seek(0)
        f.write(_header(data, rate))
    return True


def read_wav_mono(path: Path | str) -> tuple[np.ndarray, int]:
    """Đọc WAV PCM16/PCM32/float → float32 mono. Không cần thư viện ngoài."""
    import wave

    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"Không hỗ trợ sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, rate
