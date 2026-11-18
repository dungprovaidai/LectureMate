"""Bộ nhận dạng: giao diện chung, Faster-Whisper thật và FakeTranscriber cho test."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)
SR = 16_000


@dataclass
class ASRSegment:
    start_ms: int  # tuyệt đối trên timeline phiên
    end_ms: int
    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, offset_ms: int, *, final: bool,
                   prompt: str | None = None) -> list[ASRSegment]: ...


class FasterWhisperTranscriber:
    """Bọc faster_whisper.WhisperModel. Model được tải lười ở lần gọi đầu (có thể tải về
    từ Hugging Face lần đầu — cần mạng, hoặc đặt sẵn trong thư mục models/)."""

    def __init__(self, model_size: str = "base", device: str = "auto", compute_type: str = "int8",
                 language: str = "en", download_root: str | None = None, cpu_threads: int = 0,
                 final_beam_size: int = 2):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.download_root = download_root
        self.cpu_threads = cpu_threads
        self.final_beam = final_beam_size
        self.name = f"faster-whisper:{model_size}"
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                kw = dict(device=self.device, compute_type=self.compute_type)
                if self.download_root:
                    kw["download_root"] = self.download_root
                if self.cpu_threads:
                    kw["cpu_threads"] = self.cpu_threads
                try:
                    self._model = WhisperModel(self.model_size, **kw)
                except ValueError:
                    # compute_type không hỗ trợ trên thiết bị này → để CTranslate2 tự chọn
                    kw["compute_type"] = "default"
                    self._model = WhisperModel(self.model_size, **kw)
            return self._model

    def transcribe(self, audio: np.ndarray, offset_ms: int, *, final: bool,
                   prompt: str | None = None) -> list[ASRSegment]:
        model = self.load()
        segs, _info = model.transcribe(
            audio.astype(np.float32),
            language=self.language,
            beam_size=self.final_beam if final else 1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt if final else None,
            without_timestamps=not final,
            vad_filter=False,
        )
        out = []
        for s in segs:
            out.append(ASRSegment(offset_ms + int(s.start * 1000), offset_ms + int(s.end * 1000),
                                  s.text.strip(), float(s.no_speech_prob), float(s.avg_logprob)))
        return out


class FakeTranscriber:
    """Trả về các từ (start_ms, end_ms, word) nằm trong cửa sổ audio được đưa vào.

    Partial chỉ thấy các từ đã "nói xong" trong buffer → giống ASR streaming thật.
    """

    def __init__(self, words: list[tuple[int, int, str]], delay_s: float = 0.0):
        self.words = sorted(words)
        self.delay = delay_s
        self.name = "fake"
        self.calls = {"partial": 0, "final": 0}

    def transcribe(self, audio: np.ndarray, offset_ms: int, *, final: bool,
                   prompt: str | None = None) -> list[ASRSegment]:
        import time

        self.calls["final" if final else "partial"] += 1
        if self.delay:
            time.sleep(self.delay)
        end = offset_ms + len(audio) * 1000 // SR
        ws = [w for w in self.words if w[0] >= offset_ms - 50 and w[1] <= end + 50]
        if not ws:
            return []
        return [ASRSegment(ws[0][0], ws[-1][1], " ".join(w[2] for w in ws))]


def model_root():
    """Ưu tiên thư mục ``models`` cạnh LectureMate.exe (bản portable đóng gói sẵn model),
    sau đó ``%LOCALAPPDATA%/LectureMate/models`` (tải lần đầu)."""
    import sys
    from pathlib import Path

    from ..config import data_dir

    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / "models"
        if bundled.is_dir() and any(bundled.iterdir()):
            return bundled
    root = data_dir() / "models"
    root.mkdir(exist_ok=True)
    return root


def make_transcriber(cfg) -> Transcriber:
    root = model_root()
    return FasterWhisperTranscriber(cfg.asr_model, cfg.asr_device, cfg.asr_compute_type, cfg.language,
                                    download_root=str(root))
