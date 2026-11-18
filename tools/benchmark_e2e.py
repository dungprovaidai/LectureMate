"""P3.1.A.4 + P3.3.A.1 — Đo độ trễ end-to-end và chạy kiểm thử phiên dài (90 phút).

Phát lại một file WAV qua ĐÚNG pipeline của ứng dụng (FileAudioSource tốc độ thật →
AudioCaptureWorker → StreamingASR (Faster-Whisper) → SQLite → TranslationQueue → Agnes):

    python tools/benchmark_e2e.py --wav lecture_10min.wav --model base
    python tools/benchmark_e2e.py --wav lecture_90min.wav --model base --stress   # 90 phút
    python tools/benchmark_e2e.py --wav x.wav --mock-api --speed 4                 # không cần mạng

Kết quả: benchmark_e2e.md (p50/p90 tách ASR / hàng đợi / API, theo model & context),
stress_samples.csv (RSS bộ nhớ, backlog mỗi 30 s), DB riêng trong thư mục --home.
Đo trên dữ liệu thật mới được dùng cho cổng MVP — không chép số mẫu từ máy khác.
"""
from __future__ import annotations

import argparse
import csv
import os
import tempfile
import time
from pathlib import Path

from _common import api_settings


def rss_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        try:
            import resource

            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--model", default="base")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--speed", type=float, default=1.0, help=">1 phát nhanh hơn thời gian thực (chỉ để thử)")
    ap.add_argument("--mock-api", action="store_true")
    ap.add_argument("--mock-asr", action="store_true", help="bỏ ASR thật (kiểm tra pipeline/bộ nhớ)")
    ap.add_argument("--stress", action="store_true", help="ghi thêm mẫu bộ nhớ, báo cáo phiên dài")
    ap.add_argument("--home", default=None, help="thư mục dữ liệu riêng cho lần đo")
    ap.add_argument("--out", default="benchmark_e2e.md")
    a = ap.parse_args()
    home = a.home or tempfile.mkdtemp(prefix="lm-bench-")
    os.environ["LECTUREMATE_HOME"] = home

    from lecturemate.ai.agnes_adapter import AgnesAdapter
    from lecturemate.ai.llm_provider import MockProvider
    from lecturemate.app_controller import AppServices, Callbacks, LiveSession
    from lecturemate.asr.transcriber import FakeTranscriber, FasterWhisperTranscriber
    from lecturemate.audio.sources import FileAudioSource
    from lecturemate.config import audio_dir, db_path
    from lecturemate.domain.models import SessionState
    from lecturemate.metrics import markdown_report
    from lecturemate.persistence.repository import SessionRepository

    base, model, key, cfg = api_settings()
    cfg.asr_model = a.model
    cfg.asr_device = a.device
    if a.mock_api:
        provider = MockProvider(lambda m: "bản dịch giả", latency_s=0.6)
    elif key:
        provider = AgnesAdapter(base, key, model, cfg.api_stream, cfg.api_extra_body)
    else:
        print("Không có API key → dùng --mock-api hoặc cấu hình key.")
        return 2
    tr = FakeTranscriber([]) if a.mock_asr else FasterWhisperTranscriber(a.model, a.device, cfg.asr_compute_type)
    if not a.mock_asr:
        print("Tải model ASR…")
        tr.load()
    repo = SessionRepository(db_path(), audio_root=audio_dir())
    errors = []
    sv = AppServices(cfg, repo, lambda: provider, Callbacks(on_error=errors.append))
    src = FileAudioSource(a.wav, realtime=True, speed=a.speed)
    ls = LiveSession(sv, src, tr, audio_dir())
    sid = ls.start(f"bench {Path(a.wav).name}")
    print(f"Phiên {sid} — đang phát lại (Ctrl+C để dừng sớm)…")
    samples = []
    t0 = time.monotonic()
    try:
        while ls.state in (SessionState.RECORDING, SessionState.PAUSED):
            time.sleep(1)
            el = time.monotonic() - t0
            if int(el) % 30 == 0:
                st = ls.engine.stats if ls.engine else None
                samples.append({"t_s": int(el), "audio_ms": ls.elapsed_ms(), "rss_mb": rss_mb(),
                                "segments": len(repo.list_segments(sid)), "tq_backlog": sv.tq.backlog(),
                                "asr_rtf": round(st.realtime_factor, 3) if st else None,
                                "asr_dropped_frames": ls.audio.dropped_frames if ls.audio else None})
                print(samples[-1])
    except KeyboardInterrupt:
        ls.stop()
    # chờ dịch nốt
    end = time.monotonic() + 30
    while sv.tq.backlog() and time.monotonic() < end:
        time.sleep(0.5)
    rows = repo.latency_rows(sid)
    for r in rows:
        r["asr_model"] = a.model
    segs = repo.list_segments(sid)
    counts = repo.translation_counts(sid)
    md = markdown_report(rows, f"Độ trễ end-to-end — {Path(a.wav).name}")
    st = ls.engine.stats if ls.engine else None
    md += "\n## Bối cảnh lần đo\n\n"
    md += f"- Audio: `{a.wav}` · tốc độ phát ×{a.speed} · model ASR `{a.model}` ({a.device})\n"
    md += f"- API: {'MOCK (không phải số đo thật)' if a.mock_api else base + ' · ' + model}\n"
    md += f"- Segment final: {len(segs)} · trạng thái dịch: {counts}\n"
    if st:
        md += f"- ASR realtime factor: {st.realtime_factor:.3f} · partial bỏ qua do tụt: {st.skipped_partials}\n"
    md += f"- Frame audio bị bỏ do ASR quá tải (WAV vẫn đủ): {ls.audio.dropped_frames if ls.audio else '-'}\n"
    md += f"- Lỗi: {errors or 'không'}\n"
    if samples:
        md += f"- Bộ nhớ RSS: đầu {samples[0]['rss_mb']} MB → cuối {samples[-1]['rss_mb']} MB\n"
        with open("stress_samples.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(samples[0]))
            w.writeheader()
            w.writerows(samples)
    Path(a.out).write_text(md, encoding="utf-8")
    print(md)
    sv.shutdown()
    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
