KPI Sanitization Email Address

Bước 1–2 của kịch bản (`sanitizer/gen_pii_dataset.py --n 500`, `scripts/sanitization_kpi.py`) thuộc gói mã nguồn/đánh giá; bản tương ứng trong hồ sơ gốc nằm tại `03. Kpi_results/Santization/Evaluation_code/` và dataset tại `03. Kpi_results/Santization/Dataset/sanitization_pii.jsonl`. Kết quả đã chạy sẵn được trích dưới đây.

**`03. Kpi_results/Santization/Logs/sanitization-summary.json`** — khối `per_type` và `overall` (kịch bản nêu email dòng 5–12, IP 14–21, phone 23–30, api_key 32–39, password 41–48, F1 tổng 51–58; thực tế các khối tại dòng 4–59):
````
  "per_type": {
    "email": {
      "recall": 100.0,
      "precision": 100.0,
      "f1": 100.0,
      "tp": 252,
      "fp": 0,
      "fn": 0,
      "pass": true
    },
    "ip": {
      "recall": 100.0,
      "precision": 100.0,
      "f1": 100.0,
      "tp": 180,
      "fp": 0,
      "fn": 0,
      "pass": true
    },
    "phone": {
      "recall": 100.0,
      "precision": 100.0,
      "f1": 100.0,
      "tp": 108,
      "fp": 0,
      "fn": 0,
      "pass": true
    },
    "api_key": {
      "recall": 100.0,
      "precision": 100.0,
      "f1": 100.0,
      "tp": 216,
      "fp": 0,
      "fn": 0,
      "pass": true
    },
    "password": {
      "recall": 83.33,
      "precision": 100.0,
      "f1": 90.91,
      "tp": 180,
      "fp": 0,
      "fn": 36,
      "pass": true
    }
  },
  "overall": {
    "recall": 96.3,
    "precision": 100.0,
    "f1": 98.11,
    "tp": 936,
    "fp": 0,
    "fn": 36,
    "f1_pass": true
  },
````

**`03. Kpi_results/Model/quality/reports/sanitization-report.md`** — dòng 9–15 (bảng Recall/Precision/F1 theo từng loại):
````
| Type | Recall | Precision | F1 | TP/FP/FN |
|------|--------|-----------|----|----------|
| email | 100.0% | 100.0% | 100.0% | 252/0/0 |
| ip | 100.0% | 100.0% | 100.0% | 180/0/0 |
| phone | 100.0% | 100.0% | 100.0% | 108/0/0 |
| api_key | 100.0% | 100.0% | 100.0% | 216/0/0 |
| password | 83.33% | 100.0% | 90.91% | 180/0/36 |
````

**Kết luận:** Kịch bản đối chiếu được: Email/IP/Phone/API Key đạt Recall 100% & Precision 100%; Password đạt Recall 83.33% & Precision 100%; F1 tổng 98.11% — tất cả vượt ngưỡng cam kết tương ứng (Email/IP Recall ≥98% Precision ≥95%; Phone Recall ≥95% Precision ≥90%; API Key Recall ≥85% Precision ≥80%; Password Recall ≥80% Precision ≥75%; F1 tổng ≥90%).
