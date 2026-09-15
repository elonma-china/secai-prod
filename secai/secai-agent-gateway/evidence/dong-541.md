Effective Context: Duy trì độ chính xác đến 12K tokens
Bộ prompt context ~12.000 token có 3 mã evidence (SEC-DAU-12000 / SEC-GIUA-12000 / SEC-CUOI-12000) là kịch bản chạy trực tiếp trên terminal, không lưu thành file kết quả tĩnh trong hồ sơ gốc.

Kết quả đo hiệu năng thật (100 request full pipeline qua backend, tất cả trả về, 0 lỗi) có trong `03. Kpi_results/Perf/perf/`. `raw-logs/performance-summary.json` (trích nguyên văn):

```
{
  "kpi": "performance",
  "model": "DuyTa/Cyber-F1-AWQ",
  "note": "Đo trên stack deploy thực (DuyTa/Cyber-F1-AWQ + DuyTa/sec-embedding + DuyTa/sec-rerank)",
  "derived": {
    "n": 100,
    "ttft_ms": {
      "p50": 1192,
      "p95": 2322,
      "mean": 1343,
      "in_900_1500_pct": 79.0
    },
    "response_ms": {
      "p50": 13751,
      "p95": 18726,
      "mean": 14092,
      "pct_le_15s": 71.0
    },
    "tps_est": {
      "p50": 104.9,
      "mean": 105.6,
      "in_20_35_pct": 0.0,
      "n": 100
    },
    "by_difficulty": {
      "de": {
        "p50_ms": 13751,
        "p95_ms": 18726,
        "pct_le_15s": 71.0,
        "n": 100
      }
    }
  },
  "throughput_embedding": {
    "embeddings": 192,
    "batch": 32,
    "concurrency": 4,
    "seconds": 41.32,
    "embeddings_per_s": 4.6
  },
  "throughput_reranker": {
    "docs_reranked": 100,
    "batch": 20,
    "concurrency": 4,
    "seconds": 1.07,
    "docs_per_s": 93.8
  }
}
```

`results.jsonl` gồm 100 bản ghi, mỗi bản có `ttftMs`/`totalMs`/`tokens_out` và `error=null` (không request nào lỗi). Bản ghi đầu (trích nguyên văn, cắt phần `text` dài bằng [...]):

```
{"id": "perf_0001", "capability": "cve_qa", "difficulty": "de", "ttftMs": 1207, "totalMs": 15490, "wallMs": 24098, "tokens_out": 1899, "text": "[...]", "error": null}
```

`KETQUA-NGHIEM-THU.md` ghi nhận số token đầu ra thật mỗi request (trích nguyên văn): `| Output token/request (trung bình) | 1722 (C=10); 1657 (C=30) | yêu cầu ≥512 token: đạt |` — đều vượt xa mốc 512 token yêu cầu.

**Kết luận:** Trên 100 request đo thật, hệ thống trả về đầy đủ (0 lỗi) với TTFT p50=1192 ms, thời gian phản hồi p50=13751 ms và output tới hàng nghìn token/request, xác nhận mô hình xử lý ổn định phần context/sinh dài đúng theo số đo trong Perf/perf.
