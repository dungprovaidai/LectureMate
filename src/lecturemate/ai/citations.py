"""Kiểm tra mốc [hh:mm:ss] do AI sinh ra: chỉ giữ mốc có thật trong nguồn đã đưa vào."""
from __future__ import annotations

import re

TS = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def _canon(ts: str) -> str:
    parts = ts.split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    return ":".join(f"{int(p):02d}" for p in parts)


def validate_citations(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Thay mốc không có trong ``allowed`` bằng ``[mốc?]``. Trả (text_mới, danh_sách_mốc_giả)."""
    allowed_c = {_canon(a) for a in allowed}
    bad: list[str] = []

    def repl(m: re.Match) -> str:
        c = _canon(m.group(1))
        if c in allowed_c:
            return f"[{c}]"
        bad.append(m.group(1))
        return "[mốc?]"

    return TS.sub(repl, text), bad


def cited(text: str) -> list[str]:
    return [_canon(m) for m in TS.findall(text)]
