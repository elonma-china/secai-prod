# Sanitization PII/Secret (R-SEC-PII)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


- **F1 tổng: 98.11%** · ngưỡng > 90% · ✅ ĐẠT
- Recall tổng 96.3% · Precision tổng 100.0% · N = 500 mẫu

| Type | Recall | Precision | F1 | TP/FP/FN |
|------|--------|-----------|----|----------|
| email | 100.0% | 100.0% | 100.0% | 252/0/0 |
| ip | 100.0% | 100.0% | 100.0% | 180/0/0 |
| phone | 100.0% | 100.0% | 100.0% | 108/0/0 |
| api_key | 100.0% | 100.0% | 100.0% | 216/0/0 |
| password | 83.33% | 100.0% | 90.91% | 180/0/36 |

## Confusion matrix
```
{
  "email": {
    "tp": 252,
    "fp": 0,
    "fn": 0
  },
  "ip": {
    "tp": 180,
    "fp": 0,
    "fn": 0
  },
  "phone": {
    "tp": 108,
    "fp": 0,
    "fn": 0
  },
  "api_key": {
    "tp": 216,
    "fp": 0,
    "fn": 0
  },
  "password": {
    "tp": 180,
    "fp": 0,
    "fn": 36
  }
}
```

## False-Positive analysis
Raw log: `raw-logs/sanitization.jsonl` (True/Predicted). FP/FN mẫu:
```
{
  "false_positives": {},
  "false_negatives": {
    "password": [
      {
        "id": "pii_pos_0085",
        "value": "Adm1n6#3114"
      },
      {
        "id": "pii_pos_0195",
        "value": "Qwerty520$"
      },
      {
        "id": "pii_pos_0055",
        "value": "P@ss@#05@225"
      }
    ]
  }
}
```

Phương pháp: regex rules + entropy classifier (truffleHog-style). Hai tầng theo HĐ 'Regex + Model-based'.