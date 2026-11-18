"""Tiện ích chung cho các script đo (chạy từ thư mục gốc dự án)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_env(path: Path | None = None) -> None:
    """Đọc file .env đơn giản (KEY=VALUE). Không in giá trị ra màn hình."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_settings():
    """(base_url, model, key) từ biến môi trường / .env; key có thể lấy từ keyring."""
    load_env()
    from lecturemate.ai.secrets import get_api_key
    from lecturemate.config import AppConfig

    cfg = AppConfig.load()
    base = os.environ.get("AGNES_BASE_URL") or cfg.api_base_url
    model = os.environ.get("AGNES_MODEL") or cfg.api_model
    key = os.environ.get("AGNES_API_KEY") or get_api_key()
    return base, model, key, cfg
