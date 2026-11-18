# Báo cáo cổng MVP (P3.3.A.2) — MẪU

## Dữ liệu
- 50 câu có bản dịch tham chiếu do người biết môn duyệt: `data/…csv` (cột `en`, `ref_vi`)
- Chạy: `python tools/translation_benchmark.py --input data/50cau.csv --limit 50 --out mvp_50.csv`
- Chấm tay cột `manual_score_0_2`: 2 = đúng ý + đúng thuật ngữ · 1 = đúng ý, sai/thiếu thuật ngữ · 0 = sai ý

## Kết quả (điền)
| Chỉ số | Ngưỡng PRD | Thực tế |
|---|---|---|
| Câu giữ ý chính & thuật ngữ (điểm 2) | ≥ 8/10 (≥ 40/50) | |
| Lỗi phủ định / số liệu | 0 nghiêm trọng | |
| Độ trễ p50 / p90 E2E (≥100 câu) | ≤ 3 s / ≤ 5 s | |
| Đoạn final có bản dịch hoặc lỗi rõ | ≥ 95% | |
| Mất segment sau kill | 0 | |
| 90 phút không treo | có | |

## Lỗi chuyên ngành hay gặp
| Thuật ngữ | Bị dịch thành | Số lần | Cách sửa (glossary/prompt) |
|---|---|---|---|

## Quyết định: GO / PIVOT / STOP
- Lý do dựa trên bảng trên: …
