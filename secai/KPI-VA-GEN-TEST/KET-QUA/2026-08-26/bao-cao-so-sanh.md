# Báo cáo so sánh KPI — kiểm chứng độc lập (`R-EVAL-INDEP`)

- **bộ test độc lập**: `/home/ict/secai-88/eval-standalone/runs/2026-08-26-224343-evalindep/raw-logs`
- **chỉ số công bố**: `/home/ict/secai-88/kpi_results/quality/raw-logs`
- **Ngưỡng sai lệch cho phép**: ±5.0%
- **Kết quả**: 8 chấp nhận · 3 vượt ngưỡng · 8 thiếu số (trên 19 KPI)

> ⚠ **Cảnh báo so sánh khập khiễng**

> - `model` khác nhau: A = `DuyTa/Cyber-F1-AWQ` · B = `Qwen/Qwen3-30B-A3B`

`Δ%` tính theo **hướng xấu đi**: dương = tệ hơn baseline, âm = tốt hơn.

## Nhóm A — Chất lượng AI

| Mã KPI | Tên KPI | Ngưỡng cam kết | Đo thực tế (A) | Baseline (B) | Chênh lệch | Δ% | Đạt A | Kết luận |
|---|---|---|---:|---:|---:|---:|:---:|---|
| `R-KPI-EMB96` | Embedding Hit Rate@10 | > 96 % | 100 | 98.78 | 1.22 | -1.24 | ✅ | CHẤP NHẬN |
| `R-KPI-RERANK90` | Reranking Precision@1 | > 90 % | 99.41 | 98.23 | 1.18 | -1.2 | ✅ | CHẤP NHẬN |
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98 % | 96.71 | 98.48 | -1.77 | 1.8 | ❌ | CHẤP NHẬN |
| `R-KPI-RELEV90` | Answer Relevance | > 90 % | 91.12 | 92.88 | -1.76 | 1.89 | ✅ | CHẤP NHẬN |
| `R-KPI-ENTITY100` | Entity Precision CVE/CWE | = 100 % | 100 | 100 | 0 | 0 | ✅ | CHẤP NHẬN |
| `R-KPI-HALLU2` | Hallucination Rate | < 2 % | 0 | 0.8 | -0.8 | -100 | ✅ | TỐT HƠN |
| `R-LLM-SAST85` | SAST accuracy | >= 85 % | 85 | 85 | 0 | 0 | ✅ | CHẤP NHẬN |
| `R-LLM-EXPLOIT85` | Exploit Explanation | >= 85 % | 94.12 | 100 | -5.88 | 5.88 | ✅ | VƯỢT NGƯỠNG SAI LỆCH |
| `R-LLM-REMED60` | Auto-remediation | >= 60 % | 83.33 | 100 | -16.67 | 16.67 | ✅ | VƯỢT NGƯỠNG SAI LỆCH |
| `R-SEC-PII` | Sanitization PII/Secret F1 | > 90 % | 98.11 | 98.11 | 0 | 0 | ✅ | CHẤP NHẬN |
| `R-KPI-RADAR90` | Radar coverage area | > 90 % | 97.9 | 103.9 | -6 | 5.77 | ✅ | VƯỢT NGƯỠNG SAI LỆCH |

## Nhóm B — Hiệu năng chatflow

| Mã KPI | Tên KPI | Ngưỡng cam kết | Đo thực tế (A) | Baseline (B) | Chênh lệch | Δ% | Đạt A | Kết luận |
|---|---|---|---:|---:|---:|---:|:---:|---|
| `R-PERF-TPS` ¹ | TPS chatflow (token/s moi luong) | 20–35 tok/s | — | 55.2 | — | — | — | THIẾU SỐ |
| `R-PERF-TTFT` ¹ | TTFT p50 | 900–1500 ms | — | 1192 | — | — | — | THIẾU SỐ |
| `R-PERF-RPS` ¹ | RPS on dinh | ~0.6 RPS | — | 0.7 | — | — | — | THIẾU SỐ |
| `R-PERF-CCU` ¹ | CCU dong thoi | ~30 luong | — | 30 | — | — | — | THIẾU SỐ |
| `R-CTRL-RESP15` | Response p50 | <= 15000 ms | — | 13751 | — | — | — | THIẾU SỐ |

## Nhóm C — Throughput mô hình

| Mã KPI | Tên KPI | Ngưỡng cam kết | Đo thực tế (A) | Baseline (B) | Chênh lệch | Δ% | Đạt A | Kết luận |
|---|---|---|---:|---:|---:|---:|:---:|---|
| `R-MODEL-EMB1200` | Embedding throughput | >= 1200 emb/s | — | 2662.4 | — | — | — | THIẾU SỐ |
| `R-MODEL-RERANK180` | Reranker throughput | >= 180 docs/s | — | 4862.4 | — | — | — | THIẾU SỐ |
| `R-LLM-TPS28` | LLM throughput (lop suy luan) | >= 28 tok/s | — | 55.2 | — | — | — | THIẾU SỐ |

¹ KPI mà **script không ép ngưỡng** (`build_perf_summary.py:65` và các lambda `v is not None`). Cột *Đạt A* ở đây do `compare_kpi.py` **tự chấm lại** theo ngưỡng hợp đồng, không lấy trường `pass` của script.

## Metadata hai lần chạy

| Trường | A | B |
|---|---|---|
| `date` | 2026-08-26-224343 | 2026-06-22-003956 |
| `embed_model` | DuyTa/sec-embedding | BAAI/bge-m3 |
| `git_commit` | unknown | unknown |
| `infra` | — | 2× NVIDIA A100 80GB (80GB) |
| `measured_date` | — | 2026-06-22 |
| `model` | DuyTa/Cyber-F1-AWQ | Qwen/Qwen3-30B-A3B |
| `model_llm` | DuyTa/Cyber-F1-AWQ | default |
| `note` | LLM=DuyTa/Cyber-F1-AWQ @ http://80.188.223.202:11444/v1. Bench endpoint-driven; đổi stack: set LLM_*/EMBED_URL/RERANK_URL/QDRANT_URL rồi chạy lại bench.sh. | LLM=default @ http://99.148.65.9:19787/v1. Bench endpoint-driven; đổi stack: set LLM_*/EMBED_URL/RERANK_URL/QDRANT_URL rồi chạy lại bench.sh. |
| `rerank_model` | DuyTa/sec-rerank | Qwen/Qwen3-Reranker-0.6B |
| `test_set` | eval/datasets-acceptance (1000 mẫu, 40-40-20) | eval/datasets-acceptance (1000 mẫu, 40-40-20) |

## Kết luận

**3 KPI vượt ngưỡng sai lệch 5.0%.** Theo `R-EVAL-INDEP` (xlsx dòng 556), nhà thầu phải **fine-tune/điều chỉnh miễn phí** rồi chạy lại trước khi nghiệm thu.
