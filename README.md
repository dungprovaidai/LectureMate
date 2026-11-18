# LectureMate AI

Ứng dụng Windows cho sinh viên nghe giảng tiếng Anh: **phụ đề Anh–Việt gần thời gian thực**, tự lưu
âm thanh + transcript có mốc thời gian, xem lại/tua theo đoạn, **ghi chú AI** và **hỏi đáp có dẫn mốc**.

```
Micro / WASAPI loopback → AudioCaptureWorker ─┬─→ WAV (local)
                                              └─→ VAD + StreamingASR (Faster-Whisper, local)
                                                   ├─ partial → UI (chữ nhạt, cập nhật tại chỗ)
                                                   └─ final  → SQLite → TranslationQueue → Agnes API → UI
```

- Âm thanh **không** rời khỏi máy. Chỉ **văn bản** được gửi tới API dịch khi bạn bật.
- Không cần Ollama, không cần GPU (GPU giúp nhanh hơn). Một nguồn âm thanh mỗi buổi.
- Mất mạng vẫn ghi âm + phiên âm; bản dịch tự bù khi có mạng lại.
- Máy yếu: chế độ **chỉ ghi âm → phiên âm & dịch sau buổi**.

## Cài đặt (Windows 10/11, Python 3.10 trở lên)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
pip install -e .
```

```bash
python -m lecturemate
```

Chạy test (không cần micro, mạng hay model ASR — dùng Fake/Mock):

```bash
python -m pytest -q
```

Đóng gói bản portable (xem `packaging/README-Windows.md`):

```bash
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -WithModel base
```

Demo không cần micro: đặt biến môi trường `LECTUREMATE_DEMO_WAV=đường\dẫn\bai_giang.wav` rồi chạy app —
nút Bắt đầu sẽ phát lại file đó như một nguồn thu âm.

## Làm ngay tuần này (Giai đoạn 0 — kiểm chứng trên máy THẬT của bạn)

| # | Lệnh | Kết quả cần |
|---|---|---|
| 1 | `python tools/list_devices.py --test 3` | thấy micro + loopback có tín hiệu → `devices_report.json` |
| 2 | `python tools/record_test.py --source loopback --minutes 5 --out test_5min.wav` | WAV 5 phút, `ĐẠT`, **nghe lại** |
| 3 | `python tools/benchmark_asr.py --wav test_5min.wav --models tiny base small` | `asr_report.csv`, RTF từng model |
| 4 | copy `.env.example` → `.env`, điền key; `python tools/agnes_probe.py` | `api_probe.json` status `ok` |
| 5 | `python tools/translation_benchmark.py --price-in <giá> --price-out <giá>` | p50/p90, lỗi, token, chi phí 100 câu |
| 6 | Điền `docs/quyet-dinh-kien-truc.md` bằng số đo trên | quyết định đi tiếp / sửa / dừng |

Sau đó: `python tools/benchmark_e2e.py --wav bai_10phut.wav --model base` (≥100 câu final thật) và
`--stress` với file 90 phút cho cổng MVP.

## Cấu trúc

```
src/lecturemate/
  config.py              cấu hình + đường dẫn %LOCALAPPDATA%\LectureMate
  domain/models.py       TranscriptEvent, TranslationRequest/Result, SessionState
  session_controller.py  state machine IDLE→CHECKING→RECORDING↔PAUSED→STOPPING→COMPLETED, phục hồi
  app_controller.py      nối pipeline (headless được), phiên âm sau buổi
  metrics.py             p50/p90 tách ASR / hàng đợi / API / UI
  export_markdown.py     xuất transcript MD/JSON, ghi chú MD (mốc = link)
  audio/                 SoundCard (mic + loopback), resampler, WAV an toàn crash, worker
  asr/                   VAD, Faster-Whisper, StreamingASR partial/final, SegmentCommitter chống trùng
  ai/                    LLMProvider, AgnesAdapter, glossary, TranslationQueue, notes, FTS5 search, Q&A
  persistence/           SQLite WAL + migrations + repository
  ui/                    PySide6: bảng điều khiển, phụ đề, thư viện, chi tiết + player, thiết lập
tools/                   script Giai đoạn 0 và benchmark
tests/                   137 test (pytest + pytest-qt)
docs/                    tiến độ 33 bước, mẫu báo cáo, checklist
packaging/               PyInstaller spec + build_windows.ps1
```

## Về API Agnes

Mặc định `https://apihub.agnes-ai.com/v1` + `POST /chat/completions` + `Authorization: Bearer`, model
`agnes-3.0-flash` — lấy từ catalog AgnesAI-Labs (tra ngày 18/11/2026). Catalog đó **không** liệt kê riêng
`agnes-3.0-flash`, giá token hay tham số tắt suy luận. Vì vậy mọi thứ đều cấu hình được trong *Thiết lập →
API dịch* (base URL, model, JSON tham số thêm, giá) và phải xác minh bằng `tools/agnes_probe.py` với tài khoản của bạn.

## Đã kiểm chứng / chưa kiểm chứng

**Đã chạy được trong môi trường phát triển (Linux, không micro/không mạng ra Hugging Face):** 137 test xanh
gồm DB/migration/xóa an toàn, state machine + phục hồi crash, audio worker (pause, mất thiết bị, queue đầy),
StreamingASR với FakeTranscriber (100 câu không trùng sequence, partial trước final, cắt câu dài không nhân đôi từ),
adapter HTTP với mock (JSON, SSE streaming, 401/404/429/500, timeout, mất mạng), hàng đợi dịch (≤2 đồng thời, 429,
mất mạng, không gửi trùng, ngân sách), glossary 20 thuật ngữ, FTS5, ghi chú/hỏi đáp không bịa mốc, UI pytest-qt, end-to-end headless.

**Chưa kiểm chứng (cần máy Windows của bạn):** SoundCard/WASAPI loopback thật, Faster-Whisper thật (container
chặn tải model), API Agnes thật (chưa có key), độ trễ p50/p90 thật, chất lượng bản dịch, PyInstaller trên Windows,
QtMultimedia phát WAV trên Windows. Không có số đo nào trong repo được trình bày như kết quả thật.
