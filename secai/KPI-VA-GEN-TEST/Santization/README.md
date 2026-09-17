# Sanitization KPI (R-SEC-PII)

## Chạy đánh giá

```bash
cd Evaluation_code
python3 sanitization_kpi.py
```

Tùy chọn dataset/output khác:

```bash
python3 sanitization_kpi.py --dataset ../Dataset/sanitization_pii.jsonl --out ../Logs
```

## Sinh lại Dataset (tùy chọn)

```bash
cd Evaluation_code
python3 gen_pii_dataset.py --n 500 --seed 42
```

## Kết quả

| Nội dung | Vị trí |
|---|---|
| Bảng KPI (recall/precision/F1 theo console) | in ra terminal khi chạy |
| Raw log (true/predicted từng mẫu) | `Logs/sanitization.jsonl` |
| Tổng hợp số liệu + confusion matrix + FP/FN | `Logs/sanitization-summary.json` |
| Báo cáo markdown | `Logs/sanitization-report.md` |
