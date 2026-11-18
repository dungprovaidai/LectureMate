"""P1.2.A.1 — Benchmark Faster-Whisper trên máy thật.

    python tools/benchmark_asr.py --wav test_5min.wav --models tiny base small
    python tools/benchmark_asr.py --wav test.wav --ref test_ref.txt --device cuda

Đo TÁCH BẠCH:
  • Tốc độ: thời gian tải model, realtime factor (RTF = thời gian xử lý / thời lượng audio)
    cho (a) cả file một lần và (b) mô phỏng streaming qua StreamingASR (gần với ứng dụng thật).
  • Chất lượng (chỉ khi có --ref): WER so với transcript tham chiếu bạn tự gõ/duyệt.
Kết quả: asr_report.csv. Gợi ý model mặc định = model chính xác nhất có RTF streaming < 0.5.
"""
from __future__ import annotations

import argparse
import csv
import re
import time

from _common import ROOT  # noqa: F401

from lecturemate.asr.asr_worker import StreamingASR
from lecturemate.asr.segment_committer import SegmentCommitter
from lecturemate.asr.transcriber import FasterWhisperTranscriber
from lecturemate.audio.audio_worker import AudioFrame
from lecturemate.audio.resample import Resampler
from lecturemate.audio.wav_writer import read_wav_mono


def wer(ref: str, hyp: str) -> float:
    norm = lambda s: re.sub(r"[^\w' ]+", " ", s.lower()).split()  # noqa: E731
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--ref", help="file .txt transcript tham chiếu (tùy chọn)")
    ap.add_argument("--models", nargs="+", default=["tiny", "base", "small"])
    ap.add_argument("--device", default="auto")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--out", default="asr_report.csv")
    ap.add_argument("--skip-stream", action="store_true")
    a = ap.parse_args()
    x, rate = read_wav_mono(a.wav)
    if rate != 16000:
        x = Resampler(rate, 16000).process(x)
    dur = len(x) / 16000
    ref = open(a.ref, encoding="utf-8").read() if a.ref else None
    print(f"Audio {dur:.1f} s. Model: {a.models}. Thiết bị: {a.device}")
    rows = []
    for m in a.models:
        tr = FasterWhisperTranscriber(m, a.device, a.compute_type, final_beam_size=2)
        t0 = time.perf_counter()
        tr.load()
        load_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        segs = tr.transcribe(x, 0, final=True)
        batch_s = time.perf_counter() - t0
        text = " ".join(s.text for s in segs)
        row = {"model": m, "device": a.device, "compute_type": a.compute_type, "audio_s": round(dur, 1),
               "load_s": round(load_s, 1), "batch_s": round(batch_s, 1), "rtf_batch": round(batch_s / dur, 3)}
        if not a.skip_stream:
            finals = []
            com = SegmentCommitter(None, "bench", start_sequence=0)
            eng = StreamingASR(tr, com, lambda e: finals.append(e) if e.type == "final" else None)
            t0 = time.perf_counter()
            for i in range(0, len(x), 1600):
                eng.feed(AudioFrame(x[i:i + 1600], i, time.monotonic_ns()))
            eng.flush()
            stream_s = time.perf_counter() - t0
            row.update(stream_s=round(stream_s, 1), rtf_stream=round(stream_s / dur, 3),
                       finals=len(finals), partials=eng.stats.partials)
            stream_text = " ".join(f.text_en for f in finals)
        if ref:
            row["wer_batch"] = round(wer(ref, text), 3)
            if not a.skip_stream:
                row["wer_stream"] = round(wer(ref, stream_text), 3)
        rows.append(row)
        print(row)
        with open(f"asr_{m}.txt", "w", encoding="utf-8") as f:
            f.write(text)
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if r.get("rtf_stream", r["rtf_batch"]) < 0.5]
    print(f"\nĐã lưu {a.out}.")
    if ok:
        best = ok[-1] if not ref else min(ok, key=lambda r: r.get("wer_stream", r.get("wer_batch", 1)))
        print(f"Gợi ý model mặc định: {best['model']} (RTF streaming < 0.5 trên máy này). "
              "Hãy đọc asr_<model>.txt để tự đánh giá chất lượng.")
    else:
        print("Không model nào đủ nhanh (RTF < 0.5) → cân nhắc chế độ “chỉ ghi âm, phiên âm sau buổi”.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
