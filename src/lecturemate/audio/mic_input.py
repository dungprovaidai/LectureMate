"""Tiền xử lý mic yếu cho ASR, không sửa file WAV và không cần model mới."""
from __future__ import annotations

import numpy as np

from . import SAMPLE_RATE
from .resample import _lowpass_kernel

# Bỏ DC và tiếng ù tần số thấp. FIR nhân quả, trễ khoảng 10 ms, không đổi số mẫu.
_HIGHPASS = -_lowpass_kernel(100 / SAMPLE_RATE, taps=321)
_HIGHPASS[len(_HIGHPASS) // 2] += 1.0
_TARGET_RMS = 10 ** (-26 / 20)
_MAX_GAIN = 10 ** (24 / 20)
_PEAK_LIMIT = 0.95


class MicFilter:
    def __init__(self):
        self._history: np.ndarray | None = None

    def reset(self) -> None:
        self._history = None

    def process(self, samples: np.ndarray) -> np.ndarray:
        if not samples.size:
            return samples
        if self._history is None:
            self._history = np.full(len(_HIGHPASS) - 1, samples[0], dtype=np.float32)
        buf = np.concatenate((self._history, samples))
        self._history = buf[-(len(_HIGHPASS) - 1):].copy()
        return np.convolve(buf, _HIGHPASS, mode="valid")


def boost_quiet_speech(samples: np.ndarray) -> np.ndarray:
    """Chỉ dùng sau VAD: tăng đều cả cụm nói, có trần gain và giữ khoảng đệm chống vỡ tiếng."""
    peak = float(np.max(np.abs(samples), initial=0.0))
    if not peak:
        return samples
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    gain = min(max(1.0, _TARGET_RMS / rms), _MAX_GAIN, _PEAK_LIMIT / peak)
    return samples * gain if gain != 1.0 else samples
