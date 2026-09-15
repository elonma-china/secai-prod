Tỷ lệ giải thích đúng logic & đầy đủ ≥ 85%
**`03. Kpi_results/Model/quality/reports/ai-task-kpi-report.md`** — dòng 18:
```
| `R-LLM-EXPLOIT85` | Exploit Explanation | ≥ 85% | 100.0% | ✅ ĐẠT |
```

**`03. Kpi_results/Model/quality/raw-logs/task-summary.json`** — dòng 10–16:
```json
  "R-LLM-EXPLOIT85": {
    "capability": "exploit",
    "n": 60,
    "pass": 60,
    "accuracy_pct": 100.0,
    "threshold": 85,
    "passed": true
```

**`03. Kpi_results/Model/quality/run_eval_all.log`** — dòng 57:
```
  exploit      60/60 = 100.0%  (ngưỡng ≥85%) ✓  → exploit-explanation.jsonl
```

**`03. Kpi_results/kpi-summary.json`** — dòng 58–64:
```json
  "R-LLM-EXPLOIT85": {
    "name": "Exploit Explanation",
    "unit": "%",
    "thr": "≥ 85",
    "value": 100.0,
    "pass": true,
    "group": "quality"
```

**Kết luận:** Đối chiếu được: `R-LLM-EXPLOIT85` (Exploit Explanation) trong hồ sơ gốc ghi 60/60 = 100.0%, ngưỡng ≥85%, passed=true — khớp tại các file và khoảng dòng nêu trên.
