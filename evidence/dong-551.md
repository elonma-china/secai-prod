KPI mô hình Reranking: throughput đạt 180 rerank/s

Bước 1–2 của kịch bản (`eval/kpi/.env.bench`, `cd eval/kpi && python3 scripts/perf_kpi.py --throughput-n 200`) thuộc gói mã nguồn/đánh giá, không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04). Số đo throughput reranker đã chạy sẵn nằm trong hồ sơ gốc và được trích dưới đây.

**`03. Kpi_results/Model/models/host/throughput_rerank_host.json`** — dòng 1–5 (mode/url/model đo):
````
{
  "mode": "rerank",
  "url": "http://localhost:8002",
  "model": "DuyTa/sec-rerank",
  "levels": [
````

**`03. Kpi_results/Model/models/host/throughput_rerank_host.json`** — dòng 24–45 (mức concurrency 128 và peak):
````
    {
      "conc": 128,
      "batch": 50,
      "reqs": 160,
      "docs": 8000,
      "seconds": 1.65,
      "per_s": 4862.4,
      "ok_reqs": 160
    },
    {
      "conc": 256,
      "batch": 50,
      "reqs": 160,
      "docs": 8000,
      "seconds": 1.67,
      "per_s": 4786.0,
      "ok_reqs": 160
    }
  ],
  "peak_per_s": 4862.4,
  "peak_conc": 128
}
````

**`03. Kpi_results/Perf/perf/reports/performance-report.md`** — dòng 2 (hạ tầng đo) và dòng 10 (ngưỡng reranker):
````
> Hạ tầng: 2× NVIDIA A100 80GB (80GB) | LLM DuyTa/Cyber-F1-AWQ (vLLM, FP16) @ip-vps-gpu | Embedding DuyTa/sec-embedding @63483 | Reranker DuyTa/sec-rerank @49356 | backend secAI localhost:4096
````
````
| Reranker throughput | ~180/s (A100) | 93.8/s |
````

**Kết luận:** Kịch bản đối chiếu được: hồ sơ gốc ghi nhận reranker `DuyTa/sec-rerank` đạt `per_s` = 4862.4 rerank/s tại `conc` 128 và `peak_per_s` = 4862.4 (đo tại host), vượt ngưỡng 180 rerank/s; hạ tầng đo là 2× NVIDIA A100 80GB theo dòng 2 của performance-report.md.
