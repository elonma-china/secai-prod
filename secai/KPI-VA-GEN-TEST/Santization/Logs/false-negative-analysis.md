# Phân tích False Negative

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-06-22-003956 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


**Tổng FN (sanitization): 36.** FN = PII/secret có thật nhưng detector bỏ sót → rủi ro lộ dữ liệu.


| Type | FN count | Mẫu bỏ sót (rút gọn) |
|------|----------|----------------------|
| email | 0 | — |
| ip | 0 | — |
| phone | 0 | — |
| api_key | 0 | — |
| password | 36 | "Qwerty04@72@6"; "Sup3r%@@0#9"; "Secr3t4531" |

## Nhận định
- FN tập trung ở `password` (chuỗi mật khẩu đa dạng, khó bắt bằng regex thuần) — kế hoạch xử lý ở `error-analysis-and-improvement-plan.md`.
- Retrieval 'miss' (gold không vào top-10) là FN tầng truy hồi — xem `raw-logs/embedding-hit-rate-at-10.jsonl` (score=0) và `embedding-report.md`.