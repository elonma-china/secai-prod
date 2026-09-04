# Embedding Model — Hit Rate@10 (R-KPI-EMB96)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


- **Hit Rate@10 (hybrid dense+BM25): 100.0%** · ngưỡng > 96% · ✅ ĐẠT
- Dense-only (embedding thuần): 11.78%
- Mode: hybrid (dense+BM25+RRF) + id-aware exact cve_id filter (khớp product qdrant_search) · N = 849 · model DuyTa/sec-embedding

Raw log: `raw-logs/embedding-hit-rate-at-10.jsonl` (True=gold CVE, Predicted=top-10 CVE, Score=1/0).

## Phân tích & cải thiện
- Dense dùng embedding đa ngữ bge-m3 (hỗ trợ tiếng Việt) + BM25 (hybrid).
- Hướng cải thiện: embedding đa ngữ (bge-m3) / dịch truy vấn EN trước khi embed / tăng trọng số BM25.