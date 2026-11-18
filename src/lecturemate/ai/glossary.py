"""Từ điển thuật ngữ theo môn học + kiểm tra bản dịch giữ thuật ngữ, số liệu, phủ định."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    en: str
    vi: str
    note: str = ""

    def pattern(self) -> re.Pattern:
        # khớp nguyên từ, không phân biệt hoa thường, chấp nhận số nhiều -s/-es/-ies
        words = self.en.split()
        last = words[-1]
        if len(last) > 2 and last[-1].lower() == "y" and last[-2].lower() not in "aeiou":
            tail = re.escape(last[:-1]) + r"(?:y|ies)"
        else:
            tail = re.escape(last) + r"(?:s|es)?"
        esc = r"\s+".join([re.escape(w) for w in words[:-1]] + [tail])
        return re.compile(rf"(?<![\w-]){esc}(?![\w-])", re.IGNORECASE)


class Glossary:
    def __init__(self, terms: list[Term] | None = None):
        self.terms = [t for t in (terms or []) if t.en.strip() and t.vi.strip()]
        self._pats = [(t, t.pattern()) for t in self.terms]

    @classmethod
    def from_json(cls, items: list[dict]) -> "Glossary":
        return cls([Term(str(i.get("en", "")).strip(), str(i.get("vi", "")).strip(), str(i.get("note", "")))
                    for i in items or [] if isinstance(i, dict)])

    def to_json(self) -> list[dict]:
        return [{"en": t.en, "vi": t.vi, "note": t.note} for t in self.terms]

    @classmethod
    def from_csv(cls, text: str) -> "Glossary":
        """CSV/TSV: en,vi[,note]. Dòng tiêu đề 'en,vi' được bỏ qua."""
        dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
        rows = list(csv.reader(io.StringIO(text), dialect=dialect))
        terms = []
        for r in rows:
            if len(r) < 2:
                continue
            if r[0].strip().lower() in ("en", "english") and r[1].strip().lower() in ("vi", "tiếng việt", "vietnamese"):
                continue
            terms.append(Term(r[0].strip(), r[1].strip(), r[2].strip() if len(r) > 2 else ""))
        return cls(terms)

    def relevant(self, text: str, limit: int = 15) -> list[Term]:
        """Chỉ đưa thuật ngữ xuất hiện trong câu vào prompt (giới hạn token)."""
        hits = [t for t, p in self._pats if p.search(text)]
        # ưu tiên cụm dài hơn (vd. "opportunity cost" trước "cost")
        hits.sort(key=lambda t: -len(t.en))
        return hits[:limit]

    def check(self, en: str, vi: str) -> list[str]:
        """Trả danh sách thuật ngữ EN có trong câu nhưng bản dịch thiếu VI quy định."""
        missing = []
        vi_low = vi.lower()
        for t in self.relevant(en, limit=100):
            options = [o.strip().lower() for o in re.split(r"[/;|]", t.vi) if o.strip()]
            if not any(o in vi_low for o in options):
                missing.append(t.en)
        return missing


_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_NEG_EN = re.compile(r"\b(not|no|never|none|nobody|nothing|neither|nor|cannot|without|hardly|barely)\b|n't\b", re.I)
_NEG_VI = re.compile(r"\b(không|chưa|chẳng|chả|đừng|chớ|không hề|chưa từng|hầu như không|thiếu|vô|phi)\b", re.I)
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "hundred": "100", "thousand": "1000",
}


def _norm_num(s: str) -> str:
    return s.replace(",", "").replace(".", "")


def quality_warnings(en: str, vi: str, glossary: Glossary | None = None) -> list[str]:
    """Cảnh báo rẻ, không gọi AI: số liệu mất, phủ định mất, thuật ngữ sai, bản dịch bất thường."""
    w: list[str] = []
    if not vi.strip():
        return ["empty"]
    en_nums = {_norm_num(n) for n in _NUM.findall(en)}
    vi_nums = {_norm_num(n) for n in _NUM.findall(vi)}
    if en_nums - vi_nums:
        w.append("number:" + ",".join(sorted(en_nums - vi_nums)))
    if _NEG_EN.search(en) and not _NEG_VI.search(vi):
        w.append("negation")
    if glossary:
        miss = glossary.check(en, vi)
        if miss:
            w.append("term:" + ",".join(miss))
    if len(vi) > 4 * max(20, len(en)):
        w.append("too_long")
    return w


SAMPLE_GLOSSARY_ECON = [
    ("inflation", "lạm phát"), ("opportunity cost", "chi phí cơ hội"), ("supply", "cung"),
    ("demand", "cầu"), ("equilibrium", "cân bằng"), ("elasticity", "độ co giãn"),
    ("marginal cost", "chi phí cận biên"), ("monetary policy", "chính sách tiền tệ"),
    ("fiscal policy", "chính sách tài khóa"), ("interest rate", "lãi suất"),
    ("gross domestic product", "tổng sản phẩm quốc nội"), ("market failure", "thất bại thị trường"),
    ("externality", "ngoại tác"), ("comparative advantage", "lợi thế so sánh"),
    ("price ceiling", "giá trần"), ("price floor", "giá sàn"), ("utility", "độ thỏa dụng"),
    ("monopoly", "độc quyền"), ("recession", "suy thoái"), ("exchange rate", "tỷ giá hối đoái"),
]


def sample_glossary() -> Glossary:
    return Glossary([Term(en, vi) for en, vi in SAMPLE_GLOSSARY_ECON])
