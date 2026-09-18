# Reranking Model — Precision@1 (R-KPI-RERANK90)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


- **Precision@1: 99.41%** · ngưỡng > 90% · ✅ ĐẠT
- N = 849 · model DuyTa/sec-rerank

Raw log: `raw-logs/reranking-precision-at-1.jsonl` (True=gold CVE, Predicted=top-1 sau rerank, Score=1/0).

## Phân tích
- P@1 bị chặn bởi recall ứng viên: nếu gold không nằm trong candidate (Hit miss) thì rerank không cứu được.
- Cải thiện recall ứng viên (embedding đa ngữ + BM25) sẽ kéo P@1 lên.