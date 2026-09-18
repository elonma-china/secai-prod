# Log huấn luyện — nội dung API đang trả

> **Trạng thái: ĐÃ TRIỂN KHAI.** `GET /ai/finetune/runs/{run_id}/log` trả `log_stream` (log text tiếng Anh) + `training_summary`. Số liệu lấy từ hồ sơ gốc: `01. Ho_so_huan_luyen/finetuning/{reports/hyperparameters.md, reports/training-evidence-index.json, reports/epoch-summary.csv, logs/training-log.jsonl}`.
>
> So với bản nháp đầu (v1) có 3 thay đổi theo yêu cầu: log chuyển sang **tiếng Anh** cho đúng quy ước log công cụ huấn luyện thật; **mỗi dòng step lấy nguyên từ `training-log.jsonl`** thay vì 10 dòng tóm tắt viết tay; **bỏ chỉ số quy đổi "% mức độ khớp dữ liệu"** — `training_summary` chỉ còn loss · GPU · thời gian · dataset.

## Cấu trúc `log_stream.lines`

```
==== SecAI Fine-tuning Run ====                 ← header: hyperparameter thật
run_id=…  component=…
base_model=…
method=…
loss_fn=…  engine=…
hyperparameters: epochs=… effective_batch=… lr=… scheduler=…
platform=2x NVIDIA A100 80GB
dataset: <n> records, groups=[…]
dataset sources: …
================================
<mỗi dòng step lấy nguyên từ logs/training-log.jsonl>   ← granularity=step
   vd: 2026-05-03T08:32:13.118971Z | INFO | step 1 | epoch 0.0016 | loss 1.473286 | lr 5.4054e-06 | grad_norm 5.1523 | 0.48 samples/s
<hoặc mỗi dòng 1 epoch từ reports/epoch-summary.csv>    ← granularity=epoch
================================
Run complete: <steps> steps, <epochs> epochs, eval_loss=…, duration=…h
```

`max_points` (10–2000, mặc định 300) quyết định số dòng step được lấy mẫu đều từ log gốc.

## Hyperparameter + `training_summary` từng run

| | BGE-M3 embedding | Qwen3-Reranker | Qwen LLM (Cyber-F1) |
|---|---|---|---|
| `run_id` | bge-m3-embedding-ft-20260501 | qwen3-reranker-ft-20260502 | qwen-llm-instruction-ft-20260503 |
| Base model | unsloth/bge-m3 | Qwen/Qwen3-Reranker-0.6B | Qwen3-30B-A3B-Instruct-2507 (ckpt DuyTa/Cyber-F1) |
| Phương pháp | Full fine-tuning (**không** LoRA) | Full fine-tuning (**không** LoRA) | PEFT **LoRA r=8, alpha=16** |
| Loss | CachedMultipleNegativesRankingLoss | listwise_reranker (ms-swift) | CausalLM cross-entropy (Unsloth SFT) |
| Engine | unsloth FastSentenceTransformer | ms-swift generative_reranker | unsloth 16-bit MoE LoRA |
| Epochs / effective_batch / lr | 5 / 16 / 2e-05 (linear) | 4 / 32 / 6e-06 (cosine) | 2 / 64 / 2e-04 (cosine, warmup 37) |
| `first_step_loss` → `final_eval_loss` | 0.933415 → 0.219856 | 0.707771 → 0.158319 | 1.473286 → 0.74909 |
| `gpu` | 2x NVIDIA A100 80GB | 2x NVIDIA A100 80GB | 2x NVIDIA A100 80GB |
| `duration_hours` | 22.5 | 18.0 | 46.0 (~1.9 ngày) |
| `dataset_records` | 50.000 | 42.000 | 49.756 |

⚠️ Reranker **không dùng LoRA** (`lora_r=0` trong `hyperparameters.md`) — dòng `method=` của run này phải ghi "Full fine-tuning", không được ghi nhầm LoRA.

## Các yêu cầu liên quan đã xử lý ở chỗ khác

- **3 tab log độc lập/model** (row 46): FE tab Finetuning có 3 nút run_id, mỗi nút gọi F3 riêng.
- **Ma trận train/test/val từng model** (row 52-53): có thật trong hồ sơ gốc — `datasets/processed/manifest.json` + `datasets/phu-luc-ky-thuat-v1.md`; trả qua `GET /ai/finetune/data-processing` (`labeling.schema_per_dataset`) và `GET /ai/finetune/dataset-appendix`.
- **Nền tảng + tổng thời gian trong log** (rows 49-50, 54): nằm ở header/footer của `log_stream` và trong `training_summary`.
- **TensorBoard** (yêu cầu bổ sung): `GET /ai/finetune/runs/{run_id}/charts` trả `tensorboard_url` trỏ TensorBoard thật ở cổng 6006, lọc sẵn đúng run.
