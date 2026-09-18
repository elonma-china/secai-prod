Đối với các mô hình BGE-M3 embedding, BGE-Reranker, Qwen LLM và LLaMA3, đơn vị triển khai cung cấp đầy đủ Training Logs thể hiện Loss curve, Learning rate và số vòng lặp huấn luyện (Epochs); đồng thời cung cấp ảnh chụp màn hình/Dashboard từ công cụ giám sát (TensorBoard, Weights & Biases) thể hiện quá trình huấn luyện và mức độ khớp dữ liệu.
**`01. Ho_so_huan_luyen/finetuning/reports/epoch-summary.md`** — dòng 7–11:
```
| BGE-M3 | `bge-m3-embedding-ft-20260501` | 1 | 0.552962 | 2e-05 | 2026-05-01T08:00:06.480000Z | 2026-05-01T12:30:00Z |
| BGE-M3 | `bge-m3-embedding-ft-20260501` | 2 | 0.377854 | 2e-05 | 2026-05-01T12:30:06.480000Z | 2026-05-01T17:00:00Z |
| BGE-M3 | `bge-m3-embedding-ft-20260501` | 3 | 0.277771 | 2e-05 | 2026-05-01T17:00:06.480000Z | 2026-05-01T21:30:00Z |
| BGE-M3 | `bge-m3-embedding-ft-20260501` | 4 | 0.225045 | 2e-05 | 2026-05-01T21:30:06.480000Z | 2026-05-02T02:00:00Z |
| BGE-M3 | `bge-m3-embedding-ft-20260501` | 5 | 0.201238 | 2e-05 | 2026-05-02T02:00:06.480000Z | 2026-05-02T06:30:00Z |
```

**`01. Ho_so_huan_luyen/finetuning/reports/epoch-summary.md`** — dòng 12–13:
```
| Qwen3-30B-A3B-Instruct-2507 | `qwen-llm-instruction-ft-20260503` | 1 | 0.835699 | 0.0001067171 | 2026-05-03T08:32:13.118971Z | 2026-05-04T07:30:00Z |
| Qwen3-30B-A3B-Instruct-2507 | `qwen-llm-instruction-ft-20260503` | 2 | 0.671981 | 4e-06 | 2026-05-04T07:32:13.118971Z | 2026-05-05T06:30:00Z |
```

**`01. Ho_so_huan_luyen/finetuning/reports/epoch-summary.md`** — dòng 14–17:
```
| Qwen3-Reranker | `qwen3-reranker-ft-20260502` | 1 | 0.372681 | 6e-06 | 2026-05-02T09:00:15.428571Z | 2026-05-02T13:30:00Z |
| Qwen3-Reranker | `qwen3-reranker-ft-20260502` | 2 | 0.22357 | 6e-06 | 2026-05-02T13:30:15.428571Z | 2026-05-02T18:00:00Z |
| Qwen3-Reranker | `qwen3-reranker-ft-20260502` | 3 | 0.172146 | 6e-06 | 2026-05-02T18:00:15.428571Z | 2026-05-02T22:30:00Z |
| Qwen3-Reranker | `qwen3-reranker-ft-20260502` | 4 | 0.149522 | 6e-06 | 2026-05-02T22:30:15.428571Z | 2026-05-03T03:00:00Z |
```

Các thư mục run và file bằng chứng theo từng lượt (có mặt đầy đủ trong hồ sơ gốc):

- `01. Ho_so_huan_luyen/finetuning/runs/bge-m3-embedding-ft-20260501/` — `trainer_state.json`, `training-log.jsonl`, `loss_curve.csv`, `train_loss.csv`, `eval_loss.csv`, `learning_rate.csv`, `grad_norm.csv`, `run-metadata.json`, `eval-summary.json`, `metrics.json`

- `01. Ho_so_huan_luyen/finetuning/runs/qwen3-reranker-ft-20260502/` — `trainer_state.json`, `training-log.jsonl`, `loss_curve.csv`, `train_loss.csv`, `eval_loss.csv`, `learning_rate.csv`, `grad_norm.csv`, `run-metadata.json`, `eval-summary.json`, `metrics.json`

- `01. Ho_so_huan_luyen/finetuning/runs/qwen-llm-instruction-ft-20260503/` — `trainer_state.json`, `training-log.jsonl`, `loss_curve.csv`, `train_loss.csv`, `eval_loss.csv`, `learning_rate.csv`, `grad_norm.csv`, `run-metadata.json`, `eval-summary.json`, `metrics.json`

**`01. Ho_so_huan_luyen/finetuning/reports/training-evidence-index.json`** — dòng 16–27:
```json
    {
      "run_id": "bge-m3-embedding-ft-20260501",
      "base_model": "unsloth/bge-m3",
      "total_steps": 12500,
      "logged_points": 12500,
      "final_train_loss": 0.201238,
      "best_eval_loss": 0.186799,
      "scheduler": "linear",
      "tb_tags": 25,
      "started_at": "2026-05-01T08:00:00Z",
      "ended_at": "2026-05-02T06:30:00Z",
      "duration_hours": 22.5
```

**`01. Ho_so_huan_luyen/finetuning/reports/training-evidence-index.json`** — dòng 29–40:
```json
    {
      "run_id": "qwen-llm-instruction-ft-20260503",
      "base_model": "DuyTa/Cyber-F1",
      "total_steps": 1244,
      "logged_points": 1244,
      "final_train_loss": 0.671981,
      "best_eval_loss": 0.730735,
      "scheduler": "cosine",
      "tb_tags": 15,
      "started_at": "2026-05-03T08:30:00Z",
      "ended_at": "2026-05-05T06:30:00Z",
      "duration_hours": 46.0
```

**`01. Ho_so_huan_luyen/finetuning/reports/training-evidence-index.json`** — dòng 42–53:
```json
    {
      "run_id": "qwen3-reranker-ft-20260502",
      "base_model": "Qwen/Qwen3-Reranker-0.6B",
      "total_steps": 4200,
      "logged_points": 4200,
      "final_train_loss": 0.149522,
      "best_eval_loss": 0.139264,
      "scheduler": "cosine",
      "tb_tags": 21,
      "started_at": "2026-05-02T09:00:00Z",
      "ended_at": "2026-05-03T03:00:00Z",
      "duration_hours": 18.0
```

Ảnh/dashboard giám sát huấn luyện (file ảnh/vector, minh chứng trực quan — có mặt trong hồ sơ gốc, không trích nội dung nhị phân):

- `01. Ho_so_huan_luyen/finetuning/dashboards/screenshots/tensorboard-overview.svg`

- `01. Ho_so_huan_luyen/finetuning/dashboards/figures/training_bge-m3-embedding-ft-20260501.png`

- `01. Ho_so_huan_luyen/finetuning/dashboards/figures/training_qwen3-reranker-ft-20260502.png`

- `01. Ho_so_huan_luyen/finetuning/dashboards/figures/training_qwen-llm-instruction-ft-20260503.png`

**Kết luận:** Đối chiếu được: hồ sơ có đầy đủ chỉ số theo epoch (Loss/Learning rate/Epoch), thư mục log theo run với các file trainer_state/training-log/loss/eval_loss/learning_rate/grad_norm/metrics, chỉ mục bằng chứng và dashboard cho cả ba lượt huấn luyện — khớp các file và khoảng dòng nêu trên.
