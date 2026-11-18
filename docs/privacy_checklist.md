# Checklist quyền riêng tư & bảo mật (P5.1.A.1)

| # | Mục | Cách đảm bảo trong code | Kiểm tự động | Kiểm tay trên Windows |
|---|---|---|---|---|
| 1 | API key không nằm trong DB/config/log | `ai/secrets.py` (keyring → Credential Manager); `RedactingFilter` trên log handler; `AgnesAdapter.__repr__` ẩn key; httpx log ở WARNING | `test_setup_logging_redacts`, `test_config_never_contains_key`, `test_key_not_in_repr_and_logs_redacted` | Credential Manager có mục *LectureMate*; grep log không thấy key |
| 2 | Log không chứa lời giảng | Không log `en_text`/`vi_text`; chỉ log mã lỗi, số đếm | review code | mở `logs/lecturemate.log` sau 1 buổi |
| 3 | Xóa phiên xóa hết dữ liệu liên quan | `delete_session`: xóa WAV + cascade segments/translations/metrics/latency/notes + FTS trigger | `test_user_can_delete_session_with_audio` | Xóa 1 buổi, kiểm thư mục audio |
| 4 | Không xóa nhầm file ngoài thư mục app (path traversal) | `_safe_audio_file`: chỉ xóa file nằm trong `audio_root` | `test_delete_refuses_path_outside_audio_root` | — |
| 5 | File bị khóa khi xóa | đánh dấu `pending_delete`, ẩn khỏi thư viện, thử lại khi khởi động | `test_locked_file_marks_pending_cleanup` | Mở WAV bằng app khác rồi xóa |
| 6 | Người dùng biết dữ liệu nào ra Internet | Màn hình đồng ý lần đầu + dòng nhắc dưới bảng điều khiển + tab Quyền riêng tư | — | Đọc lại câu chữ |
| 7 | Luôn thấy trạng thái ghi | “● ĐANG GHI” + đồng hồ; không ghi nền khi chưa hiển thị RECORDING | `test_start_pause_stop_ui_responsive` | — |
| 8 | Xin phép ghi âm | Checkbox “chỉ ghi âm khi được phép” bắt buộc lần đầu | — | — |
| 9 | Tên file xuất an toàn | `safe_filename` loại ký tự cấm Windows | `test_safe_filename` | Tên buổi có `/:*?` |
| 10 | Bản build không chứa key | `build_windows.ps1` chặn `.env` trong thư mục build | — | Chạy script build |
| 11 | Truy vấn FTS không bị chèn cú pháp | `build_query` quote từng token | `test_build_query_escapes` | — |
| 12 | Không đồng bộ đám mây mặc định | Không có code đồng bộ | — | — |

Trước khi chia sẻ công khai: xem lại điều khoản API Agnes (lưu dữ liệu, mục đích huấn luyện), giấy phép model Whisper (MIT) / faster-whisper (MIT), chữ ký số cho exe, và quy định ghi âm của trường.
