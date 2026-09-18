Hồ sơ phương pháp và phạm vi fine-tuning
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

**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 26–38:
```json
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
```

**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 40–44:
```json
    "independent_training": [
      "BGE-M3 embedding (Unsloth LoRA)",
      "Qwen3-Reranker (ms-swift listwise full FT)",
      "Qwen LLM instruction tuning (Unsloth unified LoRA)"
    ]
```

**`01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json`** — dòng 62–74:
```json
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

**`01. Ho_so_huan_luyen/finetuning/configs/training/bge-m3-embedding.json`** — dòng 32–39:
```json
  "peft": {
    "method": "LoRA",
    "r": 32,
    "alpha": 64,
    "dropout": 0.0,
    "bias": "none",
    "target_modules": ["key", "query", "dense", "value"],
    "task_type": "FEATURE_EXTRACTION"
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen3-reranker.json`** — dòng 17–23:
```json
  "training_engine": "ms-swift sft generative_reranker listwise full FT",
  "tuner_type": "full",
  "prompt_format": "SWIFT grouped schema via prepare_reranker_swift_data.py; native Qwen template rendered by SWIFT",
  "swift": {
    "task_type": "generative_reranker",
    "loss_type": "listwise_reranker",
    "tuner_type": "full",
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json`** — dòng 35–40:
```json
  "peft": {
    "method": "LoRA",
    "r": 8,
    "alpha": 16,
    "dropout": 0.0,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "gate_up_proj", "down_proj"]
```

**`01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json`** — dòng 42–49:
```json
  "adapter": "unified",
  "covered_tasks": [
    "translation",
    "vulnerability_report",
    "pentest_guidance",
    "exploit_code_generation",
    "remediation",
    "cve_qa"
```

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — dòng 93–121:
```json
  "task_datasets": {
    "task_translation": {
      "records": 4200,
      "groups": [
        "bilingual_security",
        "cybersecurity_terms"
      ]
    },
    "task_vulnerability_report": {
      "records": 5600,
      "groups": [
        "vulnerability_reports",
        "rag_documents"
      ]
    },
    "task_pentest_guidance": {
      "records": 4800,
      "groups": [
        "instruction_tuning",
        "vulnerability_reports"
      ]
    },
    "task_exploit_code_generation": {
      "records": 3900,
      "groups": [
        "instruction_tuning",
        "vulnerability_reports"
      ]
    }
```

**`01. Ho_so_huan_luyen/finetuning/checkpoints/manifest.json`** — dòng 24–43:
```json
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

**Kết luận:** Đối chiếu được: hồ sơ thể hiện ba luồng huấn luyện độc lập (Embedding LoRA, Reranker full FT, LLM unified LoRA), cấu hình PEFT/LoRA, dữ liệu LLM phân nhóm theo tác vụ và một unified adapter bao phủ các tác vụ — khớp các file và khoảng dòng nêu trên.
