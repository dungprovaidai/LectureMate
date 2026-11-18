"""P1.1.A.2 — Ghi và xác minh WAV 5 phút.

    python tools/record_test.py --source loopback --minutes 5 --out test_5min.wav
    python tools/record_test.py --check test_5min.wav        # chỉ phân tích file có sẵn

Báo cáo: thời lượng thật vs kỳ vọng, % mẫu bị clip (méo), % im lặng, số khoảng mất tiếng
(>= 0.5 s im lặng tuyệt đối giữa đoạn có tiếng — dấu hiệu rớt buffer). Sau đó NGHE LẠI file.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
from _common import ROOT  # noqa: F401

from lecturemate.audio.audio_worker import AudioCaptureWorker
from lecturemate.audio.sources import SoundCardSource
from lecturemate.audio.wav_writer import WavWriter, read_wav_mono


def analyze(path: str, expected_s: float | None = None) -> dict:
    x, rate = read_wav_mono(path)
    dur = len(x) / rate
    clip = float(np.mean(np.abs(x) >= 0.999)) * 100 if len(x) else 0
    frame = rate // 10
    n = len(x) // frame
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1)) if n else np.zeros(0)
    silent = rms < 10 ** (-60 / 20)
    digital_zero = np.max(np.abs(x[: n * frame].reshape(n, frame)), axis=1) == 0 if n else np.zeros(0, bool)
    dropouts, run = 0, 0
    for z in digital_zero:
        run = run + 1 if z else 0
        if run == 5:
            dropouts += 1
    rep = {"file": path, "rate": int(rate), "duration_s": round(float(dur), 2), "clipped_pct": round(float(clip), 4),
           "silent_pct": round(float(np.mean(silent)) * 100, 1) if n else 100.0,
           "dropouts_ge_0_5s": int(dropouts), "peak_dbfs": round(float(20 * np.log10(max(float(np.max(np.abs(x))), 1e-6))), 1) if len(x) else None}
    if expected_s:
        rep["expected_s"] = expected_s
        rep["duration_error_s"] = round(dur - expected_s, 2)
    rep["verdict"] = ("ĐẠT" if rep["clipped_pct"] < 0.1 and rep["silent_pct"] < 50 and dropouts == 0
                      else "CẦN NGHE LẠI / KIỂM TRA")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["microphone", "loopback"], default="microphone")
    ap.add_argument("--device", default="")
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--out", default="test_record.wav")
    ap.add_argument("--check", help="chỉ phân tích file WAV có sẵn")
    a = ap.parse_args()
    if a.check:
        print(json.dumps(analyze(a.check), ensure_ascii=False, indent=2))
        return 0
    secs = a.minutes * 60
    w = AudioCaptureWorker(SoundCardSource(a.source, a.device), None, WavWriter(a.out),
                           on_error=lambda m: print("LỖI:", m))
    print(f"Đang ghi {a.minutes} phút từ {a.source}… (Ctrl+C để dừng sớm)")
    w.start()
    t0 = time.monotonic()
    try:
        while w.is_alive() and time.monotonic() - t0 < secs:
            time.sleep(1)
            print(f"\r{int(time.monotonic() - t0)} s", end="", flush=True)
    except KeyboardInterrupt:
        pass
    wall = time.monotonic() - t0
    w.stop()
    print()
    rep = analyze(a.out, expected_s=wall)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    with open(a.out + ".report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print("→ Hãy NGHE LẠI file để xác nhận không méo/mất tiếng (số liệu tự động không thay được tai người).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
