# Backlog phiên bản tiếp theo (P5.2.A.2) — khung ưu tiên

Chỉ xếp hạng **sau** pilot. Điểm = Giá trị (1–5, từ dữ liệu pilot) ÷ Công sức (ước lượng, ngày).
Không kéo các mục này ngược vào MVP.

| Mục | Vấn đề pilot liên quan | Giá trị | Công sức (ước) | Điểm | Ghi chú kỹ thuật |
|---|---|---|---|---|---|
| Phụ đề nổi (overlay luôn-trên-cùng) | | | 2–3 ngày | | cửa sổ frameless + `WindowStaysOnTopHint`, nhận cùng signal |
| Tùy biến thuật ngữ nâng cao (gợi ý từ transcript) | | | 2 ngày | | đếm n-gram danh từ hay gặp, đề xuất thêm glossary |
| Xuất PDF | | | 1 ngày | | QTextDocument → QPdfWriter từ Markdown |
| Speaker diarization | | | 5+ ngày | | cần model riêng (pyannote) — nặng, cân nhắc kỹ |
| Dịch nhanh phần đang nói (speculative) | | | 3 ngày | | kênh song song, không lưu bản nháp |
| Silero VAD thay Energy VAD | | | 0.5 ngày | | `faster_whisper.vad` — chỉ khi lớp ồn |

## Release notes 0.1.0
- MVP: một nguồn âm thanh/buổi, phụ đề EN partial/final, dịch VI qua API, lưu WAV + SQLite, phục hồi crash,
  thư viện + tua theo đoạn, xuất Markdown/JSON.
- Có sẵn (nên bật sau cổng MVP): ghi chú AI có dẫn mốc, hỏi đáp theo bài với FTS5.
- Chế độ chỉ ghi âm + phiên âm/dịch sau buổi cho máy yếu.
