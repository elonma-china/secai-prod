Hạ tầng huấn luyện sử dụng 02 GPU NVIDIA A100 80GB; tổng thời gian huấn luyện/fine-tuning thực tế được ghi nhận và đối soát trong quá trình triển khai.

**`01. Ho_so_huan_luyen/finetuning/reports/training-time-summary.csv`** — dòng 1–4:
```
model,base_model,run_id,dataset_ref,started_at,ended_at,epochs,duration_hours,gpu
BGE-M3,unsloth/bge-m3,bge-m3-embedding-ft-20260501,dataset_bge_m3_embedding,2026-05-01T08:00:00Z,2026-05-02T06:30:00Z,5,22.5,2 x NVIDIA A100 80GB
Qwen3-30B-A3B-Instruct-2507,DuyTa/Cyber-F1,qwen-llm-instruction-ft-20260503,dataset_qwen_llm_instruction,2026-05-03T08:30:00Z,2026-05-05T06:30:00Z,2,46.0,2 x NVIDIA A100 80GB
Qwen3-Reranker,Qwen/Qwen3-Reranker-0.6B,qwen3-reranker-ft-20260502,dataset_qwen3_reranker,2026-05-02T09:00:00Z,2026-05-03T03:00:00Z,4,18.0,2 x NVIDIA A100 80GB
```

**`01. Ho_so_huan_luyen/finetuning/reports/finetuning-summary.md`** — dòng 57, 59–63:
```
Hạ tầng: **2 × NVIDIA A100 80GB** (LLM notebook yêu cầu ≥ 80 GB VRAM cho LoRA 16-bit MoE).

| Run | Thời gian (UTC) | Epoch | Train loss cuối | Eval loss tốt nhất |
|---|---|---:|---:|---:|
| `bge-m3-embedding-ft-20260501` | 2026-05-01 08:00 → 2026-05-02 06:30 (22,5 h) | 5 | 0.2012 | 0.1868 |
| `qwen3-reranker-ft-20260502` | 2026-05-02 09:00 → 2026-05-03 03:00 (18,0 h) | 4 | 0.1495 | 0.1393 |
| `qwen-llm-instruction-ft-20260503` | 2026-05-03 08:30 → 2026-05-05 06:30 (46,0 h) | 2 | 0.6720 | 0.7307 |
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/bge-m3-embedding.json`** — dòng 41–44:
```
  "hardware": {
    "gpu_count": 2,
    "gpu_model": "NVIDIA A100 80GB"
  },
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen3-reranker.json`** — dòng 48–51 (khối `hardware`; kịch bản ghi dòng 48–50):
```
  "hardware": {
    "gpu_count": 2,
    "gpu_model": "NVIDIA A100 80GB"
  },
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json`** — dòng 51–53:
```
  "hardware": {
    "gpu_count": 2,
    "gpu_model": "NVIDIA A100 80GB"
```

**Kết luận:** Đối chiếu được: cả `training-time-summary.csv` (dòng 1–4), `finetuning-summary.md` (dòng 57, 61–63) và ba file cấu hình training đều ghi 02 GPU NVIDIA A100 80GB cùng thời gian bắt đầu/kết thúc, số Epoch và tổng thời lượng (BGE-M3 22,5 h/5 epoch; Qwen3-Reranker 18,0 h/4 epoch; Qwen LLM 46,0 h/2 epoch).
