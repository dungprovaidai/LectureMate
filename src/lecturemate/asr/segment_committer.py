"""Chốt segment final: gán sequence, chống trùng câu và trùng từ ở biên, rồi lưu DB."""
from __future__ import annotations

import re
import threading
from dataclasses import replace

from ..domain.models import TranscriptEvent

# Câu "ảo giác" quen thuộc của Whisper trên đoạn gần như im lặng
HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you", "bye",
    "please subscribe", "subtitles by the amara.org community", "so", "okay",
}

_WORD = re.compile(r"[\w'’%.-]+", re.UNICODE)


def normalize(text: str) -> str:
    return " ".join(w.lower().strip(".,!?;:") for w in text.split()).strip()


def strip_overlap(prev: str, cur: str, max_n: int = 6) -> str:
    """Bỏ các từ đầu ``cur`` lặp lại đuôi ``prev`` (lỗi nhân đôi từ ở biên cắt)."""
    pw = prev.split()
    cw = cur.split()
    for n in range(min(max_n, len(pw), len(cw)), 0, -1):
        if [normalize(w) for w in pw[-n:]] == [normalize(w) for w in cw[:n]]:
            # chỉ coi là trùng nếu cụm đủ dài hoặc là toàn bộ câu ngắn
            if n >= 2 or len(cw) == 1:
                return " ".join(cw[n:])
    return cur


class SegmentCommitter:
    def __init__(self, repo, session_id: str, translate: bool = True, start_sequence: int | None = None):
        self.repo = repo
        self.session_id = session_id
        self.translate = translate
        self._lock = threading.Lock()
        if start_sequence is None:
            start_sequence = repo.next_sequence(session_id) if repo is not None else 0
        self.next_seq = start_sequence
        self.prev_text = ""
        self.prev_end = -10_000
        self.committed: list[TranscriptEvent] = []
        self.dropped = 0

    def commit(self, ev: TranscriptEvent, speech_ratio: float = 1.0) -> TranscriptEvent | None:
        with self._lock:
            text = " ".join(ev.text_en.split())
            gap = ev.start_ms - self.prev_end
            # Chỉ bỏ từ trùng ở biên khi hai đoạn liền nhau (cắt cứng câu dài) và câu trước chưa
            # kết thúc. Sau khoảng lặng, giảng viên lặp lại cụm từ là chuyện bình thường.
            if self.prev_text and gap <= 250 and not self.prev_text.rstrip().endswith((".", "?", "!")):
                text = strip_overlap(self.prev_text, text)
            norm = normalize(text)
            if not norm or not _WORD.search(norm):
                self.dropped += 1
                return None
            if norm == normalize(self.prev_text) and gap <= 1000:
                self.dropped += 1
                return None
            if norm in HALLUCINATIONS and speech_ratio < 0.4:
                self.dropped += 1
                return None
            start = max(ev.start_ms, self.prev_end) if ev.start_ms < self.prev_end else ev.start_ms
            final = replace(ev, type="final", text_en=text, sequence_number=self.next_seq,
                            start_ms=start, end_ms=max(ev.end_ms, start + 1))
            if self.repo is not None:
                if not self.repo.commit_final(final, translate=self.translate):
                    self.dropped += 1
                    return None
            self.next_seq += 1
            self.prev_text = text
            self.prev_end = final.end_ms
            self.committed.append(final)
            return final
