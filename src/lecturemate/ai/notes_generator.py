"""AI Notes có dẫn nguồn: map (từng chunk) → reduce (gộp). Mốc giả bị loại.

Kết quả từng chunk được lưu tạm (cache JSON) nên nếu một chunk lỗi token/mạng, lần chạy lại
chỉ gọi lại chunk đó — không ghi đè/tạo lại toàn bộ.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.models import format_ts
from .citations import validate_citations
from .llm_provider import LLMError, LLMProvider
from .notes_chunker import chunk_segments
from .translator import load_prompt

log = logging.getLogger(__name__)


@dataclass
class NotesResult:
    markdown: str
    model: str
    segment_ids: list[str]
    invalid_citations: list[str] = field(default_factory=list)
    failed_chunks: list[int] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failed_chunks


class NotesGenerator:
    def __init__(self, provider: LLMProvider, cache_dir: Path | None = None, max_chars: int = 6000,
                 overlap: int = 2, max_tokens: int = 1500, timeout_s: float = 60.0):
        self.provider = provider
        self.cache_dir = cache_dir
        self.max_chars = max_chars
        self.overlap = overlap
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s

    def _cache_file(self, session_id: str) -> Path | None:
        if not self.cache_dir:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"{session_id}.json"

    def generate(self, session_id: str, segments, title: str = "") -> NotesResult:
        segs = list(segments)
        if not segs:
            return NotesResult("_Chưa có transcript để tạo ghi chú._", "", [])
        chunks = chunk_segments(segs, self.max_chars, self.overlap)
        cache_path = self._cache_file(session_id)
        cache: dict = {}
        if cache_path and cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cache = {}
        system = load_prompt("notes.txt")
        parts: list[str] = []
        failed: list[int] = []
        bad_all: list[str] = []
        model = getattr(self.provider, "model", "")
        for ch in chunks:
            key = hashlib.sha1(ch.text.encode("utf-8")).hexdigest()
            if key in cache:
                parts.append(cache[key])
                continue
            msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": f"Bài giảng: {title}\nPhần {ch.index + 1}/{len(chunks)}\n\n{ch.text}"}]
            try:
                resp = self.provider.complete(msgs, max_tokens=self.max_tokens, timeout_s=self.timeout_s)
            except LLMError as e:
                log.warning("Chunk %d lỗi: %s", ch.index, e.code)
                failed.append(ch.index)
                continue
            model = resp.model or model
            text, bad = validate_citations(resp.text.strip(), ch.timestamps)
            bad_all += bad
            cache[key] = text
            parts.append(text)
            if cache_path:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        allowed = {format_ts(s.start_ms) for s in segs}
        if len(parts) > 1 and not failed:
            msgs = [{"role": "system", "content": load_prompt("notes_merge.txt")},
                    {"role": "user", "content": "\n\n---\n\n".join(parts)}]
            try:
                resp = self.provider.complete(msgs, max_tokens=self.max_tokens * 2, timeout_s=self.timeout_s)
                body, bad = validate_citations(resp.text.strip(), allowed)
                bad_all += bad
            except LLMError:
                body = "\n\n".join(parts)  # gộp thô vẫn hơn mất kết quả
        else:
            body = "\n\n".join(parts)
        header = f"# Ghi chú: {title}\n\n" if title else ""
        if failed:
            body += (f"\n\n> ⚠️ {len(failed)} phần chưa tạo được ghi chú (lỗi API). "
                     "Bấm “Tạo lại” để chỉ chạy lại các phần đó.")
        if bad_all:
            body += f"\n\n> ⚠️ Đã loại {len(bad_all)} mốc thời gian không có trong transcript (hiển thị [mốc?])."
        if cache_path and not failed and cache_path.exists():
            cache_path.unlink()
        return NotesResult(header + body, model, [s.id for s in segs], bad_all, failed)
