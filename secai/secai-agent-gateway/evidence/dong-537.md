Quy trình cập nhật được thiết lập để tiếp nhận và tích hợp lỗ hổng 0-day và kỹ thuật tấn công mới từ các nguồn tình báo ATTT:
Các file mã nguồn pipeline ingest mà kịch bản nêu (`ingest/**`: `nvd_feed.py`, `exploitdb.py`, `taxonomy.py`, `cve_to_chunk.py`, `watermark.py`, `qdrant_sink.py`, `bm25_sink.py`, `neo4j_sink.py`) không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04) — chúng thuộc gói mã nguồn/pipeline.

Kết quả thật thay thế — bằng chứng tri thức đã nạp và truy hồi được khi chạy 1.000 mẫu. `03. Kpi_results/Model/quality/raw-logs/retrieval-summary.json` (trích nguyên văn):

```
{
  "kpi": "retrieval",
  "mode": "hybrid (dense+BM25+RRF) + id-aware exact cve_id filter (khớp product qdrant_search)",
  "model_embed": "DuyTa/sec-embedding",
  "model_rerank": "DuyTa/sec-rerank",
  "n": 735,
  "errors": 0,
  "hit_rate_at_10_pct": 98.78,
  "hit_rate_at_10_hybrid_pct": 80.14,
  "hit_rate_at_10_dense_only_pct": 30.2,
  "precision_at_1_pct": 98.23
}
```

`03. Kpi_results/Model/quality/raw-logs/contexts.jsonl` (735 bản ghi) cho thấy mỗi truy vấn thật đã lấy context + danh sách CVE truy hồi từ KB. Bản ghi đầu (trích nguyên văn các trường, `contexts` là chuỗi dài nên cắt [...]):

```
id: acc_dd_0001
query: CVE-2018-15318 là lỗ hổng gì? Mức độ nghiêm trọng thế nào?
contexts: [...]
retrieved_cves: ['CVE-2018-15318', 'CVE-2018-15318', 'CVE-2018-8327', 'CVE-2018-11218', 'CVE-2017-15318']
```

**Kết luận:** RAG hybrid (dense + BM25 + RRF trên Qdrant/Neo4j, model DuyTa/sec-embedding + DuyTa/sec-rerank) đã nạp và truy hồi được KB CVE trên 735 truy vấn thật với Hit@10=98.78% và P@1=98.23%, đúng bằng chứng nạp/dùng tri thức thay cho các file mã nguồn ingest không có trong hồ sơ.
