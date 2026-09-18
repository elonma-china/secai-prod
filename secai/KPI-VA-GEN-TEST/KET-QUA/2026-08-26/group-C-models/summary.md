# Bench throughput mô hình — 2026-08-26-224443
> Hạ tầng: WAN từ máy bench tới vast.ai 80.188.223.202 (KHÔNG phải đo tại chỗ trên A100 — ngưỡng gốc tính trên A100 đo local, số đo ở đây gồm cả round-trip qua internet)

| Mã | KPI | Ngưỡng | Thực đo | Kết quả |
|----|-----|--------|---------|---------|
| `R-MODEL-EMB1200` | Embedding throughput | ≥ 1200 emb/s | 366.9 emb/s | CHƯA ĐẠT |
| `R-MODEL-RERANK180` | Reranker throughput | ≥ 180 docs/s | 1640.1 docs/s | ĐẠT |
| `R-LLM-TPS28` | LLM throughput (lớp suy luận) | ≥ 28 tok/s | 88.5 tok/s | ĐẠT |

Tổng: 2/3 KPI đạt.
Artifact: raw-logs/ · reports/performance-report.md
