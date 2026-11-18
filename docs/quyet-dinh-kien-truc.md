# Quyết định kiến trúc MVP (P1.3.A.1) — MẪU, điền bằng số đo thật

> Không ghi giả định như số đo. Ô nào chưa đo để "CHƯA ĐO".

## 1. Máy thử
- CPU: … · RAM: … · GPU: … (hoặc không) · Windows: …
- Mạng khi đo: …

## 2. Âm thanh (`devices_report.json`, `test_5min.wav.report.json`)
| Nguồn | Mở được? | Đỉnh dBFS | Clip % | Rớt ≥0.5s | Nghe lại (tai người) |
|---|---|---|---|---|---|
| Micro | | | | | |
| Loopback | | | | | |

## 3. ASR (`asr_report.csv`)
| Model | RTF batch | RTF streaming | WER (nếu có ref) | Nhận xét chất lượng |
|---|---|---|---|---|
| tiny | | | | |
| base | | | | |
| small | | | | |

**Ngưỡng:** RTF streaming < 0.5 trong 10 phút (chừa chỗ cho hệ điều hành + UI).

## 4. API (`api_probe.json`, `translation_results.csv`)
- Endpoint/model xác nhận: …
- Usage do provider trả hay ước lượng: …
- 100 câu: TTFT p50 … / p90 … · tổng p50 … / p90 … · lỗi …/100 · chi phí … USD (giá …)
- Cảnh báo thuật ngữ/số/phủ định (tự động): …/100 · chấm tay: …

## 5. Ước lượng độ trễ đầu-cuối
`chốt câu (silence_to_final ≈ 0.6 s) + ASR final + hàng đợi + API` = … (ước lượng từ số trên — **chưa phải đo E2E**).

## 6. Quyết định
- [ ] **Đi tiếp** realtime với model `…`, `silence_to_final_s = …`, `context_sentences = …`
- [ ] **Sửa**: … (giảm model, rút context, giảm max_output_tokens, tắt suy luận nếu provider hỗ trợ)
- [ ] **Chuyển fallback** chỉ ghi âm + phiên âm/dịch sau buổi (xem `fallback-spec.md`)
- [ ] **Dừng** vì: …

Mục tiêu thử nghiệm giữ nguyên PRD: p50 ≤ 3 s, p90 ≤ 5 s (không hứa p90 ≤ 3 s trước khi đo E2E).
