# LectureMate AI — bản portable cho Windows 10/11 x64

## Chạy
1. Giải nén `LectureMate-portable-win64.zip` vào thư mục bất kỳ (đường dẫn có dấu tiếng Việt vẫn được).
2. Mở `LectureMate\LectureMate.exe`. Windows Defender/SmartScreen có thể cảnh báo vì exe chưa ký số → *More info → Run anyway* (chỉ khi bạn tự build hoặc tin nguồn).
3. Lần đầu: đọc và đồng ý màn hình quyền riêng tư.

## Thiết lập lần đầu
1. **Nguồn âm thanh:** Microphone (học tại lớp) hoặc System Audio (lớp online, WASAPI loopback). Với Microphone, lựa chọn mặc định ưu tiên micro vật lý thay vì Stereo Mix. Bấm **Thử 5 giây**, im 1 giây để đo nền rồi nói khi app nhắc. Bản thử không được lưu.
   - Nếu micro không hoạt động: *Settings → Privacy & security → Microphone* → bật cho ứng dụng desktop.
   - **Hỗ trợ mic yếu** bật sẵn trong *Thiết lập → Âm thanh & ASR*. Chế độ này hỗ trợ phụ đề trực tiếp và khi phiên âm WAV từ mic; không thay đổi WAV gốc. Nếu giọng lẫn vào tiếng nền, thử đưa mic gần hơn hoặc dùng mic tai nghe.
2. **Model ASR:** lần đầu bắt đầu buổi học, model Faster-Whisper được tải về `%LOCALAPPDATA%\LectureMate\models` (cần mạng, `base` ~150 MB). Có thể đặt sẵn model trong thư mục `models` cạnh exe nếu build với `-WithModel`.
3. **API dịch:** *Thiết lập → API dịch* → nhập Base URL, Model, API key → **Thử kết nối**. Key được lưu trong Windows Credential Manager (*Control Panel → Credential Manager → Windows Credentials → LectureMate*).
   - Chưa có key vẫn ghi âm và có phụ đề tiếng Anh; bấm *Dịch lại các đoạn còn thiếu* sau khi có key.
4. **Nhịp dịch và tóm tắt:** *Thiết lập → Âm thanh & ASR → Nhịp dịch* chọn **Nhanh** (nhiều lượt API hơn)
   hoặc **Tự nhiên**. Bật/tắt tự tạo tóm tắt khi kết thúc trong *Thiết lập → API dịch*.
5. **Thuật ngữ:** *Thiết lập → Thuật ngữ* → thêm môn → nhập bảng EN | VI (hoặc *Nhập CSV*).

## Phím tắt
| Phím | Việc |
|---|---|
| Ctrl+R | Bắt đầu / kết thúc (có xác nhận) |
| Ctrl+P | Tạm dừng / tiếp tục |
| Ctrl+F | Tìm trong phiên |
| Ctrl+L | Thư viện |
| Space | Phát/tạm dừng (trang chi tiết) |

## Dữ liệu ở đâu
- `%LOCALAPPDATA%\LectureMate\lecturemate.db` — SQLite (transcript, bản dịch, ghi chú, số đo)
- `%LOCALAPPDATA%\LectureMate\audio\*.wav` — âm thanh (~115 MB/giờ)
- `%LOCALAPPDATA%\LectureMate\logs\` — log (không chứa lời giảng hay API key)
- Gỡ hoàn toàn: xóa thư mục ứng dụng + thư mục trên + mục *LectureMate* trong Credential Manager.

## Máy yếu / không theo kịp
- Chọn model `tiny` hoặc `base` trong Thiết lập.
- Hoặc chế độ **Chỉ ghi âm — phiên âm & dịch sau buổi**: trong buổi chỉ ghi WAV; sau buổi mở trang chi tiết → *Phiên âm từ file âm thanh*.

## Kiểm thử phát hành (checklist)
- [ ] Máy Windows x64 sạch, không cài Python: exe mở được; `LectureMate.exe --smoke` mở cửa sổ ~2 giây rồi tự đóng (mã thoát 0: `echo %ERRORLEVEL%`).
- [ ] Micro và loopback đều ghi được (Thử 5 giây có tín hiệu).
- [ ] Firewall/SSL: Thử kết nối API thành công trên mạng trường/ký túc xá.
- [ ] Thư mục giải nén có dấu tiếng Việt / khoảng trắng.
- [ ] Ổ đĩa gần đầy: ứng dụng báo lỗi rõ, không crash.
- [ ] Không có file `.env` hay API key trong thư mục build.
