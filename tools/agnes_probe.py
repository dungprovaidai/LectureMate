"""P1.2.A.2 — Xác minh kết nối Agnes 3.0 Flash bằng 3 request text thật.

Chuẩn bị: copy .env.example → .env, điền AGNES_API_KEY (hoặc lưu key trong ứng dụng).
    python tools/agnes_probe.py

Ghi api_probe.json: endpoint, model trả về, HTTP/lỗi, TTFT, tổng thời gian, usage (thật hoặc
ước lượng). KHÔNG ghi API key. Nếu lỗi 401/404 → báo "blocked" kèm lý do, không tự đổi endpoint.
"""
from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone

from _common import api_settings

from lecturemate.ai.agnes_adapter import AgnesAdapter
from lecturemate.ai.llm_provider import LLMError
from lecturemate.ai.secrets import mask

PROMPTS = [
    "Translate to Vietnamese, output only the translation: Good morning, everyone.",
    "Translate to Vietnamese, output only the translation: Opportunity cost is the value of the next best alternative.",
    "Translate to Vietnamese, output only the translation: Prices did not rise by more than 3.5% in 2023.",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--out", default="api_probe.json")
    a = ap.parse_args()
    base, model, key, cfg = api_settings()
    print(f"Endpoint: {base}/chat/completions · model: {model} · key: {mask(key)}")
    if not key:
        print("BLOCKED: chưa có API key (AGNES_API_KEY trong .env hoặc lưu trong ứng dụng).")
        return 2
    results = []
    for stream in ([False] if a.no_stream else [False, True]):
        ad = AgnesAdapter(base, key, model, stream=stream, extra_body=cfg.api_extra_body)
        for p in PROMPTS:
            r = {"stream": stream, "prompt": p}
            try:
                resp = ad.complete([{"role": "user", "content": p}], max_tokens=80, timeout_s=20)
                r.update(ok=True, model_returned=resp.model, text=resp.text, ttft_ms=resp.ttft_ms,
                         total_ms=resp.total_ms, input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                         usage_estimated=resp.usage_estimated)
            except LLMError as e:
                r.update(ok=False, error_code=e.code, http_status=e.status, error=str(e))
            results.append(r)
            print(json.dumps(r, ensure_ascii=False))
        ad.close()
    ok = [r for r in results if r.get("ok")]
    out = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": f"{base}/chat/completions", "model_requested": model, "python": platform.python_version(),
        "status": "ok" if len(ok) == len(results) else ("partial" if ok else "blocked"),
        "results": results,
        "note": "Số liệu này chỉ là 3–6 request smoke test; độ trễ thật cần translation_benchmark.py (100 câu).",
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nTrạng thái: {out['status']} → {a.out}")
    if not ok:
        codes = {r.get("error_code") for r in results}
        if "auth" in codes:
            print("Gợi ý: kiểm tra API key / quyền gói dịch vụ.")
        if "bad_request" in codes:
            print("Gợi ý: kiểm tra base URL và model ID trong tài liệu/console Agnes của bạn (không đoán endpoint).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
