"""Bộ chuyển tần số lấy mẫu có trạng thái (stream-safe), chỉ dùng numpy.

Lọc thông thấp FIR (windowed-sinc) trước khi giảm mẫu để tránh aliasing, giữ đuôi giữa
các chunk để không có tiếng "click" ở biên.
"""
from __future__ import annotations

import numpy as np


def to_mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    return x


def _lowpass_kernel(cutoff: float, taps: int = 63) -> np.ndarray:
    """cutoff: tỉ lệ so với tần số lấy mẫu đầu vào (0..0.5)."""
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n)
    h *= np.blackman(taps)
    return (h / h.sum()).astype(np.float32)


class Resampler:
    def __init__(self, src_rate: int, dst_rate: int = 16_000):
        self.src = int(src_rate)
        self.dst = int(dst_rate)
        self.passthrough = self.src == self.dst
        if not self.passthrough:
            cutoff = 0.5 * min(1.0, self.dst / self.src) * 0.9
            self.kernel = _lowpass_kernel(cutoff)
            self._hist = np.zeros(len(self.kernel) - 1, dtype=np.float32)
            self._pos = 0.0  # vị trí (theo mẫu đầu vào) của mẫu đầu ra kế tiếp
            self._consumed = 0  # số mẫu đầu vào đã qua lọc

    def process(self, x: np.ndarray) -> np.ndarray:
        x = to_mono(x)
        if self.passthrough or x.size == 0:
            return x.astype(np.float32, copy=False)
        buf = np.concatenate([self._hist, x])
        filt = np.convolve(buf, self.kernel, mode="valid")  # len == len(x)
        self._hist = buf[-(len(self.kernel) - 1):]
        step = self.src / self.dst
        start = self._consumed
        end = start + len(filt)
        positions = np.arange(self._pos, end - 1, step)
        out = np.interp(positions - start, np.arange(len(filt)), filt) if len(positions) else np.zeros(0, np.float32)
        self._pos = positions[-1] + step if len(positions) else self._pos
        self._consumed = end
        return out.astype(np.float32)
