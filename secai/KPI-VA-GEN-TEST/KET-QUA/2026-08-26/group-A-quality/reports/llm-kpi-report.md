# LLM Model — KPI (Faithfulness/Relevance/Entity/Hallucination/Reasoning)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


| Mã | KPI | Ngưỡng | Thực đo | Kết quả |
|----|-----|--------|---------|---------|
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98% | 96.71% | ❌ CHƯA ĐẠT |
| `R-KPI-RELEV90` | Answer Relevance (LLM-judge) | > 90% | 91.12% | ✅ ĐẠT |
| `R-KPI-ENTITY100` | Entity Precision CVE/CWE | = 100% | 100.0% | ✅ ĐẠT |
| `R-KPI-HALLU2` | Hallucination Rate | < 2% | 0.0% | ✅ ĐẠT |
| `R-KPI-REASONING` | Reasoning từng bước | — | 73.7% | ⏳ chưa đo |

Raw logs: `llm-faithfulness.jsonl`, `llm-answer-relevance.jsonl`, `llm-entity-precision.jsonl`, `llm-hallucination.jsonl` (True/Predicted/Score).

## Phương pháp
- Faithfulness: RAGAS (LLM judge) trên (question, answer, contexts truy hồi).
- Answer Relevance: cosine(embed(answer), embed(question)) — bge-m3.
- Entity Precision: regex CVE/CWE, mỗi id đối chiếu KB Qdrant (grounded).
- Hallucination: id bịa (không có trong KB) hoặc khẳng định CVE-giả (trap) là thật.

## False-Positive analysis
- Xem `raw-logs/llm-hallucination.jsonl` (score=1) và `llm-entity-precision.jsonl` (bad != []) để liệt kê ca sai.