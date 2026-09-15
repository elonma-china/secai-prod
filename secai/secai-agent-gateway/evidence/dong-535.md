Evaluation Code tái lập kết quả đánh giá

**`03. Kpi_results/Model/quality/evaluation-code/`** — liệt kê thư mục (đủ 5 file mã nguồn nêu trong kịch bản):
```
acceptance_runner.py
faith_judge.py
llm_kpi.py
retrieval_kpi.py
task_kpi.py
```

Docstring đầu mỗi file (xác nhận vai trò tái lập kết quả đánh giá):

**`.../evaluation-code/acceptance_runner.py`** — dòng 1–2:
```
#!/usr/bin/env python3
"""acceptance_runner.py — Runner nghiệm thu 1000 câu trên VPS (httpx), chống backend-wedge.
```

**`.../evaluation-code/retrieval_kpi.py`** — dòng 1–6:
```
#!/usr/bin/env python3
"""retrieval_kpi.py — [CHẠY TRÊN VPS] Đo KPI tầng truy hồi + xuất contexts cho RAGAS.

- R-KPI-EMB96: Embedding Hit Rate@10 — embed(query) → Qdrant dense search top-10;
  hit nếu có point cve_id==gold & chunk_kind==core trong top-10.
- R-KPI-RERANK90: Reranking Precision@1 — rerank top-20 candidate (dense) → top-1 có cve_id==gold.
```

**`.../evaluation-code/llm_kpi.py`** — dòng 2–9:
```
"""llm_kpi.py — [LOCAL] KPI tầng LLM: Faithfulness, Answer Relevance, Entity Precision,
Hallucination, Reasoning. Raw log True/Predicted/Score.

- R-KPI-FAITH98 Faithfulness >98% — RAGAS faithfulness (LLM judge = DeepSeek). Cần `ragas`.
- R-KPI-RELEV90 Answer Relevance >90% — Cosine Similarity embed(answer)·embed(question) (bge).
- R-KPI-ENTITY100 Entity Precision =100% — regex CVE/CWE, mỗi id phải grounded (gold/contexts).
- R-KPI-HALLU2 Hallucination <2% — câu có entity bịa / khẳng định CVE giả tồn tại.
- R-KPI-REASONING — heuristic có giải thích từng bước.
```

**`.../evaluation-code/faith_judge.py`** — dòng 1–4:
```
#!/usr/bin/env python3
"""Faithfulness judge trực tiếp (gpt-4o-mini) — RAGAS-style, stdlib only.
Merge answers + contexts theo id → mỗi mẫu hỏi judge: bao nhiêu claim được context hậu thuẫn.
score = supported/total. Output faithfulness-summary.json + llm-faithfulness.jsonl."""
```

**`.../evaluation-code/task_kpi.py`** — dòng 2–6:
```
"""task_kpi.py — [LOCAL] Accuracy 3 nhiệm vụ LLM theo hợp đồng, raw log True/Predicted/Score.

- R-LLM-SAST85: SAST phát hiện & phân loại CWE đúng ≥85% (OWASP Top10/CWE Top25).
- R-LLM-EXPLOIT85: Exploit Explanation đúng logic & đầy đủ ≥85%.
- R-LLM-REMED60: Auto-remediation đề xuất bản vá hợp lệ ≥60%.
```

**Kết luận:** Đối chiếu được: thư mục `03. Kpi_results/Model/quality/evaluation-code/` chứa đủ 5 file mã nguồn đánh giá theo kịch bản — `acceptance_runner.py`, `retrieval_kpi.py`, `llm_kpi.py`, `faith_judge.py`, `task_kpi.py` — mỗi file có docstring mô tả KPI/tác vụ tương ứng để tái lập kết quả đánh giá.
