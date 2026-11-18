"""Cấu hình ứng dụng và đường dẫn dữ liệu.

- Dữ liệu ở ``%LOCALAPPDATA%/LectureMate`` (Windows) hoặc ``~/.local/share/LectureMate``.
- Có thể ghi đè bằng biến môi trường ``LECTUREMATE_HOME`` (dùng cho test/portable).
- API key KHÔNG nằm trong file cấu hình: xem ``lecturemate.ai.secrets``.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_NAME


def data_dir() -> Path:
    override = os.environ.get("LECTUREMATE_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def audio_dir() -> Path:
    p = data_dir() / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "lecturemate.db"


def config_path() -> Path:
    return data_dir() / "config.json"


@dataclass
class AppConfig:
    # --- Âm thanh ---
    source_type: str = "microphone"  # "microphone" | "loopback"
    device_id: str = ""  # rỗng = thiết bị mặc định
    save_audio: bool = True

    # --- ASR ---
    asr_model: str = "base"  # tiny | base | small (chốt sau benchmark P1.2.A.1)
    asr_device: str = "auto"  # auto | cpu | cuda
    asr_compute_type: str = "int8"
    language: str = "en"
    partial_interval_s: float = 1.0
    max_segment_s: float = 12.0
    silence_to_final_s: float = 0.6
    asr_mode: str = "realtime"  # "realtime" | "record_only" (fallback P1.3.B)

    # --- AI / Agnes ---
    translation_enabled: bool = True
    # Theo catalog AgnesAI-Labs (OpenAI-compatible). PHẢI xác minh lại bằng tài khoản thật.
    api_base_url: str = "https://apihub.agnes-ai.com/v1"
    api_model: str = "agnes-3.0-flash"
    api_timeout_s: float = 10.0
    api_max_retries: int = 2
    api_max_in_flight: int = 2
    api_stream: bool = True
    api_extra_body: dict = field(default_factory=dict)  # vd. tắt suy luận sâu nếu provider hỗ trợ
    max_output_tokens: int = 256
    context_sentences: int = 2
    price_in_per_mtok: float = 0.0  # USD / 1M token input — người dùng nhập giá thật
    price_out_per_mtok: float = 0.0
    monthly_budget_usd: float = 5.0

    # --- UI ---
    theme: str = "dark"  # dark | light
    subtitle_font_px: int = 18
    auto_scroll: bool = True
    privacy_accepted: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = path or config_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
