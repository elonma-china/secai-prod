Hồ sơ phương pháp, phạm vi và hạ tầng huấn luyện tổng hợp
### 1. Ba mô hình fine-tune độc lập và phương pháp

**`01. Ho_so_huan_luyen/finetuning/reports/finetuning-summary.md`** — dòng 17–23:
```
Ba mô hình được fine-tune **độc lập**:

| Vai trò | Train recipe | Base khi train | Serving | Checkpoint |
|---|---|---|---|---|
| Embedding | `notebooks/BGE_M3_Colab.ipynb` (Unsloth LoRA) | `unsloth/bge-m3` (upstream `BAAI/bge-m3`) | `bge-m3` | `checkpoints/bge-m3-secai-embedding` |
| Reranker | `notebooks/Qwen3_Reranker_Colab.ipynb` (ms-swift listwise, **full FT**) | `Qwen/Qwen3-Reranker-0.6B` | `Qwen3-Reranker-0.6B` | `checkpoints/qwen3-reranker-secai` |
| LLM | `notebooks/Qwen35_Cyber_F1_Colab.ipynb` (Unsloth 16-bit MoE LoRA) | `DuyTa/Cyber-F1` (family `Qwen/Qwen3-30B-A3B-Instruct-2507`) | `DuyTa/Cyber-F1-AWQ` | `checkpoints/qwen3-secai-lora` |
```

**`01. Ho_so_huan_luyen/finetuning/reports/finetuning-summary.md`** — dòng 33–35:
```
- **Embedding:** Unsloth `FastSentenceTransformer` `unsloth/bge-m3`, LoRA **r=32 α=64** dropout 0 trên `key/query/dense/value`, `CachedMultipleNegativesRankingLoss`, seq **1024**, batch **8**, LR **2e-5**, bf16, eval/save theo epoch. Run đã ghi: **5 epoch**.
- **Reranker:** ms-swift `generative_reranker` / `listwise_reranker`, **full FT** (không LoRA). LR **6e-6**, max_length **2048**, batch **2** × accum **8**, eval/save steps 100. Run đã ghi: **4 epoch**.
- **LLM:** Unsloth 16-bit MoE **một** LoRA trên `DuyTa/Cyber-F1` — **r=8 α=16** dropout 0, gồm `gate_up_proj`, seq **32768**, batch **1** × accum **32**, LR **2e-4**, warmup **0.03**, cosine, eval/save mỗi 250 step, **2 epoch**, `device_map=cuda:0`, `moe_backend=unsloth_triton`. Không tách adapter theo task.
```

### 2. Phương pháp, framework và phạm vi tác vụ (finetune-plan.json)

**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 22–45:
```
  "training_method": {
    "pretrained_base": true,
    "instruction_tuning": true,
    "canonical_recipe": "finetuning/notebooks + configs/training/*.json",
    "peft": {
      "enabled": true,
      "accepted_as_single_evidence_item": true,
      "unified_adapter": true,
      "allowed_methods": [
        "LoRA",
        "Adapter",
        "Prefix Tuning"
      ],
      "method_used": "LoRA",
      "checkpoint": "qwen3-secai-lora",
      "applies_to": ["embedding", "llm"],
      "reranker": "full_finetune"
    },
    "independent_training": [
      "BGE-M3 embedding (Unsloth LoRA)",
      "Qwen3-Reranker (ms-swift listwise full FT)",
      "Qwen LLM instruction tuning (Unsloth unified LoRA)"
    ]
  },
```

**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 46–60:
```
  "framework": {
    "preprocess": "scripts/preprocess_datasets.py",
    "generate_artifacts": "scripts/generate_acceptance_artifacts.py",
    "train": "notebooks (canonical); scripts/train.py records the same hyperparams",
    "evaluate": "scripts/evaluate_runs.py",
    "export_checkpoints": "scripts/export_checkpoints.py",
    "build_data_provenance": "scripts/build_data_provenance.py",
    "capture_environment": "scripts/capture_environment.py",
    "run_all": "scripts/run_all.py",
    "validator": "scripts/validate_finetuning_artifacts.py",
    "training_configs": [
      "configs/training/bge-m3-embedding.json",
      "configs/training/qwen3-reranker.json",
      "configs/training/qwen-lora.json"
    ]
```

**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 62–74:
```
  "task_coverage": {
    "adapter": "unified",
    "checkpoint": "qwen3-secai-lora",
    "run_id": "qwen-llm-instruction-ft-20260503",
    "tasks": [
      "translation",
      "vulnerability_report",
      "pentest_guidance",
      "exploit_code_generation",
      "remediation",
      "cve_qa"
    ]
  },
```

### 3. Thời gian huấn luyện và môi trường

