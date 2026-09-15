KPI mô hình Embedding: throughput đạt 1.200 embedding/s

**`03. Kpi_results/Model/models/host/throughput_embed_host.json`** — dòng 1–14 (model + mức concurrency 32 đạt per_s 2662.4):

```
{
  "mode": "embed",
  "url": "http://localhost:8000",
  "model": "DuyTa/sec-embedding",
  "levels": [
    {
      "conc": 32,
      "batch": 64,
      "reqs": 160,
      "embeddings": 10240,
      "seconds": 3.85,
      "per_s": 2662.4,
      "ok_reqs": 160
    },
```

Dòng 43–45 (đỉnh throughput):

```
  "peak_per_s": 2662.4,
  "peak_conc": 32
}
```

**Kết luận:** Đối chiếu được: `throughput_embed_host.json` (đo tại host) ghi model `DuyTa/sec-embedding` đạt đỉnh 2.662,4 embedding/s tại concurrency 32, cao hơn ngưỡng tối thiểu 1.200 embedding/s.
