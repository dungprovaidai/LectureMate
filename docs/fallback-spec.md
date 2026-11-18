# Phương án dự phòng: chỉ ghi âm → phiên âm & dịch sau buổi (P1.3.B.1)

## Khi nào dùng
- Benchmark cho thấy không model ASR nào đạt RTF streaming < 0.5, hoặc
- API quá chậm/không dùng được trong buổi, hoặc máy đang chạy việc nặng khác.

## Hành vi
| Pha | Realtime (mặc định) | `record_only` |
|---|---|---|
| Trong buổi | WAV + ASR partial/final + dịch | **Chỉ WAV** (vẫn có đồng hồ, mức âm, trạng thái ĐANG GHI) |
| Sau buổi | dịch bù câu pending | Trang chi tiết → **Phiên âm từ file âm thanh** → câu final lưu DB → tự xếp hàng dịch |

Bật: *Thiết lập → Âm thanh & ASR → Chế độ*. Nếu chưa cài faster-whisper, app tự đề nghị chế độ này.

## Đảm bảo không mất bài
- WAV cập nhật header mỗi 2 giây; crash vẫn phát được tới gần thời điểm cuối; `repair_wav` sửa header khi phục hồi.
- `transcribe_file_job` **idempotent**: bỏ qua audio trước `end_ms` của segment cuối đã có → chạy lại sau khi bị ngắt chỉ thêm phần còn thiếu, không nhân đôi (test `test_record_only_then_transcribe_after`).
- Lỗi dịch không ảnh hưởng transcript tiếng Anh; câu giữ `pending/failed` để dịch lại.

## Điều kiện dừng hàng đợi dịch
- Hết câu `pending/failed/skipped` của buổi; **hoặc**
- Lỗi không retry được (`auth`, `bad_request`) → câu `failed`, dừng gửi tiếp, báo lỗi; **hoặc**
- Chạm ngân sách tháng (`budget`) → câu giữ `pending`; **hoặc**
- Mất mạng sau `max_retries` → `pending`, nghỉ `offline_cooldown_s`, tự thử lại khi có câu mới thành công.

## Đổi lại realtime khi đã khắc phục
Đổi *Chế độ* về “Trực tiếp”. Không cần chuyển đổi dữ liệu: cả hai chế độ dùng cùng schema và cùng `SegmentCommitter`.
