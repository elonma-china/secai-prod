Evaluation speed & hiệu năng suy luận: Được đo trên hạ tầng GPU NVIDIA A100 80GB, trong môi trường triển khai thực tế (chatflow đầy đủ gồm Hybrid Search, Reranking và LLM inference). Hiệu năng đạt 20–35 tokens/s, TTFT ~900–1500 ms với context tối đa 16k token. Bên cung cấp cam kết bàn giao đầy đủ evaluation code và kết quả đo thực nghiệm khi nghiệm thu.
Bộ mã đo hiệu năng — `03. Kpi_results/Perf/evaluation_code/`:
```
.env.example  README-BENCH.md  _run.sh  build_combined.py  build_perf_summary.py  config.py  llm_load.py  make_perf_bench.py  perf_bench_100.jsonl  perf_load.py  run-perf-bench.sh  scripts  tp_bench.py
```

Kết quả đo — `03. Kpi_results/Perf/perf/`:
```
KETQUA-NGHIEM-THU.md  ccu_rps.json  env.bench.before  llm35.json  perf_bench_100.jsonl  raw-logs  reports  results.jsonl  summary.md  tools
```

Trích `03. Kpi_results/Perf/perf/summary.md`:
```
## Bảng KPI (số đo thực)

| KPI | Ngưỡng hợp đồng | Kết quả đo | Điều kiện đo | Kết luận |
|-----|-----------------|------------|--------------|----------|
| TPS (token/s, mỗi luồng) | 20–35 | 55.2 (LLM thuần, 35 CCU); 133.9 (full pipeline, 10 CCU); 105.6 (perf_kpi ước lượng) | vLLM endpoint 35 CCU / full pipeline 10 CCU | Đạt (vượt ngưỡng) |
| TTFT p50 (ms) | 900–1500 | 1192 (full pipeline, 79.0% trong dải); 598 (LLM thuần 35 CCU) | full pipeline 10 CCU / vLLM 35 CCU | Đạt |
| RPS (ổn định) | ~0.6 | 0.701 (30 CCU, 90 req); 0.64 (24 CCU) | full pipeline, concurrency 24–30 | Đạt |
| CCU (người dùng đồng thời) | ~30 | 30 luồng, 90/90 OK, 0 lỗi | full pipeline, 30 luồng đồng thời | Đạt |

## Số
```

**Kết luận:** Có đủ bộ mã đo và kết quả đo hiệu năng thật trong hồ sơ gốc (đo trên 2×A100 80GB, chatflow đầy đủ).
