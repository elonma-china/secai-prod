# Báo cáo bench throughput mô hình — 2026-08-26-224443
> Hạ tầng: WAN từ máy bench tới vast.ai 80.188.223.202 (KHÔNG phải đo tại chỗ trên A100 — ngưỡng gốc tính trên A100 đo local, số đo ở đây gồm cả round-trip qua internet)
> LLM: DuyTa/Cyber-F1-AWQ @  · Embedding @  · Reranker @ 

| Mã | KPI | Ngưỡng | Thực đo | Ghi chú |
|----|-----|--------|---------|---------|
| R-MODEL-EMB1200 | Embedding throughput | ≥ 1200 emb/s | 366.9 emb/s | peak @conc 32 |
| R-MODEL-RERANK180 | Reranker throughput | ≥ 180 docs/s | 1640.1 docs/s | peak @conc 32 |
| R-LLM-TPS28 | LLM throughput (lớp suy luận) | ≥ 28 tok/s | 88.5 tok/s | TTFT 754ms |

Ghi chú: throughput mô hình đo từ máy bench (gồm độ trễ mạng). Endpoint xa → số có thể thấp; đo năng lực server thuần chạy bench tại host model.
