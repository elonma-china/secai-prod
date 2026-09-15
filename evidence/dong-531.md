Diện tích bao phủ Radar > 90% so với KPI cam kết

Ghi chú phạm vi: các script sinh báo cáo (`scripts/build_reports.py`, `make_deliverables.py`) thuộc gói mã nguồn/triển khai, KHÔNG nằm trong hồ sơ gốc phần AI. Sản phẩm đầu ra của chúng có thật trong hồ sơ: `radar-data.json` và `radar.svg` có mặt; `radar.png` KHÔNG có (thiếu module `matplotlib` — xem `reports/radar-error.txt`: `ModuleNotFoundError("No module named 'matplotlib'")`).

**`03. Kpi_results/Model/quality/reports/radar-data.json`** — dòng 1–23:
```
{
  "axes": [
    "Faithfulness",
    "Relevance",
    "Entity Precision",
    "Logic/Reasoning",
    "Safety/Hallucination"
  ],
  "actual": [
    98.48,
    92.88,
    100.0,
    91.9,
    99.2
  ],
  "committed": [
    98,
    90,
    100,
    80,
    98
  ],
  "coverage_area_pct": 103.9
}
```

**`03. Kpi_results/Model/quality/reports/radar.svg`** — dòng 8–21, 24 (nhãn 5 trục + giá trị thực đo + diện tích phủ):
```
<text x="260.0" y="56.0" text-anchor="middle" font-size="11">Faithfulness</text>
<text x="260.0" y="69.0" text-anchor="middle" font-size="10" fill="#c00">98.5%</text>
...
<text x="454.0" y="197.0" text-anchor="start" font-size="11">Relevance</text>
<text x="454.0" y="210.0" text-anchor="start" font-size="10" fill="#c00">92.9%</text>
...
<text x="379.9" y="425.0" text-anchor="start" font-size="11">Entity Precision</text>
<text x="379.9" y="438.0" text-anchor="start" font-size="10" fill="#c00">100.0%</text>
...
<text x="140.1" y="425.0" text-anchor="end" font-size="11">Logic/Reasoning</text>
<text x="140.1" y="438.0" text-anchor="end" font-size="10" fill="#c00">91.9%</text>
...
<text x="66.0" y="197.0" text-anchor="end" font-size="11">Safety/Hallucination</text>
<text x="66.0" y="210.0" text-anchor="end" font-size="10" fill="#c00">99.2%</text>
...
<text x="260" y="510" text-anchor="middle" font-size="12">Diện tích phủ: 103.9% · <tspan fill="#1d4ed8">đặc=thực đo</tspan> · <tspan fill="#888">nét đứt=cam kết</tspan></text>
```

**`03. Kpi_results/Model/quality/reports/ai-task-kpi-report.md`** — dòng 6 và dòng 21:
```
**Tổng quan: 11/11 KPI đạt.** Radar coverage: 103.9%.
```
```
| `R-KPI-RADAR90` | Radar coverage area | > 90% | 103.9% | ✅ ĐẠT |
```

**`03. Kpi_results/Model/quality/summary.md`** — dòng 27:
```
| `R-KPI-RADAR90` | Radar coverage area | > 90% | 103.9% | ✅ ĐẠT |
```

**`03. Kpi_results/Model/quality/run_eval_all.log`** — dòng 170, 173, 208:
```
  R-KPI-RADAR90          103.9%  thr > 90    ✅ ĐẠT
```
```
  ✓ runs/2026-06-16-003956-evalall/reports/radar.svg
```
```
  R-KPI-RADAR90         103.9 (thr > 90) PASS
```

**Kết luận:** Đối chiếu được: `radar-data.json` liệt kê đủ 5 trục (Faithfulness, Relevance, Entity Precision, Logic/Reasoning, Safety/Hallucination) với thực đo 98,48/92,88/100,0/91,9/99,2% và `coverage_area_pct` = 103,9% (> 90%); `radar.svg`, `ai-task-kpi-report.md` (dòng 6, 21), `summary.md` (dòng 27) và `run_eval_all.log` (dòng 170, 208) đều xác nhận R-KPI-RADAR90 ĐẠT; script sinh biểu đồ và `radar.png` không có trong hồ sơ gốc (chỉ có `radar.svg`).
