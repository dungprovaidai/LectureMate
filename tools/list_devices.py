"""P1.1.A.1 — Liệt kê và thử thiết bị âm thanh (micro + WASAPI loopback).

    python tools/list_devices.py              # chỉ liệt kê
    python tools/list_devices.py --test 3     # thử mỗi thiết bị 3 giây, đo mức âm

Khi thử loopback: hãy phát một video/bài giảng trên máy. Kết quả lưu devices_report.json.
"""
from __future__ import annotations

import argparse
import json
import time

from _common import ROOT  # noqa: F401

from lecturemate.audio.audio_worker import rms_dbfs
from lecturemate.audio.sources import DeviceError, SoundCardSource, list_devices


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=float, default=0, help="số giây thử mỗi thiết bị (0 = không thử)")
    ap.add_argument("--out", default="devices_report.json")
    a = ap.parse_args()
    try:
        devs = list_devices()
    except DeviceError as exc:
        print("LỖI:", exc)
        return 1
    if not devs:
        print("Không tìm thấy thiết bị (hoặc chưa cài soundcard: pip install soundcard).")
        return 1
    report = []
    for d in devs:
        row = {"id": d.id, "name": d.name, "kind": d.kind, "default": d.is_default}
        print(f"[{d.kind:10}] {'*' if d.is_default else ' '} {d.name}")
        if a.test:
            src = SoundCardSource(d.kind, d.id)
            try:
                src.open()
                levels, t0 = [], time.monotonic()
                while time.monotonic() - t0 < a.test:
                    levels.append(rms_dbfs(src.read(1600)))
                row.update(ok=True, peak_dbfs=round(max(levels), 1), mean_dbfs=round(sum(levels) / len(levels), 1),
                           has_signal=max(levels) > -55)
                print(f"      → OK, đỉnh {row['peak_dbfs']} dBFS, {'CÓ' if row['has_signal'] else 'KHÔNG có'} tín hiệu")
            except DeviceError as exc:
                row.update(ok=False, error=str(exc))
                print("      → LỖI:", exc)
            finally:
                src.close()
        report.append(row)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nĐã lưu {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
