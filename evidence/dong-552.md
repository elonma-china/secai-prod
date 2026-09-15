KPI mô hình LLM: throughput đạt 28 tokens/s

Bước 1–2 của kịch bản (`eval/kpi/.env.bench`, `cd eval/kpi && python3 scripts/perf_kpi.py --results raw-logs/results.jsonl`) thuộc gói mã nguồn/đánh giá, không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04). Số đo TPS lớp LLM đã chạy sẵn nằm trong hồ sơ gốc và được trích dưới đây.

**`03. Kpi_results/Perf/perf/llm35.json`** — toàn file (dòng 1–17); lớp đo và CCU tại dòng 2–3, TPS/stream trung vị (p50) tại dòng 14:
````
{
  "layer": "inference (vLLM endpoint)",
  "ccu": 35,
  "total": 105,
  "ok": 105,
  "errors": 0,
  "wall_s": 15.6,
  "aggregate_tok_s": 1412.4,
  "rps": 6.751,
  "tokens_per_req_mean": 209,
  "ttft_ms_p50": 598,
  "ttft_ms_p95": 845,
  "ttft_in_900_1500_pct": 2.9,
  "tps_per_stream_p50": 55.2,
  "tps_per_stream_mean": 54.7,
  "tps_in_20_35_pct": 1.0,
  "err_samples": []
````

**`03. Kpi_results/Perf/perf/reports/performance-report.md`** — dòng 2 (hạ tầng đo) và dòng 12 (TPS lớp suy luận thuần, 35 CCU):
````
> Hạ tầng: 2× NVIDIA A100 80GB (80GB) | LLM DuyTa/Cyber-F1-AWQ (vLLM, FP16) @ip-vps-gpu | Embedding DuyTa/sec-embedding @63483 | Reranker DuyTa/sec-rerank @49356 | backend secAI localhost:4096
````
````
TPS lớp suy luận thuần (vLLM, 35 CCU): per-stream p50 = 55.2 token/s; tổng = 1412.4 token/s; TTFT p50 = 598 ms.
````

**Kết luận:** Kịch bản đối chiếu được: hồ sơ gốc ghi nhận LLM (`layer": "inference (vLLM endpoint)"`) chạy với `ccu` = 35 và `tps_per_stream_p50` = 55.2 token/s (tổng `aggregate_tok_s` = 1412.4), vượt ngưỡng 28 tokens/s; hạ tầng đo 2× NVIDIA A100 80GB theo dòng 2 của performance-report.md.
