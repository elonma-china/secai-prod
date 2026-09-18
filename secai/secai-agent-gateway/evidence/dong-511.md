Tỷ lệ đề xuất bản vá hợp lệ ≥ 60%
**`03. Kpi_results/Model/quality/reports/ai-task-kpi-report.md`** — dòng 19:
```
| `R-LLM-REMED60` | Auto-remediation | ≥ 60% | 100.0% | ✅ ĐẠT |
```

**`03. Kpi_results/Model/quality/raw-logs/task-summary.json`** — dòng 18–24:
```json
  "R-LLM-REMED60": {
    "capability": "remediation",
    "n": 60,
    "pass": 60,
    "accuracy_pct": 100.0,
    "threshold": 60,
    "passed": true
```

**`03. Kpi_results/Model/quality/run_eval_all.log`** — dòng 58:
```
  remediation  60/60 = 100.0%  (ngưỡng ≥60%) ✓  → auto-remediation.jsonl
```

**`03. Kpi_results/kpi-summary.json`** — dòng 66–72:
```json
  "R-LLM-REMED60": {
    "name": "Auto-remediation",
    "unit": "%",
    "thr": "≥ 60",
    "value": 100.0,
    "pass": true,
    "group": "quality"
```

**Kết luận:** Đối chiếu được: `R-LLM-REMED60` (Auto-remediation) trong hồ sơ gốc ghi 60/60 = 100.0%, ngưỡng ≥60%, passed=true — khớp tại các file và khoảng dòng nêu trên.
