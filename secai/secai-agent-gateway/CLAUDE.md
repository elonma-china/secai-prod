# secai-agent-gateway — project instructions

## Deploy serving (vast) — LUÔN dùng BIẾN THỂ fine-tuned, KHÔNG dùng mặc định

Mỗi khi **deploy / thuê lại serving** qua `secai-serving-vast/serving-up-cyber-f1.sh`
(hoặc bất kỳ đường nào dựng serving), **BẮT BUỘC** truyền cờ model biến thể:

    ./serving-up-cyber-f1.sh --yes \
      --embed-model  DuyTa/sec-embedding \
      --rerank-model DuyTa/sec-rerank

**KHÔNG dùng default của script** (`BAAI/bge-m3` + `Qwen/Qwen3-Reranker-0.6B`).

**Lý do:** gateway `secai-docker/.env` gửi `EMBEDDING_MODEL=DuyTa/sec-embedding` và
`RERANKER_MODEL=DuyTa/sec-rerank` trong mỗi request. Nếu serving load model mặc định thì
**tên model LỆCH** → endpoint embed/rerank trả `404 "model does not exist"` → dense retrieval
(Qdrant) + rerank gãy, RAG tụt xuống **BM25-only** (dù `/health` vẫn báo `ok` vì health không
probe embed/rerank). (Sự cố thực tế 2026-08-23.)

**Verify sau deploy (bắt buộc, không chỉ /health):**

    curl -s -X POST http://<IP>:<EMBED_PORT>/v1/embeddings \
      -H 'Authorization: Bearer sk-secai2026' -H 'Content-Type: application/json' \
      -d '{"model":"DuyTa/sec-embedding","input":"test"}'   # phải trả 200 + vector

LLM vẫn là `DuyTa/Cyber-F1-AWQ` (mặc định script cho LLM là đúng — chỉ embed/rerank phải override).
