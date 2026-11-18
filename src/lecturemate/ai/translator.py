"""Dựng prompt dịch và làm sạch/kiểm tra đầu ra."""
from __future__ import annotations

import json
import re
from importlib import resources

from ..domain.models import TranslationRequest

_PREFIX = re.compile(r"^\s*(bản dịch|dịch|translation|vietnamese|tiếng việt)\s*[:：-]\s*", re.I)


def load_prompt(name: str) -> str:
    return (resources.files("lecturemate.ai") / "prompts" / name).read_text(encoding="utf-8")


def build_messages(req: TranslationRequest, max_context_chars: int = 400, *, preview: bool = False) -> list[dict]:
    if preview:
        system = ("Dịch phần lời nói đang dở trong <cau_can_dich> sang tiếng Việt. "
                  "Giữ nguyên nếu đã là tiếng Việt. Chỉ trả bản dịch, không tự viết tiếp câu.")
        return [{"role": "system", "content": system}, {"role": "user", "content":
                f"<ngon_ngu_goc>{req.source_language}</ngon_ngu_goc>\n<cau_can_dich>{req.text_en}</cau_can_dich>"}]
    system = load_prompt("translate_vi.txt")
    ctx = " ".join(req.preceding_context)[-max_context_chars:]
    gl = "\n".join(f"- {en} = {vi}" for en, vi in req.glossary) or "(không có)"
    user = (
        f"<tu_dien>\n{gl}\n</tu_dien>\n"
        f"<ngu_canh_truoc>{ctx or '(không có)'}</ngu_canh_truoc>\n"
        f"<ngon_ngu_goc>{req.source_language}</ngon_ngu_goc>\n"
        f"<cau_can_dich>{req.text_en}</cau_can_dich>"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def clean_output(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\s*|\s*```$", "", t).strip()
    if t.startswith("{"):
        try:
            j = json.loads(t)
            for k in ("vi", "translation", "text"):
                if isinstance(j.get(k), str):
                    t = j[k]
                    break
        except json.JSONDecodeError:
            pass
    # bỏ thẻ suy luận nếu provider trả về
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    t = re.sub(r"</?cau_can_dich>", "", t).strip()
    t = _PREFIX.sub("", t)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    t = lines[0] if len(lines) == 1 else " ".join(lines)
    if len(t) >= 2 and t[0] in "\"“'«" and t[-1] in "\"”'»":
        t = t[1:-1].strip()
    return t
