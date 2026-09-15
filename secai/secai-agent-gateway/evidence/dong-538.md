Cung cấp nhân sự kỹ thuật phối hợp với Chủ đầu tư để hướng dẫn và tổng hợp 01 bộ test mới cho từng loại mô hình Embedding, Reranking và LLM, phục vụ đánh giá độc lập độ chính xác. Kết quả đánh giá chấp nhận khi sai lệch không vượt quá 5% so với các chỉ số đã công bố; trường hợp sai lệch vượt ngưỡng, bên cung cấp cam kết fine-tune/điều chỉnh (code hssd đi kèm - tự config điều chỉnh tham số) mô hình miễn phí trước khi nghiệm thu.
Ngưỡng "sai lệch không vượt quá 5%" đã được mã hoá thành chính sách chất lượng có thật trong hồ sơ, không chỉ là điều khoản cam kết. `02. Bo_build_finetuning/finetune/configs/finetune_thresholds.yml` ("policy_id": "secai-finetune-quality-gate-v1",): mỗi phép kiểm bắt buộc đặt mức trôi tương đối tối đa `max_relative_drop: 0.05` (tức 5%). Ví dụ phép kiểm embedding (trích nguyên văn):

```
    {
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
    },
```

Các phép kiểm còn lại cũng ràng buộc `max_relative_drop: 0.05`: reranker `precision@1` (min 0.90), llm `answer_correctness` (min 0.85), `answer_relevance` (min 0.90); riêng `entity_precision` đặt `max_relative_drop: 0.0` (min 1.0) và `hallucination_rate` đặt `max: 0.02` với `max_absolute_increase: 0.01`.

Bộ công cụ đánh giá độc lập có sẵn để hai bên tái lập tại `03. Kpi_results/Model/quality/evaluation-code/`: `acceptance_runner.py`, `faith_judge.py`, `llm_kpi.py`, `retrieval_kpi.py`, `task_kpi.py`.

**Kết luận:** Ngưỡng chấp nhận ≤5% đã được cụ thể hoá bằng `max_relative_drop: 0.05` cho mọi phép kiểm bắt buộc trong finetune_thresholds.yml, kèm bộ evaluation-code cho phép Chủ đầu tư đánh giá độc lập và tái lập.
