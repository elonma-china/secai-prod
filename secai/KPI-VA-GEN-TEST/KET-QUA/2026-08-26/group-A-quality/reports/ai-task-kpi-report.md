# Báo cáo KPI AI — Tổng hợp

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


**Tổng quan: 10/11 KPI đạt.** Radar coverage: 97.9%.


| Mã | KPI | Ngưỡng | Thực đo | Kết quả |
|----|-----|--------|---------|---------|
| `R-KPI-EMB96` | Embedding Hit Rate@10 | > 96% | 100.0% | ✅ ĐẠT |
| `R-KPI-RERANK90` | Reranking Precision@1 | > 90% | 99.41% | ✅ ĐẠT |
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98% | 96.71% | ❌ CHƯA ĐẠT |
| `R-KPI-RELEV90` | Answer Relevance (LLM-judge) | > 90% | 91.12% | ✅ ĐẠT |
| `R-KPI-ENTITY100` | Entity Precision CVE/CWE | = 100% | 100.0% | ✅ ĐẠT |
| `R-KPI-HALLU2` | Hallucination Rate | < 2% | 0.0% | ✅ ĐẠT |
| `R-LLM-SAST85` | SAST accuracy | ≥ 85% | 85.0% | ✅ ĐẠT |
| `R-LLM-EXPLOIT85` | Exploit Explanation | ≥ 85% | 94.12% | ✅ ĐẠT |
| `R-LLM-REMED60` | Auto-remediation | ≥ 60% | 83.33% | ✅ ĐẠT |
| `R-SEC-PII` | Sanitization PII/Secret F1 | > 90% | 98.11% | ✅ ĐẠT |
| `R-KPI-RADAR90` | Radar coverage area | > 90% | 97.9% | ✅ ĐẠT |

## Diễn giải

- Raw logs True/Predicted/Score: `raw-logs/*.jsonl`. Tổng hợp: `kpi-summary.json`/`.xlsx`, `run-manifest.json`.
- Báo cáo chi tiết từng mô hình: `embedding-report.md`, `reranking-report.md`, `llm-kpi-report.md`, `performance-report.md`, `sanitization-report.md`.
- Radar: `radar.png` (+ `radar-data.json`).