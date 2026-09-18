# Hiệu năng suy luận (R-PERF-*, R-CTRL-RESP15)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


> Đo trên stack deploy thực (deepseek cloud + bge endpoint), KHÔNG phải 3×A100/Qwen on-prem


| Chỉ tiêu | Ngưỡng | Thực đo |
|----------|--------|---------|
| TTFT (p50) | 900–1500 ms | 6159 ms (trong dải: 0.7%) |
| TPS ước lượng (mean) | 20–35 tok/s | 76.8 tok/s |
| Response time (p50) | ≤ 15 s | 12893 ms (≤15s: 56.4%) |
| Embedding throughput | ~1200/s (A100) | 0.0/s |
| Reranker throughput | ~180/s (A100) | 0.0/s |
| Concurrency RPS | ~0.6 RPS / 30 CCU | — |

## Response time theo độ khó (≤15s)
| Độ khó | p50 (ms) | p95 (ms) | %≤15s |
|--------|----------|----------|-------|
| de | 13122 | 46963 | 55.2% |
| trung_binh | 12537 | 48476 | 58.8% |
| kho | 13359 | 46963 | 54.1% |
| tu_duy | 12772 | 45816 | 58.9% |

## Lưu ý
- Đo trên deepseek-v4-flash (cloud) + bge endpoint VPS — KHÔNG phải 3×A100/Qwen on-prem như cam kết phần cứng.
- Response >15s ở câu khó/tư duy do LLM sinh câu trả lời dài; cân nhắc giới hạn độ dài / model nhanh hơn / on-prem A100 để đạt ngưỡng.