"""P4.2.A.3 — Chạy bộ câu hỏi đánh giá Ask Your Lecture trên một buổi học đã lưu.

    python tools/qa_eval.py --session <session_id> --cases data/qa_cases.json

Ra qa_eval.csv để CHẤM TAY (đúng/một phần/sai, trích dẫn đúng?, có bịa?). Script chỉ tự đếm
những gì kiểm được bằng máy: số mốc bị loại vì không có trong bằng chứng, câu "không đủ dữ kiện".
"""
from __future__ import annotations

import argparse
import csv
import json

from _common import api_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--cases", default="data/qa_cases.json")
    ap.add_argument("--out", default="qa_eval.csv")
    a = ap.parse_args()
    from lecturemate.ai.agnes_adapter import AgnesAdapter
    from lecturemate.ai.qa_service import QAService
    from lecturemate.config import audio_dir, db_path
    from lecturemate.persistence.repository import SessionRepository

    base, model, key, cfg = api_settings()
    repo = SessionRepository(db_path(), audio_root=audio_dir())
    if not repo.get_session(a.session):
        print("Không thấy session; xem ID trong Thư viện (hoặc bảng sessions).")
        return 1
    svc = QAService(repo, AgnesAdapter(base, key, model, cfg.api_stream, cfg.api_extra_body) if key else None)
    cases = json.load(open(a.cases, encoding="utf-8"))["cases"]
    out = []
    for c in cases:
        ans = svc.ask(c["question"], a.session)
        out.append({"id": c["id"], "type": c["type"], "question": c["question"], "expected": c.get("expected", ""),
                    "answer": ans.text, "insufficient": int(ans.insufficient),
                    "citations": " ".join(f"{ms // 1000}s" for _, ms in ans.citations),
                    "invalid_citations_removed": len(ans.invalid_citations), "error": ans.error or "",
                    "manual_correct(dung/mot_phan/sai)": "", "manual_citation_ok(1/0)": "",
                    "manual_hallucination(1/0)": ""})
        print(out[-1]["id"], out[-1]["type"], "→", out[-1]["answer"][:80])
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    oot = [o for o in out if o["type"] == "ngoai_bai"]
    print(f"\nCâu ngoài bài trả 'không đủ dữ kiện': {sum(o['insufficient'] for o in oot)}/{len(oot)}")
    print(f"Mốc giả bị loại tự động: {sum(o['invalid_citations_removed'] for o in out)}")
    print(f"→ {a.out} (hãy chấm tay các cột manual_*)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
