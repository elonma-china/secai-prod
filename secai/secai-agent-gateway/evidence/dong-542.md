- Hiệu năng đạt 20–35 tokens/giây (TPS), - TTFT 900–1500 ms trên hạ tầng suy luận.

**`03. Kpi_results/Perf/perf/reports/performance-report.md`** — dòng 1–12 (toàn file):

```
# Hiệu năng suy luận (perf_kpi derived) — 2026-06-22
> Hạ tầng: 2× NVIDIA A100 80GB (80GB) | LLM DuyTa/Cyber-F1-AWQ (vLLM, FP16) @ip-vps-gpu | Embedding DuyTa/sec-embedding @63483 | Reranker DuyTa/sec-rerank @49356 | backend secAI localhost:4096

| Chỉ tiêu | Ngưỡng | Thực đo |
|----------|--------|---------|
| TTFT p50 | 900–1500 ms | 1192 ms (trong dải: 79.0%) |
| TPS ước lượng (mean) | 20–35 token/s | 105.6 token/s |
| Thời gian phản hồi p50 | ≤15 s | 13751 ms (≤15s: 71.0%) |
| Embedding throughput | ~1200/s (A100) | 4.6/s |
| Reranker throughput | ~180/s (A100) | 93.8/s |

TPS lớp suy luận thuần (vLLM, 35 CCU): per-stream p50 = 55.2 token/s; tổng = 1412.4 token/s; TTFT p50 = 598 ms.
```

**`03. Kpi_results/Perf/perf/raw-logs/performance-summary.json`** — dòng 1–24 (khối `derived` mà kịch bản nêu là dòng 5–24):

```
{
  "kpi": "performance",
  "model": "DuyTa/Cyber-F1-AWQ",
  "note": "Đo trên stack deploy thực (DuyTa/Cyber-F1-AWQ + DuyTa/sec-embedding + DuyTa/sec-rerank)",
  "derived": {
    "n": 100,
    "ttft_ms": {
      "p50": 1192,
      "p95": 2322,
      "mean": 1343,
      "in_900_1500_pct": 79.0
    },
    "response_ms": {
      "p50": 13751,
      "p95": 18726,
      "mean": 14092,
      "pct_le_15s": 71.0
    },
    "tps_est": {
      "p50": 104.9,
      "mean": 105.6,
      "in_20_35_pct": 0.0,
      "n": 100
    },
```

**Kết luận:** Đối chiếu được: report và summary khớp nhau — hạ tầng 2× NVIDIA A100 80GB (dòng 2), TTFT p50 = 1192 ms (79.0% trong dải 900–1500 ms), TPS ước lượng mean = 105.6 token/s, TPS lớp suy luận LLM thuần per-stream p50 = 55.2 token/s (dòng 12).
