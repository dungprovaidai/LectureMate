"""Chia transcript final thành các cửa sổ có chồng lấn, giữ ID segment và thứ tự."""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import format_ts


@dataclass
class Chunk:
    index: int
    segment_ids: list[str]
    start_ms: int
    end_ms: int
    text: str  # mỗi dòng: "[hh:mm:ss] câu"

    @property
    def timestamps(self) -> set[str]:
        return {ln[1:9] for ln in self.text.splitlines() if ln.startswith("[")}


def chunk_segments(segments, max_chars: int = 6000, overlap: int = 2) -> list[Chunk]:
    """``segments``: iterable có .id, .start_ms, .end_ms, .en_text (theo thứ tự).

    Không chunk nào vượt ``max_chars`` (trừ khi một segment đơn lẻ đã dài hơn — khi đó nó bị
    cắt bớt). ``overlap`` segment cuối của chunk trước được lặp lại ở đầu chunk sau.
    """
    segs = list(segments)
    if overlap < 0:
        raise ValueError("overlap >= 0")
    lines = [(s, f"[{format_ts(s.start_ms)}] {s.en_text.strip()}") for s in segs]
    lines = [(s, ln if len(ln) <= max_chars else ln[:max_chars - 1] + "…") for s, ln in lines]
    chunks: list[Chunk] = []
    i = 0
    while i < len(lines):
        cur, size, j = [], 0, i
        while j < len(lines) and size + len(lines[j][1]) + 1 <= max_chars:
            cur.append(lines[j])
            size += len(lines[j][1]) + 1
            j += 1
        if not cur:  # một dòng quá dài (đã cắt) → luôn tiến
            cur = [lines[j]]
            j += 1
        chunks.append(Chunk(len(chunks), [s.id for s, _ in cur], cur[0][0].start_ms, cur[-1][0].end_ms,
                            "\n".join(ln for _, ln in cur)))
        if j >= len(lines):
            break
        i = max(j - overlap, i + 1)
    return chunks
