Tỷ lệ phát hiện & phân loại chính xác ≥ 85%
**`03. Kpi_results/Model/quality/reports/ai-task-kpi-report.md`** — dòng 17:
```
| `R-LLM-SAST85` | SAST accuracy | ≥ 85% | 85.0% | ✅ ĐẠT |
```

**`03. Kpi_results/Model/quality/raw-logs/task-summary.json`** — dòng 2–8:
```json
  "R-LLM-SAST85": {
    "capability": "sast",
    "n": 60,
    "pass": 51,
    "accuracy_pct": 85.0,
    "threshold": 85,
    "passed": true
```

**`03. Kpi_results/Model/quality/run_eval_all.log`** — dòng 56:
```
  sast         51/60 = 85.0%  (ngưỡng ≥85%) ✓ [majority 3 lượt]  → sast.jsonl
```

**`03. Kpi_results/kpi-summary.json`** — dòng 50–56:
```json
  "R-LLM-SAST85": {
    "name": "SAST accuracy",
    "unit": "%",
    "thr": "≥ 85",
    "value": 85.0,
    "pass": true,
    "group": "quality"
```

**Kết luận:** Đối chiếu được: `R-LLM-SAST85` (SAST) trong hồ sơ gốc ghi 51/60 = 85.0%, ngưỡng ≥85%, passed=true — khớp tại các file và khoảng dòng nêu trên.
