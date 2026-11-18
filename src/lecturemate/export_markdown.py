"""Xuất transcript (Markdown/JSON) và ghi chú (Markdown UTF-8, mốc là link tới đoạn)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .ai.citations import TS, _canon
from .domain.models import format_ts, language_name


def anchor(ms_or_ts) -> str:
    ts = format_ts(ms_or_ts) if isinstance(ms_or_ts, int) else _canon(ms_or_ts)
    return "t-" + ts.replace(":", "")


def transcript_markdown(session: dict, segments) -> str:
    lines = [f"# {session.get('title', 'Buổi học')}", ""]
    meta = []
    if session.get("course_name"):
        meta.append(f"Môn: {session['course_name']}")
    if session.get("started_at"):
        meta.append(f"Bắt đầu: {session['started_at']}")
    if session.get("duration_ms"):
        meta.append(f"Thời lượng: {format_ts(session['duration_ms'])}")
    if meta:
        lines += [" · ".join(meta), ""]
    lines += ["> Bản dịch do AI tạo, có thể cần sửa thuật ngữ.", ""]
    for s in segments:
        ts = format_ts(s.start_ms)
        vi = s.vi_text if s.vi_text else ("_(chưa dịch)_" if s.translation_status != "failed" else "_(dịch lỗi)_")
        flag = " ⚑" if s.flagged else ""
        speaker = f" · Người nói {s.speaker_id}" if getattr(s, "speaker_id", None) else ""
        language = language_name(getattr(s, "language", "und"))
        lines.append(f'<a id="{anchor(s.start_ms)}"></a>**[{ts}]**{flag} [{language}{speaker}] {s.en_text}  ')
        lines.append(f"↳ {vi}")
        lines.append("")
    return "\n".join(lines)


def transcript_json(session: dict, segments) -> str:
    data = {
        "session": {k: session.get(k) for k in ("id", "title", "course_name", "started_at", "ended_at", "duration_ms")},
        "segments": [
            {"id": s.id, "sequence": s.sequence_number, "start_ms": s.start_ms, "end_ms": s.end_ms,
             "en": s.en_text, "text": s.en_text, "language": getattr(s, "language", "und"),
              "language_probability": getattr(s, "language_probability", 0.0),
              "speaker_id": getattr(s, "speaker_id", None), "vi": s.vi_text,
             "translation_status": s.translation_status, "flagged": s.flagged}
            for s in segments
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def link_timestamps(md: str, valid: set[str]) -> str:
    """[00:05:32] → [00:05:32](#t-000532) nếu mốc tương ứng một đoạn có thật."""
    valid_c = {_canon(v) for v in valid}

    def repl(m: re.Match) -> str:
        c = _canon(m.group(1))
        if c in valid_c:
            return f"[{c}](#{anchor(c)})"
        return m.group(0)

    # không đụng tới link đã có sẵn
    return re.sub(TS.pattern + r"(?!\()", repl, md)


def notes_markdown(notes_md: str, session: dict, segments) -> str:
    segs = list(segments)
    valid = {format_ts(s.start_ms) for s in segs}
    body = link_timestamps(notes_md, valid)
    return body.rstrip() + "\n\n---\n\n## Phụ lục: transcript\n\n" + transcript_markdown(session, segs).split("\n", 2)[2]


def write_text(path: Path | str, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:80] or "lecture"