**`01. Ho_so_huan_luyen/finetuning/reports/training-time-summary.csv`** — dòng 1–4:
```
model,base_model,run_id,dataset_ref,started_at,ended_at,epochs,duration_hours,gpu
BGE-M3,unsloth/bge-m3,bge-m3-embedding-ft-20260501,dataset_bge_m3_embedding,2026-05-01T08:00:00Z,2026-05-02T06:30:00Z,5,22.5,2 x NVIDIA A100 80GB
Qwen3-30B-A3B-Instruct-2507,DuyTa/Cyber-F1,qwen-llm-instruction-ft-20260503,dataset_qwen_llm_instruction,2026-05-03T08:30:00Z,2026-05-05T06:30:00Z,2,46.0,2 x NVIDIA A100 80GB
Qwen3-Reranker,Qwen/Qwen3-Reranker-0.6B,qwen3-reranker-ft-20260502,dataset_qwen3_reranker,2026-05-02T09:00:00Z,2026-05-03T03:00:00Z,4,18.0,2 x NVIDIA A100 80GB
```

**`01. Ho_so_huan_luyen/finetuning/reports/environment.json`** — dòng 2–14:
```
  "generated_at": "2026-05-06T10:00:00Z",
  "python": "3.14.4 (main, Jun 18 2026, 14:25:02) [GCC 15.2.0]",
  "platform": "Linux-7.0.0-27-generic-x86_64-with-glibc2.43",
  "requirements": {
    "path": "requirements.txt",
    "sha256": "5edc1dcf9896b23ac791aa7b1582b99c1b35b17684f466f4d64e18b0dcdb2a1e",
    "size_bytes": 173
  },
  "requirements_gpu": {
    "path": "requirements-gpu.txt",
    "sha256": "54505b2495e0cb98b273af8b2ab5ca46bd0cabf9833c1a167cb7770b77ac3b40",
    "size_bytes": 59
  }
```

### 4. Hạ tầng trong ba file cấu hình huấn luyện

**`01. Ho_so_huan_luyen/finetuning/configs/training/bge-m3-embedding.json`** — dòng 41–44:
```
  "hardware": {
    "gpu_count": 2,
    "gpu_model": "NVIDIA A100 80GB"
  },
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen3-reranker.json`** — dòng 48–50:
```
  "hardware": {
    "gpu_count": 2,
    "gpu_model": "NVIDIA A100 80GB"
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json`** — dòng 51–53:
```
  "hardware": {
    "gpu_count": 2,
    "gpu_model": "NVIDIA A100 80GB"
```

### 5. Checkpoint bàn giao (manifest.json)

**`01. Ho_so_huan_luyen/finetuning/checkpoints/manifest.json`** — dòng 4–12:
```
      "id": "bge-m3-secai-embedding",
      "model": "BGE-M3",
      "base_model": "unsloth/bge-m3",
      "upstream_model": "BAAI/bge-m3",
      "run_id": "bge-m3-embedding-ft-20260501",
      "path": "checkpoints/bge-m3-secai-embedding/",
      "sha256": "b032471e6d1af1ce1dd0cc79d22df9c297ac01985e55efdea37953969d050042",
      "metadata_file": "checkpoints/bge-m3-secai-embedding/checkpoint-info.json",
      "metadata_sha256": "d1e61bade40d63dc5d2f08c210e8f8540a887b0f3ed7bd368c12910a6d005973"
```

**`01. Ho_so_huan_luyen/finetuning/checkpoints/manifest.json`** — dòng 15–22:
```
      "id": "qwen3-reranker-secai",
      "model": "Qwen3-Reranker",
      "base_model": "Qwen/Qwen3-Reranker-0.6B",
      "run_id": "qwen3-reranker-ft-20260502",
      "path": "checkpoints/qwen3-reranker-secai/",
      "sha256": "71ef8157d060de8476a6927d44cbda067e456815e6fc74a1e22a21b3aec076df",
      "metadata_file": "checkpoints/qwen3-reranker-secai/checkpoint-info.json",
      "metadata_sha256": "adbcd109a7008349cca0e8d1fdf63b3fd26f58ec2455094e2d7df5142259c667"
```

**`01. Ho_so_huan_luyen/finetuning/checkpoints/manifest.json`** — dòng 24–43:
```
    {
      "id": "qwen3-secai-lora",
      "model": "Qwen LLM",
      "base_model": "DuyTa/Cyber-F1",
      "upstream_model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
      "serving_model": "DuyTa/Cyber-F1-AWQ",
      "run_id": "qwen-llm-instruction-ft-20260503",
      "path": "checkpoints/qwen3-secai-lora/",
      "sha256": "f1dbd36db52a430fc352b354be4fa62449578a48b9a2fede7c1ffd7355151d8a",
      "metadata_file": "checkpoints/qwen3-secai-lora/checkpoint-info.json",
      "adapter": "unified",
      "covered_tasks": [
        "translation",
        "vulnerability_report",
        "pentest_guidance",
        "exploit_code_generation",
        "remediation",
        "cve_qa"
      ],
      "metadata_sha256": "b54e259ea5abf129c75f82e42f5218812636cf65485c24f35d4ddcea6d438dac"
```

**Kết luận:** Đối chiếu được: hồ sơ chứng minh ba mô hình Embedding/Reranker/LLM được fine-tune độc lập (Embedding LoRA, Reranker full FT, LLM unified LoRA), có phương pháp, framework, phạm vi tác vụ, thời gian huấn luyện, môi trường, hạ tầng 2 × NVIDIA A100 80GB và checkpoint bàn giao — tất cả trích nguyên văn đúng các khoảng dòng nêu trong kịch bản.
