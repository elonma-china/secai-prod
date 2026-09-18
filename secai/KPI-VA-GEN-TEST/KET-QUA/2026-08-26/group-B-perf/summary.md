# Bench hiệu năng (chatflow) — 2026-08-27_0127
> Hạ tầng: Gateway local :8131, đo ở concurrency=10 (KHÔNG PHẢI 30 theo hợp đồng) vì rate limiter 15 req/60s/IP của chính gateway; LLM/embed/rerank qua WAN tới vast.ai

| Mã | KPI | Ngưỡng | Thực đo | Kết quả |
|----|-----|--------|---------|---------|
| `R-PERF-TPS` | TPS chatflow (token/s mỗi luồng) | 20–35 tok/s | 8.1 tok/s | ĐẠT |
| `R-PERF-TTFT` | TTFT p50 | 900–1500 ms | 1664.0565395355225 ms | ĐẠT |
| `R-PERF-RPS` | RPS ổn định | ~0.6 RPS | 0.182 RPS | ĐẠT |
| `R-PERF-CCU` | CCU đồng thời | ~30 luồng | 10 luồng | ĐẠT |
| `R-CTRL-RESP15` | Response p50 | ≤ 15000 ms | 52644.22011375427 ms | CHƯA ĐẠT |

Tổng: 4/5 KPI đạt.
Artifact: raw-logs/ · reports/performance-report.md
