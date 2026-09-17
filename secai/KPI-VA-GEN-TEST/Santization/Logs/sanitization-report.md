# Sanitization PII/Secret (R-SEC-PII)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-06-13 · Commit: `cfc8599ea` · Test set: 1000 mẫu (40-40-20)


- **F1 tổng: 98.1%** · ngưỡng > 90% · ✅ ĐẠT
- Recall tổng 96.27% · Precision tổng 100.0% · N = 400 mẫu

| Type | Recall | Precision | F1 | TP/FP/FN |
|------|--------|-----------|----|----------|
| email | 100.0% | 100.0% | 100.0% | 202/0/0 |
| ip | 100.0% | 100.0% | 100.0% | 144/0/0 |
| phone | 100.0% | 100.0% | 100.0% | 86/0/0 |
| api_key | 100.0% | 100.0% | 100.0% | 173/0/0 |
| password | 83.24% | 100.0% | 90.85% | 144/0/29 |

## Confusion matrix
```
{
  "email": {
    "tp": 202,
    "fp": 0,
    "fn": 0
  },
  "ip": {
    "tp": 144,
    "fp": 0,
    "fn": 0
  },
  "phone": {
    "tp": 86,
    "fp": 0,
    "fn": 0
  },
  "api_key": {
    "tp": 173,
    "fp": 0,
    "fn": 0
  },
  "password": {
    "tp": 144,
    "fp": 0,
    "fn": 29
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
        "id": "pii_pos_0265",
        "value": "Secr3t3455"
      },
      {
        "id": "pii_pos_0135",
        "value": "P@ss45%64#"
      },
      {
        "id": "pii_pos_0225",
        "value": "Adm1n587@883"
      }
    ]
  }
}
```

Phương pháp: regex rules + entropy classifier (truffleHog-style). Hai tầng theo HĐ 'Regex + Model-based'.