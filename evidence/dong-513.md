Xác nhận Base Model
**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 9–14:
```json
  "base_models": {
    "llm": "DuyTa/Cyber-F1",
    "llm_upstream": "Qwen/Qwen3-30B-A3B-Instruct-2507",
    "embedding": "unsloth/bge-m3",
    "embedding_upstream": "BAAI/bge-m3",
    "reranker": "Qwen/Qwen3-Reranker-0.6B"
```

**`01. Ho_so_huan_luyen/finetuning/reports/finetuning-summary.md`** — dòng 19–23:
```
| Vai trò | Train recipe | Base khi train | Serving | Checkpoint |
|---|---|---|---|---|
| Embedding | `notebooks/BGE_M3_Colab.ipynb` (Unsloth LoRA) | `unsloth/bge-m3` (upstream `BAAI/bge-m3`) | `bge-m3` | `checkpoints/bge-m3-secai-embedding` |
| Reranker | `notebooks/Qwen3_Reranker_Colab.ipynb` (ms-swift listwise, **full FT**) | `Qwen/Qwen3-Reranker-0.6B` | `Qwen3-Reranker-0.6B` | `checkpoints/qwen3-reranker-secai` |
| LLM | `notebooks/Qwen35_Cyber_F1_Colab.ipynb` (Unsloth 16-bit MoE LoRA) | `DuyTa/Cyber-F1` (family `Qwen/Qwen3-30B-A3B-Instruct-2507`) | `DuyTa/Cyber-F1-AWQ` | `checkpoints/qwen3-secai-lora` |
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/bge-m3-embedding.json`** — dòng 5–6:
```json
  "base_model": "unsloth/bge-m3",
  "upstream_model": "BAAI/bge-m3",
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen3-reranker.json`** — dòng 5:
```json
  "base_model": "Qwen/Qwen3-Reranker-0.6B",
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json`** — dòng 6–8:
```json
  "base_model": "DuyTa/Cyber-F1",
  "upstream_model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
  "serving_model": "DuyTa/Cyber-F1-AWQ",
```

**Kết luận:** Đối chiếu được: hồ sơ xác nhận LLM base `DuyTa/Cyber-F1` (upstream `Qwen/Qwen3-30B-A3B-Instruct-2507`, serving `DuyTa/Cyber-F1-AWQ`), Embedding base `unsloth/bge-m3` (upstream `BAAI/bge-m3`) và Reranker `Qwen/Qwen3-Reranker-0.6B` — khớp các file và khoảng dòng nêu trên.
