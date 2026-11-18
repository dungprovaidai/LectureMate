# Tiến độ theo 33 bước của lộ trình

Ký hiệu: ✅ code + test xong · 🧪 code xong, **cần chạy trên máy Windows/API thật** để có số liệu · 📝 mẫu báo cáo sẵn, cần dữ liệu thật · ⏳ việc của người dùng (không thể làm thay).

## P1 · Giai đoạn 0 — Kiểm chứng
| Bước | Trạng thái | Đầu ra trong repo |
|---|---|---|
| P1.1.A.1 Liệt kê và thử thiết bị | 🧪 | `tools/list_devices.py` → `devices_report.json` |
| P1.1.A.2 Ghi và xác minh WAV 5 phút | 🧪 | `tools/record_test.py` (ghi + phân tích clip/im lặng/rớt) |
| P1.2.A.1 Benchmark Faster-Whisper | 🧪 | `tools/benchmark_asr.py` → `asr_report.csv` (RTF batch + streaming, WER nếu có ref) |
| P1.2.A.2 Xác minh Agnes | 🧪 | `tools/agnes_probe.py`, `.env.example` → `api_probe.json` |
| P1.2.A.3 Đo 100 câu | 🧪 | `tools/translation_benchmark.py`, `data/sample_sentences.csv` (100 câu tự soạn) |
| P1.3.A.1 Chốt cấu hình MVP | 📝 | `docs/quyet-dinh-kien-truc.md` |
| P1.3.B.1 Phương án chỉ ghi âm | ✅ | `docs/fallback-spec.md`, chế độ `record_only`, `transcribe_file_job`, test `test_record_only_then_transcribe_after` |

## P2 · Nền tảng
| Bước | Trạng thái | Đầu ra |
|---|---|---|
| P2.1.A.1 Cấu trúc dự án | ✅ | `pyproject.toml`, `src/lecturemate/`, `python -m lecturemate` |
| P2.1.A.2 SQLite + migration | ✅ | `persistence/db.py`, `migrations/001_init.sql`, `002_search_notes.sql`, `tests/test_db.py` |
| P2.1.A.3 SessionController + phục hồi | ✅ | `session_controller.py`, `tests/test_session_states.py` |
| P2.2.A.1 MainWindow + nút | ✅ | `ui/main_window.py`, `tests/test_ui.py` |
| P2.2.A.2 Phụ đề EN/VI partial/final | ✅ | `ui/subtitle_widget.py`, test trong `tests/test_ui.py` |
| P2.3.A.1 AudioCaptureWorker + WAV | ✅ (🧪 thiết bị thật) | `audio/audio_worker.py`, `audio/wav_writer.py`, `tests/test_audio_worker.py` |
| P2.3.A.2 ASR partial/final | ✅ (🧪 model thật) | `asr/asr_worker.py`, `asr/segment_committer.py`, `tests/test_segment_dedupe.py` |
| P2.3.A.3 Nối end-to-end | ✅ | `app_controller.py`, `tests/test_integration.py` |

## P3 · Dịch & lưu MVP
| Bước | Trạng thái | Đầu ra |
|---|---|---|
| P3.1.A.1 LLMProvider + AgnesAdapter | ✅ (🧪 API thật) | `ai/llm_provider.py`, `ai/agnes_adapter.py`, `tests/test_agnes_mock.py` |
| P3.1.A.2 Prompt dịch + glossary | ✅ | `ai/prompts/translate_vi.txt`, `ai/glossary.py`, `tests/test_glossary.py` (20 thuật ngữ, phủ định, số) |
| P3.1.A.3 TranslationQueue | ✅ | `ai/translation_queue.py`, `tests/test_translation_queue.py` |
| P3.1.A.4 Đo p50/p90 | 🧪 | `metrics.py`, `tools/benchmark_e2e.py` → `benchmark_e2e.md`, `tests/test_metrics.py` |
| P3.2.A.1 Thư viện | ✅ | `ui/session_library.py` |
| P3.2.A.2 Player + seek | ✅ (🧪 phát âm thanh trên Windows) | `ui/session_detail.py`, `ui/audio_player.py`, `test_segment_at_accuracy` |
| P3.3.A.1 90 phút + crash | 🧪 | `tools/benchmark_e2e.py --stress`, `docs/regression_checklist.md`, `test_crash_recovery` |
| P3.3.A.2 Cổng MVP 50 câu | 📝 | `docs/mvp_acceptance_report.md` + cột chấm tay trong `translation_results.csv` |

## P4 · Trợ lý học tập
| Bước | Trạng thái | Đầu ra |
|---|---|---|
| P4.1.A.1 Chunker | ✅ | `ai/notes_chunker.py` |
| P4.1.A.2 Prompt ghi chú có dẫn nguồn | ✅ | `ai/prompts/notes.txt`, `ai/notes_generator.py` (loại mốc giả, chạy lại chỉ phần lỗi) |
| P4.1.A.3 Lưu + xuất Markdown | ✅ | `persistence/repository.py` (notes), `export_markdown.py`, tab Ghi chú |
| P4.2.A.1 Tìm kiếm FTS5 | ✅ | `ai/search_service.py` (tìm cả tiếng Việt không dấu) |
| P4.2.A.2 Ask Your Lecture | ✅ | `ai/qa_service.py`, `ai/prompts/qa.txt`, tab Hỏi bài (click mốc để tua) |
| P4.2.A.3 Đánh giá 20 câu hỏi | 📝 | `tools/qa_eval.py`, `data/qa_cases.json` (điền theo bài thật) |

> Lưu ý: kit đặt Notes/Q&A sau cổng MVP. Code đã có sẵn nhưng **nên bật dùng sau khi** P3.3 đạt.

## P5 · Hoàn thiện
| Bước | Trạng thái | Đầu ra |
|---|---|---|
| P5.1.A.1 Keyring, quyền, xóa | ✅ | `ai/secrets.py`, `docs/privacy_checklist.md`, `tests/test_secret_and_delete.py` |
| P5.1.A.2 PyInstaller | 🧪 | `packaging/lecturemate.spec`, `packaging/build_windows.ps1`, `packaging/README-Windows.md` |
| P5.2.A.1 3–5 buổi học thử | ⏳ | `docs/pilot_template.csv`, `docs/pilot_summary.md` |
| P5.2.A.2 Backlog v2 | ⏳ | `docs/backlog_v2.md` (khung ưu tiên, điền sau pilot) |
