"""VAD năng lượng thích nghi (rẻ, chạy mỗi 30 ms). Silero VAD của faster-whisper có thể
thay thế sau khi benchmark nếu môi trường ồn."""
from __future__ import annotations

import numpy as np

FRAME = 480  # 30 ms @ 16 kHz


class EnergyVAD:
    def __init__(self, margin_db: float = 9.0, min_db: float = -48.0, floor_init_db: float = -60.0,
                 rise: float = 0.02, fall: float = 0.3):
        self.margin = margin_db
        self.min_db = min_db
        self.noise_db = floor_init_db
        self.rise = rise  # ồn nền tăng chậm (để giọng nói dài không bị coi là nền)
        self.fall = fall  # giảm nhanh khi im lặng hơn

    def is_speech(self, frame: np.ndarray) -> bool:
        r = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if frame.size else 0.0
        db = 20 * np.log10(max(r, 1e-6))
        speech = db > max(self.min_db, self.noise_db + self.margin)
        a = self.rise if db > self.noise_db else self.fall
        if not speech or db < self.noise_db:
            self.noise_db += a * (db - self.noise_db)
        return speech
