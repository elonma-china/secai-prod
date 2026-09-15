Cơ chế Sanitization trước Embedding/LLM

Kịch bản trỏ tới mã nguồn `eval/kpi/sanitizer` và `detector.py`. File tương ứng trong hồ sơ gốc phần AI nằm tại `03. Kpi_results/Santization/Evaluation_code/detector.py`.

**`03. Kpi_results/Santization/Evaluation_code/detector.py`** — dòng 1–11 (cơ chế hai tầng: Regex-based rules + Model-based/entropy classification):
````
#!/usr/bin/env python3
"""detector.py — Phát hiện & làm sạch PII/Secret trước inference (R-SEC-PII).

Hai tầng (theo hợp đồng "Regex-based rules + Model-based classification"):
  1. Regex rules — bắt định dạng chuẩn: email, IP, số điện thoại, API key, password hardcoded.
  2. Entropy classifier — bắt secret/khóa lạ không khớp regex: token entropy cao trong ngữ cảnh
     gán giá trị (key/token/secret = <chuỗi ngẫu nhiên entropy cao>). Đây là tầng "model-based"
     thống kê (giống secret-scanner truffleHog/gitleaks), không phụ thuộc ML deps.

Stdlib-only. Dùng cho cả detect (eval) lẫn redact (làm sạch).
"""
````

**`03. Kpi_results/Santization/Evaluation_code/detector.py`** — dòng 127–132 (mẫu kiểm thử được insert trong hàm `main`, khớp Input của kịch bản; in ra type/source phát hiện và bản `REDACTED`):
````
if __name__ == "__main__":
    sample = ('DB_PASSWORD = "Sup3rS3cret_2024!" và liên hệ admin@corp.vn, server 192.168.10.5, '
              'hotline 0912345678, AWS AKIAIOSFODNN7EXAMPLE, api_key=sk-abcd1234efgh5678ijkl')
    for s in detect(sample):
        print(f"  {s['type']:10} [{s['source']}] {s['value']}")
    print("\nREDACTED:\n", redact(sample))
````

Kết quả chạy sẵn của cơ chế này có trong hồ sơ gốc:

**`03. Kpi_results/Model/quality/reports/sanitization-report.md`** — dòng 6–7 (F1 tổng và ngưỡng) và dòng 72 (phương pháp):
````
- **F1 tổng: 98.11%** · ngưỡng > 90% · ✅ ĐẠT
- Recall tổng 96.3% · Precision tổng 100.0% · N = 500 mẫu
````
````
Phương pháp: regex rules + entropy classifier (truffleHog-style). Hai tầng theo HĐ 'Regex + Model-based'.
````

Confusion matrix và phân tích False-Positive/False-Negative của cùng báo cáo tại dòng 17–46 và 48–70; raw logs (True/Predicted) tại `03. Kpi_results/Santization/Logs/sanitization.jsonl`.

**Kết luận:** Kịch bản đối chiếu được: cơ chế Sanitization hai tầng (Regex rules + entropy/model-based classifier) hiện diện trong mã nguồn hồ sơ (`detector.py`) với mẫu kiểm thử khớp Input; kết quả chạy sẵn ghi nhận F1 tổng 98.11% (ngưỡng > 90%) kèm confusion matrix và phân tích FP/FN.
