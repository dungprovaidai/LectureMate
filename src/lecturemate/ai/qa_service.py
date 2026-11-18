"""Ask Your Lecture: câu hỏi → top-k đoạn (FTS5) → Agnes trả lời tiếng Việt kèm mốc.

Không có bằng chứng → trả "Không đủ dữ kiện" mà KHÔNG gọi API. Mốc trong câu trả lời phải
thuộc các đoạn đã đưa vào; mốc khác bị đánh dấu [mốc?].
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import format_ts
from .citations import cited, validate_citations
from .llm_provider import LLMError, LLMProvider
from .search_service import Hit, SearchService
from .translator import load_prompt

INSUFFICIENT = "Không đủ dữ kiện trong bài giảng để trả lời câu này."


@dataclass
class Answer:
    text: str
    evidence: list[Hit]
    citations: list[tuple[str, int]] = field(default_factory=list)  # (segment_id, start_ms) để click tua
    insufficient: bool = False
    invalid_citations: list[str] = field(default_factory=list)
    error: str | None = None


class QAService:
    def __init__(self, repo, provider: LLMProvider | None, top_k: int = 6, neighbor: int = 1):
        self.repo = repo
        self.search = SearchService(repo)
        self.provider = provider
        self.top_k = top_k
        self.neighbor = neighbor

    def _expand(self, hits: list[Hit], session_id: str) -> list[Hit]:
        """Thêm câu liền trước/sau mỗi hit để đủ ngữ cảnh, giữ thứ tự thời gian."""
        if not self.neighbor:
            return sorted(hits, key=lambda h: h.start_ms)
        segs = self.repo.list_segments(session_id)
        idx = {s.id: i for i, s in enumerate(segs)}
        keep = set()
        for h in hits:
            i = idx.get(h.segment_id)
            if i is None:
                continue
            for j in range(max(0, i - self.neighbor), min(len(segs), i + self.neighbor + 1)):
                keep.add(j)
        return [Hit(segs[j].id, segs[j].session_id, segs[j].start_ms, segs[j].end_ms, segs[j].en_text,
                    segs[j].vi_text or "", 0.0) for j in sorted(keep)]

    def ask(self, question: str, session_id: str) -> Answer:
        hits = self.search.search(question, session_id=session_id, limit=self.top_k)
        if not hits:
            return Answer(INSUFFICIENT, [], insufficient=True)
        ev = self._expand(hits, session_id)
        if self.provider is None:
            return Answer("Chưa cấu hình API — dưới đây là các đoạn liên quan nhất.", ev,
                          [(h.segment_id, h.start_ms) for h in hits], error="no_provider")
        evidence_txt = "\n".join(f"[{format_ts(h.start_ms)}] {h.en_text}" for h in ev)
        msgs = [{"role": "system", "content": load_prompt("qa.txt")},
                {"role": "user", "content": f"<bang_chung>\n{evidence_txt}\n</bang_chung>\n\nCâu hỏi: {question}"}]
        try:
            resp = self.provider.complete(msgs, max_tokens=600, timeout_s=30)
        except LLMError as e:
            return Answer(f"Không gọi được API ({e.code}). Các đoạn liên quan được liệt kê bên dưới.", ev,
                          [(h.segment_id, h.start_ms) for h in hits], error=e.code)
        raw = resp.text.strip()
        if "KHÔNG ĐỦ DỮ KIỆN" in raw.upper():
            return Answer(INSUFFICIENT, ev, insufficient=True)
        allowed = {format_ts(h.start_ms) for h in ev}
        text, bad = validate_citations(raw, allowed)
        by_ts = {format_ts(h.start_ms): h for h in ev}
        cits = []
        for ts in dict.fromkeys(cited(text)):
            h = by_ts.get(ts)
            if h:
                cits.append((h.segment_id, h.start_ms))
        return Answer(text, ev, cits, invalid_citations=bad)
