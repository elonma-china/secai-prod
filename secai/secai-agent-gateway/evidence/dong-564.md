- Minh chứng Fine-tune mô hình thực hiện theo chính sách kiểm soát khi cần thiết: Là phần giải pháp tự động phát hiện suy giảm độ chính xác qua pipeline đánh giá định kỳ (bên trên)

Kịch bản dẫn chiếu "(bên trên)" / "(đã thực hiện ở trên)" — gồm hai phần.

1. Fine-tune theo chính sách kiểm soát khi cần thiết (tự động phát hiện suy giảm độ chính xác qua pipeline đánh giá định kỳ): cùng minh chứng với dòng 561 — chính sách quality-gate `02. Bo_build_finetuning/finetune/configs/finetune_thresholds.yml`.

**`02. Bo_build_finetuning/finetune/configs/finetune_thresholds.yml`** — dòng 3–5 (policy id + điều kiện kích hoạt retrain):
````
  "policy_id": "secai-finetune-quality-gate-v1",
  "baseline_file": "../../benchmarks/baselines/approved_metrics.json",
  "trigger": "A failed required check means its finetune_target should be retrained. Missing reports are not a failure; they mean the component was not assessed.",
````

**`02. Bo_build_finetuning/finetune/configs/finetune_thresholds.yml`** — dòng 12–14 (ví dụ ngưỡng suy giảm 5% của một check):
````
      "metric": "recall@10",
      "min": 0.96,
      "max_relative_drop": 0.05,
````

2. Thu thập tri thức mới hằng ngày và cập nhật vào RAG (không retrain): thuộc pipeline ingest/đồng bộ trong gói mã nguồn/triển khai (`ingest/**`, `run_sync.sh`), không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04); kịch bản ghi rõ phần này "đã thực hiện ở trên".

**Kết luận:** Kịch bản đối chiếu được phần fine-tune theo chính sách kiểm soát qua `finetune_thresholds.yml` (trigger retrain khi check `required` fail; ngưỡng suy giảm `max_relative_drop: 0.05` = 5%); phần cập nhật tri thức RAG hằng ngày (không retrain) thuộc pipeline ingest/đồng bộ của gói mã nguồn/triển khai, không có file trích trong hồ sơ gốc phần AI.
