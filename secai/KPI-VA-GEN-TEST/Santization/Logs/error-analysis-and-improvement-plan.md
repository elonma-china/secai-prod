# Sanitization — Phân tích lỗi & kế hoạch cải thiện

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-06-22-003956 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


- **F1 tổng: 98.11%** (ngưỡng > 90%) · Recall 96.3% · Precision 100.0% · N = 500 mẫu


| Type | Recall | Precision | F1 | TP/FP/FN | Đạt |
|------|--------|-----------|----|----------|-----|
| email | 100.0% | 100.0% | 100.0% | 252/0/0 | ✅ |
| ip | 100.0% | 100.0% | 100.0% | 180/0/0 | ✅ |
| phone | 100.0% | 100.0% | 100.0% | 108/0/0 | ✅ |
| api_key | 100.0% | 100.0% | 100.0% | 216/0/0 | ✅ |
| password | 83.33% | 100.0% | 90.91% | 180/0/36 | ✅ |

## Kế hoạch cải thiện
- Loại có Recall thấp (thường `password`): bổ sung mẫu sinh đa dạng + nới rule pattern mật khẩu.
- Loại có Precision thấp: thêm hard-negative (chuỗi giống PII nhưng không phải) để giảm False-Positive.
- Hai tầng Regex + entropy/model-based theo HĐ; tinh chỉnh ngưỡng entropy per-type.

Chi tiết FN/FP: `false-negative-analysis.md`, `false-positive-analysis.md`. Raw log: `raw-logs/sanitization.jsonl`.