# Kế hoạch cải thiện & phân tích lỗi (improvement-plan)

> Model: **DuyTa/Cyber-F1-AWQ** · Ngày: 2026-08-26-224343 · Commit: `unknown` · Test set: 1000 mẫu (40-40-20)


**1/11 KPI chưa đạt ngưỡng.** Phân tích nguyên nhân + hướng xử lý dưới đây.


| Mã | KPI | Ngưỡng | Thực đo | Hướng cải thiện |
|----|-----|--------|---------|-----------------|
| `R-KPI-FAITH98` | Faithfulness (RAGAS) | > 98% | 96.71% | Đưa đủ context agent thực dùng (qdrant+neo4j+poc) vào phép chấm thay vì chỉ chunk gold; cân nhắc prompt agent bám sát nguồn/trích dẫn. |

## Hiệu năng — Response ≤ 15s
- p50 = 12893 ms · chỉ 56.4% câu ≤ 15s. Giới hạn độ dài trả lời cho truy vấn đơn giản; model nhanh hơn / on-prem A100; stream + cache câu lặp.

## Phân tích False-Positive (chống bịa đặt)
- Entity Precision + Hallucination là thước đo 'không bịa'. Xem `raw-logs/llm-entity-precision.jsonl` (`bad != []`) và `llm-hallucination.jsonl` (score=1) để liệt kê ca sai; chi tiết FP/FN: `false-positive-analysis.md`, `false-negative-analysis.md`, `failed-samples.xlsx`.
- Nguyên nhân gốc retrieval/relevance: embedding EN baseline trước đây không hợp tiếng Việt — đã chuyển bge-m3 đa ngữ (xem `embedding-report.md`).