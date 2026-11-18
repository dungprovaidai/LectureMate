"""Thống kê độ trễ p50/p90 tách ASR / hàng đợi / API / UI và xuất báo cáo Markdown.

Không thay p50/p90 bằng token/s. Báo cáo ghi rõ số mẫu; < 100 mẫu thì cảnh báo chưa đủ.
"""
from __future__ import annotations

import math
from collections import defaultdict


def percentile(values: list[float], p: float) -> float | None:
    """Nội suy tuyến tính (giống numpy 'linear'). None nếu rỗng."""
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return None
    if len(vs) == 1:
        return float(vs[0])
    k = (len(vs) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return float(vs[int(k)])
    return vs[f] + (vs[c] - vs[f]) * (k - f)


PARTS = ("asr_ms", "queue_ms", "api_ms", "ui_ms", "total_ms")


def summarize(rows: list[dict]) -> dict:
    out = {}
    for part in PARTS:
        vals = [r[part] for r in rows if r.get(part) is not None]
        out[part] = {"n": len(vals), "p50": percentile(vals, 50), "p90": percentile(vals, 90),
                     "max": max(vals) if vals else None}
    return out


def summarize_by(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "?")].append(r)
    return {k: summarize(v) for k, v in groups.items()}


def context_bucket(chars: int | None) -> str:
    if chars is None:
        return "?"
    if chars < 1200:
        return "<1200 ký tự"
    if chars < 2000:
        return "1200–2000"
    return ">=2000"


def _fmt(v):
    return "—" if v is None else f"{v:,.0f}"


def markdown_report(rows: list[dict], title: str = "Độ trễ end-to-end") -> str:
    lines = [f"# {title}", ""]
    n_total = sum(1 for r in rows if r.get("total_ms") is not None)
    lines.append(f"Số segment có đo đầy đủ: **{n_total}**.")
    if n_total < 100:
        lines.append("> ⚠️ Chưa đủ 100 segment final thật — chưa dùng số liệu này để chốt cổng.")
    lines += ["", "| Thành phần | n | p50 (ms) | p90 (ms) | max (ms) |", "|---|---:|---:|---:|---:|"]
    s = summarize(rows)
    names = {"asr_ms": "Ranh giới câu → ASR final", "queue_ms": "Chờ hàng đợi dịch",
             "api_ms": "API (gửi → nhận đủ)", "ui_ms": "Hiển thị UI", "total_ms": "Tổng (ranh giới → bản dịch)"}
    for part in PARTS:
        d = s[part]
        lines.append(f"| {names[part]} | {d['n']} | {_fmt(d['p50'])} | {_fmt(d['p90'])} | {_fmt(d['max'])} |")
    for key, label in (("asr_model", "Theo model ASR"), ("context_bucket", "Theo dung lượng context")):
        rs = [dict(r, context_bucket=context_bucket(r.get("context_chars"))) for r in rows]
        groups = summarize_by(rs, key)
        lines += ["", f"## {label}", "", "| Nhóm | n | p50 tổng | p90 tổng | p50 API | p90 API |", "|---|---:|---:|---:|---:|---:|"]
        for g, d in sorted(groups.items()):
            lines.append(f"| {g} | {d['total_ms']['n']} | {_fmt(d['total_ms']['p50'])} | {_fmt(d['total_ms']['p90'])} "
                         f"| {_fmt(d['api_ms']['p50'])} | {_fmt(d['api_ms']['p90'])} |")
    return "\n".join(lines) + "\n"
