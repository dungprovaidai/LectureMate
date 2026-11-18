"""P1.2.A.3 — Đo 100 câu dịch chuyên ngành (cũng dùng cho P3.3.A.2 nếu có cột ref_vi).

    python tools/translation_benchmark.py --input data/sample_sentences.csv --pace 0.5
    python tools/translation_benchmark.py --mock        # chạy thử không cần mạng

Input CSV: cột ``en`` (bắt buộc), ``ref_vi`` (tùy chọn). Dùng prompt + glossary y như ứng dụng.
Ra translation_results.csv + tóm tắt: p50/p90 TTFT & tổng, số lỗi, token, chi phí theo giá cấu
hình (--price-in/--price-out USD/1M token), cảnh báo thuật ngữ/số/phủ định.
"""
from __future__ import annotations

import argparse
import csv
import time

from _common import ROOT, api_settings

from lecturemate.ai.agnes_adapter import AgnesAdapter
from lecturemate.ai.glossary import Glossary, quality_warnings, sample_glossary
from lecturemate.ai.llm_provider import LLMError, MockProvider
from lecturemate.ai.translation_queue import cost_usd
from lecturemate.ai.translator import build_messages, clean_output
from lecturemate.domain.models import TranslationRequest
from lecturemate.metrics import percentile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(ROOT / "data" / "sample_sentences.csv"))
    ap.add_argument("--glossary", help="CSV en,vi (mặc định: mẫu Kinh tế 20 từ)")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--pace", type=float, default=0.5, help="giây nghỉ giữa các request (tránh 429)")
    ap.add_argument("--context", type=int, default=2)
    ap.add_argument("--price-in", type=float, default=None)
    ap.add_argument("--price-out", type=float, default=None)
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--out", default="translation_results.csv")
    a = ap.parse_args()
    base, model, key, cfg = api_settings()
    p_in = cfg.price_in_per_mtok if a.price_in is None else a.price_in
    p_out = cfg.price_out_per_mtok if a.price_out is None else a.price_out
    gl = Glossary.from_csv(open(a.glossary, encoding="utf-8-sig").read()) if a.glossary else sample_glossary()
    rows = list(csv.DictReader(open(a.input, encoding="utf-8-sig")))[: a.limit]
    if a.mock:
        provider = MockProvider(lambda m: "bản dịch giả", latency_s=0.05)
    else:
        if not key:
            print("BLOCKED: chưa có API key.")
            return 2
        provider = AgnesAdapter(base, key, model, stream=not a.no_stream, extra_body=cfg.api_extra_body)
    out, prev = [], []
    for i, r in enumerate(rows):
        en = r["en"].strip()
        terms = gl.relevant(en)
        req = TranslationRequest(f"b{i}", "bench", en, tuple(prev[-a.context:]) if a.context else (),
                                 tuple((t.en, t.vi) for t in terms))
        msgs = build_messages(req)
        rec = {"i": i, "en": en, "ref_vi": r.get("ref_vi", ""), "terms": ";".join(t.en for t in terms)}
        try:
            resp = provider.complete(msgs, max_tokens=cfg.max_output_tokens, timeout_s=cfg.api_timeout_s)
            vi = clean_output(resp.text)
            rec.update(ok=1, vi=vi, ttft_ms=resp.ttft_ms, total_ms=resp.total_ms, input_tokens=resp.input_tokens,
                       output_tokens=resp.output_tokens, usage_estimated=int(resp.usage_estimated),
                       cost_usd=round(cost_usd(resp.input_tokens, resp.output_tokens, p_in, p_out), 8),
                       warnings="|".join(quality_warnings(en, vi, gl)), error="")
        except LLMError as e:
            rec.update(ok=0, vi="", error=e.code)
        out.append(rec)
        prev.append(en)
        print(f"{i + 1:3}/{len(rows)} {'OK ' if rec['ok'] else 'ERR'} {rec.get('total_ms', '-')} ms {rec.get('warnings') or rec.get('error')}")
        time.sleep(a.pace)
    keys = ["i", "en", "vi", "ref_vi", "terms", "ok", "error", "ttft_ms", "total_ms", "input_tokens",
            "output_tokens", "usage_estimated", "cost_usd", "warnings", "manual_score_0_2", "manual_note"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    ok = [r for r in out if r["ok"]]
    tt = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
    tot = [r["total_ms"] for r in ok]
    fmt = lambda v: "—" if v is None else f"{v:.0f} ms"  # noqa: E731
    print("\n==== TÓM TẮT ====")
    print(f"Request: {len(out)} · lỗi: {len(out) - len(ok)}")
    print(f"TTFT  p50 {fmt(percentile(tt, 50))} · p90 {fmt(percentile(tt, 90))}")
    print(f"Tổng  p50 {fmt(percentile(tot, 50))} · p90 {fmt(percentile(tot, 90))}")
    tin, tout = sum(r["input_tokens"] for r in ok), sum(r["output_tokens"] for r in ok)
    est = any(r["usage_estimated"] for r in ok)
    print(f"Token: in {tin} · out {tout}{' (CÓ ƯỚC LƯỢNG — provider không trả usage)' if est else ''}")
    print(f"Chi phí theo giá cấu hình (${p_in}/1M in, ${p_out}/1M out): ${sum(r['cost_usd'] for r in ok):.6f}"
          + ("  ⚠ giá = 0, hãy nhập giá thật" if not (p_in or p_out) else ""))
    warn = [r for r in ok if r["warnings"]]
    print(f"Câu có cảnh báo tự động (thuật ngữ/số/phủ định): {len(warn)}/{len(ok)} — xem cột warnings, "
          "chấm tay cột manual_score_0_2.")
    print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
