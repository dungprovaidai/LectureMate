"""Lightweight heuristic speaker labels from pitch and coarse spectral shape.

This deliberately avoids a heavyweight diarization model. It is useful for obvious
voice changes, but is not a substitute for embedding-based speaker diarization.
"""
from __future__ import annotations

import numpy as np

SR = 16_000
FRAME = 480  # 30 ms
HOP = 320


class SpeakerTracker:
    def __init__(self, threshold: float = 0.48, update_rate: float = 0.12):
        self.threshold = threshold
        self.update_rate = update_rate
        self._centroids: list[np.ndarray] = []
        self._last_id: str | None = None

    @property
    def current_id(self) -> str | None:
        return self._last_id

    def assign(self, audio: np.ndarray) -> str | None:
        feature = _voice_feature(audio)
        if feature is None:
            return self.current_id
        if not self._centroids:
            self._centroids.append(feature)
            self._last_id = "1"
            return "1"

        distances = [_distance(feature, center) for center in self._centroids]
        idx = int(np.argmin(distances))
        if distances[idx] > self.threshold:
            self._centroids.append(feature)
            self._last_id = str(len(self._centroids))
            return self._last_id

        center = self._centroids[idx]
        updated = center * (1.0 - self.update_rate) + feature * self.update_rate
        self._centroids[idx] = _normalize(updated)
        self._last_id = str(idx + 1)
        return self._last_id


def _voice_feature(audio: np.ndarray) -> np.ndarray | None:
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(x) < int(0.35 * SR):
        return None
    x = x - float(np.mean(x))
    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP]
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    active = frames[rms > max(float(np.max(rms)) * 0.12, 0.003)]
    if len(active) < 4:
        return None

    window = np.hanning(FRAME).astype(np.float32)
    spectra = np.abs(np.fft.rfft(active * window, n=1024))
    power = spectra * spectra
    freqs = np.fft.rfftfreq(1024, 1 / SR)
    bands = np.geomspace(100, 4_000, 13)
    profile = []
    for lo, hi in zip(bands[:-1], bands[1:]):
        mask = (freqs >= lo) & (freqs < hi)
        profile.append(float(np.mean(power[:, mask])) if np.any(mask) else 1e-12)
    profile = np.log(np.maximum(profile, 1e-12))
    profile -= np.mean(profile)

    # Estimate pitch using FFT autocorrelation, then express it in semitones.
    centered = active - np.mean(active, axis=1, keepdims=True)
    nfft = 1 << (2 * FRAME - 1).bit_length()
    spec = np.fft.rfft(centered, n=nfft, axis=1)
    corr = np.fft.irfft(spec * np.conjugate(spec), n=nfft, axis=1)[:, :FRAME]
    corr /= np.maximum(corr[:, :1], 1e-9)
    min_lag, max_lag = SR // 300, SR // 65
    pitch_corr = corr[:, min_lag:max_lag]
    lags = np.argmax(pitch_corr, axis=1) + min_lag
    strengths = np.max(pitch_corr, axis=1)
    pitches = SR / lags[strengths > 0.25]
    pitch = float(np.median(pitches)) if len(pitches) else 150.0

    # Pitch provides a strong cue; normalized coarse spectrum helps separate voices
    # with similar pitch. The scale is intentionally broad to avoid splitting every word.
    pitch_feature = np.array([np.log2(pitch / 150.0) * 1.8], dtype=np.float64)
    return _normalize(np.concatenate((pitch_feature, profile.astype(np.float64))))


def _normalize(x: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(x))
    return x / norm if norm > 1e-9 else x


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    # Cosine distance plus a small Euclidean term keeps the pitch cue meaningful.
    cosine = 1.0 - float(np.clip(np.dot(a, b), -1.0, 1.0))
    return cosine + 0.08 * float(np.linalg.norm(a - b))
