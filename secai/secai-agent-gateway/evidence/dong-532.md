Hybrid Search trong hệ thống

File được kịch bản yêu cầu mở — `code/source/docker-compose.deploy.yml` — thuộc gói mã nguồn/triển khai, KHÔNG nằm trong hồ sơ gốc phần AI (4 thư mục 01–04) nên không có nội dung để trích nguyên văn tại đây. Không bịa nội dung file này.

Việc hệ thống chạy Hybrid Search (Vector + Keyword/BM25) và các mô hình Embedding/Reranking được đối chiếu qua các tài liệu CÓ trong hồ sơ gốc:

**`03. Kpi_results/Model/quality/raw-logs/retrieval-summary.json`** — dòng 2–5 (chế độ truy hồi + mô hình):
```
  "kpi": "retrieval",
  "mode": "hybrid (dense+BM25+RRF) + id-aware exact cve_id filter (khớp product qdrant_search)",
  "model_embed": "DuyTa/sec-embedding",
  "model_rerank": "DuyTa/sec-rerank",
```

**`03. Kpi_results/Model/quality/reports/embedding-report.md`** — dòng 8 (mode hybrid dense+BM25):
```
- Mode: hybrid (dense+BM25+RRF) + id-aware exact cve_id filter (khớp product qdrant_search) · N = 735 · model DuyTa/sec-embedding
```

**`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 5, 10 (thành phần serving + Docker gồm qdrant):
```
1. **Serving GPU** (thuê trên vast.ai): phục vụ ba mô hình gồm mô hình ngôn ngữ (LLM), mô hình embedding và mô hình reranker.
```
```
  Docker: gateway + qdrant + neo4j + redis
```

**`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 105–112 (endpoint embedding/reranker + QDRANT_URL cấu hình mặc định):
```
EMBEDDING_API_URL=http://<IP>:<port2>     # EMBED URL
RERANKER_API_URL=http://<IP>:<port3>      # RERANK URL
EMBEDDING_API_KEY=sk-secai2026
RERANKER_API_KEY=sk-secai2026
NEO4J_PASSWORD=<mật-khẩu-mạnh>            # không giữ giá trị mặc định 'change-me'
ADMIN_API_KEY=<khóa-quản-trị>
```
```
Các tham số còn lại (tên mô hình, đường dẫn, QDRANT_URL, v.v.) giữ nguyên giá trị mặc định.
```

**Kết luận:** Không đối chiếu được trực tiếp trên `code/source/docker-compose.deploy.yml` vì file này thuộc gói mã nguồn/triển khai, không nằm trong hồ sơ gốc phần AI; tuy nhiên việc hệ thống triển khai Hybrid Search (Vector qua Qdrant + Keyword/BM25 + RRF) cùng embedding/reranking được xác nhận qua `retrieval-summary.json` (trường `mode`, `model_embed`, `model_rerank`), `embedding-report.md` (dòng 8) và `HUONG-DAN-CAI-DAT.md` (thành phần serving + Docker qdrant + endpoint embed/rerank + QDRANT_URL).
