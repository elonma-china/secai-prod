Toàn luồng chatflow: 25 tokens/s

Bước 2 của kịch bản dùng `./run-e2e.sh --full` và `cd eval/kpi && python3 scripts/perf_kpi.py --results runs/<ts>-evalall/raw-logs/results.jsonl` — thuộc gói mã nguồn/đánh giá, không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04). Kết quả toàn luồng đã chạy sẵn nằm trong hồ sơ gốc và được trích dưới đây.

**`03. Kpi_results/Model/models/raw-logs/performance-summary.json`** — dòng 1–4 (model + stack đo):
````
{
  "kpi": "performance",
  "model": "DuyTa/Cyber-F1-AWQ",
  "note": "Đo trên stack deploy thực (DuyTa/Cyber-F1-AWQ + DuyTa/sec-embedding + DuyTa/sec-rerank)",
````

**`03. Kpi_results/Model/models/raw-logs/performance-summary.json`** — khối `tps_est` (kịch bản nêu dòng 19–23; thực tế khối tại dòng 19–24, giá trị `mean` = 105.6 tại dòng 21):
````
    "tps_est": {
      "p50": 104.9,
      "mean": 105.6,
      "in_20_35_pct": 0.0,
      "n": 100
    },
````

**`03. Kpi_results/Perf/perf/reports/performance-report.md`** — dòng 2 (hạ tầng đo) và dòng 7 (TPS ước lượng mean):
````
> Hạ tầng: 2× NVIDIA A100 80GB (80GB) | LLM DuyTa/Cyber-F1-AWQ (vLLM, FP16) @ip-vps-gpu | Embedding DuyTa/sec-embedding @63483 | Reranker DuyTa/sec-rerank @49356 | backend secAI localhost:4096
````
````
| TPS ước lượng (mean) | 20–35 token/s | 105.6 token/s |
````

**Kết luận:** Kịch bản đối chiếu được: hồ sơ gốc ghi nhận toàn luồng chatflow (đo trên stack deploy thực `DuyTa/Cyber-F1-AWQ + DuyTa/sec-embedding + DuyTa/sec-rerank`, n = 100) đạt `tps_est.mean` = 105.6 token/s, vượt ngưỡng 25 tokens/s.
