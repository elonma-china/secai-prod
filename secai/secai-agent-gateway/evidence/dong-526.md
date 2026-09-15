Faithfulness (RAGAS/NLI) > 98%, Answer Relevance > 90%, Entity Precision = 100%, Hallucination < 2%

Ghi chú phạm vi: các script sinh đáp án (`run_eval_all.sh`, `bench_answers_concurrent.py`, `gen_answers.py`) thuộc gói mã nguồn/triển khai, KHÔNG nằm trong hồ sơ gốc phần AI. Công cụ đo faithfulness `faith_judge.py` và `llm_kpi.py` có bản trong hồ sơ tại `03. Kpi_results/Model/quality/evaluation-code/`. KẾT QUẢ các lần chạy có thật trong `03. Kpi_results/` và được trích dưới đây.

**`03. Kpi_results/Model/quality/reports/llm-kpi-report.md`** — dòng 6–20:
```
| Mã | KPI | Ngưỡng | Thực đo | Kết quả |
|----|-----|--------|---------|---------|
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98% | 98.48% | ✅ ĐẠT |
| `R-KPI-RELEV90` | Answer Relevance (LLM-judge) | > 90% | 92.88% | ✅ ĐẠT |
| `R-KPI-ENTITY100` | Entity Precision CVE/CWE | = 100% | 100.0% | ✅ ĐẠT |
| `R-KPI-HALLU2` | Hallucination Rate | < 2% | 0.8% | ✅ ĐẠT |
| `R-KPI-REASONING` | Reasoning từng bước | — | 91.9% | ⏳ chưa đo |

Raw logs: `llm-faithfulness.jsonl`, `llm-answer-relevance.jsonl`, `llm-entity-precision.jsonl`, `llm-hallucination.jsonl` (True/Predicted/Score).

## Phương pháp
- Faithfulness: RAGAS (LLM judge) trên (question, answer, contexts truy hồi).
- Answer Relevance: cosine(embed(answer), embed(question)) — DuyTa/sec-embedding.
- Entity Precision: regex CVE/CWE, mỗi id đối chiếu KB Qdrant (grounded).
- Hallucination: id bịa (không có trong KB) hoặc khẳng định CVE-giả (trap) là thật.
```

**`03. Kpi_results/Model/quality/raw-logs/faithfulness-summary.json`** — dòng 1–6:
```
{
  "mean_faithfulness_pct": 98.48,
  "n": 248,
  "judge": "DuyTa/Cyber-F1-AWQ",
  "method": "direct-judge (RAGAS-style)"
}
```

**`03. Kpi_results/Model/quality/raw-logs/kpi-summary.json`** — dòng 16–43 (bốn KPI LLM):
```
  "R-KPI-FAITH98": {
    "name": "Faithfulness (RAGAS)",
    "unit": "%",
    "thr": "> 98",
    "value": 98.48,
    "pass": true
  },
  "R-KPI-RELEV90": {
    "name": "Answer Relevance (LLM-judge)",
    "unit": "%",
    "thr": "> 90",
    "value": 92.88,
    "pass": true
  },
  "R-KPI-ENTITY100": {
    "name": "Entity Precision CVE/CWE",
    "unit": "%",
    "thr": "= 100",
    "value": 100.0,
    "pass": true
  },
  "R-KPI-HALLU2": {
    "name": "Hallucination Rate",
    "unit": "%",
    "thr": "< 2",
    "value": 0.8,
    "pass": true
  },
```

**`03. Kpi_results/Model/quality/raw-logs/llm-kpi-summary.json`** — dòng 1–21:
```
{
  "R-KPI-ENTITY100": {
    "mean_precision_pct": 100.0,
    "perfect_samples_pct": 100.0,
    "n": 965
  },
  "R-KPI-HALLU2": {
    "hallucination_rate_pct": 0.8,
    "n": 1000,
    "halluc": 8
  },
  "R-KPI-REASONING": {
    "reasoning_present_pct": 91.9,
    "n": 1000
  },
  "R-KPI-RELEV90": {
    "mean_relevance_pct": 92.88,
    "n": 250,
    "method": "llm-judge"
  }
}
```

**`03. Kpi_results/Model/quality/summary.md`** — dòng 19–22:
```
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98% | 98.48% | ✅ ĐẠT |
| `R-KPI-RELEV90` | Answer Relevance (LLM-judge) | > 90% | 92.88% | ✅ ĐẠT |
| `R-KPI-ENTITY100` | Entity Precision CVE/CWE | = 100% | 100.0% | ✅ ĐẠT |
| `R-KPI-HALLU2` | Hallucination Rate | < 2% | 0.8% | ✅ ĐẠT |
```

Lưu ý số liệu thực tế: `faithfulness-summary.json` ghi `judge` = `DuyTa/Cyber-F1-AWQ`, `n` = 248 (không phải "deepseek-v4-flash"); các bản ghi chi tiết từng mẫu tại `03. Kpi_results/Model/quality/raw-logs/llm-faithfulness.jsonl` (250 dòng), `llm-answer-relevance.jsonl` (250 dòng), `llm-entity-precision.jsonl` (965 dòng), `llm-hallucination.jsonl` (1000 dòng).

**Kết luận:** Đối chiếu được: kết quả đã chạy cho Faithfulness = 98,48% (> 98%, pass=true, judge `DuyTa/Cyber-F1-AWQ`, n=248), Answer Relevance = 92,88%, Entity Precision = 100%, Hallucination = 0,8% — cả 4 KPI LLM đều ĐẠT; script sinh đáp án không nằm trong hồ sơ gốc phần AI nên chỉ đối chiếu qua kết quả.
