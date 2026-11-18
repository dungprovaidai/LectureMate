"""Tìm kiếm full-text (SQLite FTS5) theo segment, lọc theo phiên/môn học."""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[\w%]+", re.UNICODE)
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "and", "or", "what", "why",
    "how", "does", "do", "did", "which", "who", "when", "that", "this", "it", "be", "for", "with",
    "là", "gì", "có", "không", "và", "của", "thế", "nào", "tại", "sao", "những", "các", "một", "được",
    "trong", "cho", "về", "như", "khi", "thì", "nói", "bài", "giảng", "thầy", "cô",
}


@dataclass
class Hit:
    segment_id: str
    session_id: str
    start_ms: int
    end_ms: int
    en_text: str
    vi_text: str
    score: float


def build_query(text: str, mode: str = "any") -> str | None:
    """Biến chuỗi người dùng thành truy vấn FTS5 an toàn (mỗi token được quote)."""
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]
    if not toks:
        toks = [t for t in _TOKEN.findall(text.lower()) if len(t) > 1]
    if not toks:
        return None
    quoted = [f'"{t}"' for t in dict.fromkeys(toks)]
    if mode == "phrase":
        return '"' + " ".join(toks) + '"'
    return (" AND " if mode == "all" else " OR ").join(quoted)


class SearchService:
    def __init__(self, repo):
        self.repo = repo

    def search(self, text: str, session_id: str | None = None, course_id: str | None = None,
               limit: int = 8, mode: str = "any") -> list[Hit]:
        q = build_query(text, mode)
        if not q:
            return []
        sql = """SELECT f.segment_id, f.session_id, g.start_ms, g.end_ms, g.en_text,
                        coalesce(t.vi_text, '') AS vi_text, bm25(segment_fts) AS score
                 FROM segment_fts f
                 JOIN segments g ON g.id = f.segment_id
                 JOIN sessions s ON s.id = g.session_id
                 LEFT JOIN translations t ON t.segment_id = g.id
                 WHERE segment_fts MATCH ?"""
        params: list = [q]
        if session_id:
            sql += " AND f.session_id = ?"
            params.append(session_id)
        if course_id:
            sql += " AND s.course_id = ?"
            params.append(course_id)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        rows = self.repo._q(sql, tuple(params))
        return [Hit(r["segment_id"], r["session_id"], r["start_ms"], r["end_ms"], r["en_text"],
                    r["vi_text"], float(r["score"])) for r in rows]

    def phrase_in_session(self, phrase: str, session_id: str) -> list[Hit]:
        return self.search(phrase, session_id=session_id, mode="phrase", limit=50)
