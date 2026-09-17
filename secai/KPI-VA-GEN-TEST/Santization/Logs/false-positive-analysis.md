# Phân tích False Positive

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-06-22-003956 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


**Tổng FP (sanitization): 0.** FP = 0 → detector không gắn nhãn nhầm chuỗi vô hại (đã ghi nhận theo HĐ).


## Nhận định
- Hard-negative trong bộ test (CVE-id, version `192.168`, hash SHA-256, `changeme`) dùng để đo FP — Precision cao nghĩa là detector phân biệt tốt.
- FP tầng nội dung: agent khẳng định CVE-giả là thật → xem `raw-logs/llm-hallucination.jsonl`.