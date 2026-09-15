Faithfulness (RAGAS/NLI) > 98%, Answer Relevance > 90%, Entity Precision = 100%, Hallucination < 2%

Ghi chú phạm vi: các script/lệnh sinh đáp án ở kịch bản (`run_eval_all.sh`, `bench_answers_concurrent.py`, `gen_answers.py`) thuộc gói mã nguồn/triển khai, KHÔNG nằm trong hồ sơ gốc phần AI (4 thư mục 01–04). Công cụ đo `scripts/llm_kpi.py` và `faith_judge.py` có bản trong hồ sơ tại `03. Kpi_results/Model/quality/evaluation-code/`. KẾT QUẢ của các lần chạy này có thật trong `03. Kpi_results/` và được trích dưới đây.

**`03. Kpi_results/Model/quality/raw-logs/retrieval-summary.json`** — dòng 1–12:
```
{
  "kpi": "retrieval",
  "mode": "hybrid (dense+BM25+RRF) + id-aware exact cve_id filter (khớp product qdrant_search)",
  "model_embed": "DuyTa/sec-embedding",
  "model_rerank": "DuyTa/sec-rerank",
  "n": 735,
  "errors": 0,
  "hit_rate_at_10_pct": 98.78,
  "hit_rate_at_10_hybrid_pct": 80.14,
  "hit_rate_at_10_dense_only_pct": 30.2,
  "precision_at_1_pct": 98.23
}
```
(Trường `model_embed` = `DuyTa/sec-embedding` và `model_rerank` = `DuyTa/sec-rerank` tại dòng 4–5, đúng như kịch bản; `n`=735, `errors`=0 tại dòng 6–7.)

**`03. Kpi_results/Model/quality/reports/embedding-report.md`** — dòng 1–10:
```
# Embedding Model — Hit Rate@10 (R-KPI-EMB96)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-06-22-003956 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


- **Hit Rate@10 (hybrid dense+BM25): 98.78%** · ngưỡng > 96% · ✅ ĐẠT
- Dense-only (embedding thuần): 30.2%
- Mode: hybrid (dense+BM25+RRF) + id-aware exact cve_id filter (khớp product qdrant_search) · N = 735 · model DuyTa/sec-embedding

Raw log: `raw-logs/embedding-hit-rate-at-10.jsonl` (True=gold CVE, Predicted=top-10 CVE, Score=1/0).
```

**`03. Kpi_results/Model/quality/reports/reranking-report.md`** — dòng 1–9:
```
# Reranking Model — Precision@1 (R-KPI-RERANK90)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-06-22-003956 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


- **Precision@1: 98.23%** · ngưỡng > 90% · ✅ ĐẠT
- N = 735 · model DuyTa/sec-rerank

Raw log: `raw-logs/reranking-precision-at-1.jsonl` (True=gold CVE, Predicted=top-1 sau rerank, Score=1/0).
```

**`03. Kpi_results/Model/quality/raw-logs/kpi-summary.json`** — dòng 2–15:
```
  "R-KPI-EMB96": {
    "name": "Embedding Hit Rate@10",
    "unit": "%",
    "thr": "> 96",
    "value": 98.78,
    "pass": true
  },
  "R-KPI-RERANK90": {
    "name": "Reranking Precision@1",
    "unit": "%",
    "thr": "> 90",
    "value": 98.23,
    "pass": true
  },
```

**`03. Kpi_results/Model/quality/summary.md`** — dòng 17–18:
```
| `R-KPI-EMB96` | Embedding Hit Rate@10 | > 96% | 98.78% | ✅ ĐẠT |
| `R-KPI-RERANK90` | Reranking Precision@1 | > 90% | 98.23% | ✅ ĐẠT |
```

**`03. Kpi_results/Model/quality/run_eval_all.log`** — dòng 160–161:
```
  R-KPI-EMB96            98.78%  thr > 96    ✅ ĐẠT
  R-KPI-RERANK90         98.23%  thr > 90    ✅ ĐẠT
```

**Kết luận:** Đối chiếu được: kết quả đã chạy trong `03. Kpi_results/Model/quality/` cho Embedding Hit Rate@10 = 98,78% (> 96%, pass=true) và Reranking Precision@1 = 98,23% (> 90%, pass=true), model `DuyTa/sec-embedding` / `DuyTa/sec-rerank`, n=735, errors=0; các script sinh đáp án (`run_eval_all.sh`, `bench_answers_concurrent.py`, `gen_answers.py`) không nằm trong hồ sơ gốc phần AI nên chỉ đối chiếu qua kết quả.
