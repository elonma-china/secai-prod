Chính sách retraining tự động và ngưỡng suy giảm chất lượng

**`02. Bo_build_finetuning/finetune/configs/finetune_thresholds.yml`** — dòng 5 (điều kiện kích hoạt retrain khi một check bắt buộc fail):
````
  "trigger": "A failed required check means its finetune_target should be retrained. Missing reports are not a failure; they mean the component was not assessed.",
````

**Embedding Hit Rate@10** — dòng 8–18 (`max_relative_drop: 0.05` tại dòng 14):
````
      "id": "embedding-hit-at-10",
      "report": "embedding",
      "finetune_target": "embedding",
      "suite_id": "secai-embedding-pairwise-v1",
      "metric": "recall@10",
      "min": 0.96,
      "max_relative_drop": 0.05,
      "min_examples": 100,
      "required": true,
      "platform_kpi": "R-KPI-EMB96",
      "note": "Local pairwise Recall@10 is a model-regression signal. The Platform KPI is end-to-end Qdrant Hit@10."
````

**Reranking Precision@1** — dòng 21–31 (`max_relative_drop: 0.05` tại dòng 27):
````
      "id": "reranker-precision-at-1",
      "report": "reranker",
      "finetune_target": "reranker",
      "suite_id": "secai-reranker-candidate-set-v1",
      "metric": "precision@1",
      "min": 0.90,
      "max_relative_drop": 0.05,
      "min_examples": 100,
      "required": true,
      "platform_kpi": "R-KPI-RERANK90",
      "note": "Local candidate-set Precision@1 is a model-regression signal, not end-to-end retrieval recall."
````

**LLM Correctness** — dòng 34–44 (`max_relative_drop: 0.05` tại dòng 40):
````
      "id": "llm-answer-correctness",
      "report": "llm",
      "finetune_target": "llm",
      "suite_id": "secai-llm-qa-harness-v1",
      "metric": "answer_correctness",
      "min": 0.85,
      "max_relative_drop": 0.05,
      "min_examples": 1000,
      "required": true,
      "platform_kpi": "R-LLM-SAST85",
      "note": "Independent judge compares the candidate answer to the frozen Q/A reference."
````

**Answer Relevance** — dòng 47–56 (`max_relative_drop: 0.05` tại dòng 53):
````
      "id": "llm-answer-relevance",
      "report": "llm",
      "finetune_target": "llm",
      "suite_id": "secai-llm-qa-harness-v1",
      "metric": "answer_relevance",
      "min": 0.90,
      "max_relative_drop": 0.05,
      "min_examples": 1000,
      "required": true,
      "platform_kpi": "R-KPI-RELEV90"
````

**Kết luận:** Kịch bản đối chiếu được: ngưỡng suy giảm tương đối kích hoạt retraining là 5% (`max_relative_drop: 0.05`) đối với Embedding Hit Rate@10 (dòng 14), Reranking Precision@1 (dòng 27), LLM Correctness (dòng 40) và Answer Relevance (dòng 53); trigger retrain khi một check `required` fail (dòng 5).
