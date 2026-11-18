# Checklist hồi quy & phiên dài (P3.3.A.1)

Chạy tự động trước mỗi bản build: `python -m pytest -q` (phải xanh 100%).

## Thủ công trên Windows (theo 02-thiet-ke/06-chat-luong.md)
| Mã | Bài thử | Cách làm | Đạt khi | Kết quả |
|---|---|---|---|---|
| A1 | Micro/loopback | `tools/record_test.py` mỗi nguồn 5 phút, nghe lại | không méo/vỡ | |
| A2 | Đổi/rút nguồn khi đang ghi | rút tai nghe USB giữa buổi | banner lỗi rõ, buổi dừng an toàn, phần đã ghi còn | |
| S1 | Lặp/cắt câu | 10 phút bài thật, xem transcript | ≤1% câu trùng, không nhân đôi từ ở biên | |
| S2 | Timestamp | đánh dấu tay 10 mốc, click tua | lệch ≤ 1 s | |
| T1 | Thuật ngữ | 20 thuật ngữ trong glossary | ≥ 8/10 giữ đúng | |
| T2 | Mất mạng 30 s | tắt Wi-Fi giữa buổi | EN vẫn lưu, VI “chờ dịch lại”, tự bù khi có mạng | |
| T3 | 429/500/timeout | (mock đã test) hoặc giảm quota | UI không treo, không spam | |
| D1 | Kill process | Task Manager → End task giữa buổi; mở lại | hộp “Phục hồi”, mọi đoạn đã hiện “Đã lưu” còn nguyên | |
| D2 | Xóa một buổi | Thư viện → Xóa | DB + WAV biến mất, buổi khác nguyên | |
| U1 | 90 phút | `tools/benchmark_e2e.py --wav 90min.wav --stress` | không crash, RSS không tăng tuyến tính | |
| U2 | UI tiếng Việt | font, nút bấm khi AI đang chạy | không lỗi font, phản hồi < 200 ms | |

## Báo cáo phiên dài (điền từ `benchmark_e2e.md` + `stress_samples.csv`)
- File/thời lượng: … · model: … · thiết bị: …
- Segment final: … · mất sau crash: … · frame bị bỏ do ASR quá tải: …
- RSS đầu/cuối: … MB · WAV: … MB (kỳ vọng ~173 MB/90 phút)
- p50/p90 tổng: … / … (n = …)
