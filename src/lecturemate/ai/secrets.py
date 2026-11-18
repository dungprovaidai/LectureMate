"""Lưu API key bằng Windows Credential Manager (qua ``keyring``). Không bao giờ lưu
vào SQLite/config/log. Biến môi trường ``LECTUREMATE_API_KEY`` chỉ dùng cho script đo."""
from __future__ import annotations

import logging
import os

SERVICE = "LectureMate"
USER = "agnes_api_key"
log = logging.getLogger(__name__)


def mask(secret: str | None) -> str:
    if not secret:
        return "(chưa có)"
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:3]}…{secret[-2:]}"


def get_api_key(quiet: bool = False) -> str | None:
    env = os.environ.get("LECTUREMATE_API_KEY")
    if env:
        return env.strip()
    try:
        import keyring

        return keyring.get_password(SERVICE, USER)
    except Exception as exc:  # backend không có (Linux CI) → coi như chưa có key
        if not quiet:
            log.warning("Không đọc được keyring: %s", exc.__class__.__name__)
        return None


def set_api_key(value: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE, USER, value.strip())
        return True
    except Exception as exc:
        log.error("Không lưu được key vào keyring: %s", exc.__class__.__name__)
        return False


def delete_api_key() -> bool:
    try:
        import keyring

        keyring.delete_password(SERVICE, USER)
        return True
    except Exception:
        return False


class RedactingFilter(logging.Filter):
    """Chặn mọi bản ghi log chứa API key hiện tại hoặc header Authorization."""

    def __init__(self, secret_getter=None, cache_s: float = 30.0):
        super().__init__()
        self._get = secret_getter or (lambda: get_api_key(quiet=True))  # quiet: không log trong filter
        self._cache_s = cache_s
        self._cached: tuple[float, str | None] = (-1e9, None)

    def _secret(self) -> str | None:
        import time

        t, val = self._cached
        now = time.monotonic()
        if now - t > self._cache_s:
            try:
                val = self._get()
            except Exception:
                val = None
            self._cached = (now, val)
        return val

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        secret = self._secret()
        if secret and secret in msg:
            record.msg = msg.replace(secret, "[REDACTED]")
            record.args = ()
        if "authorization" in msg.lower() and "bearer" in msg.lower():
            record.msg = "[REDACTED auth header]"
            record.args = ()
        return True
