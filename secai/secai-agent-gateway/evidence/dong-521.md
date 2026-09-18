Bộ dữ liệu huấn luyện gồm các nhóm dữ liệu ATTT (thuật ngữ chuyên ngành, tài liệu RAG, instruction tuning, dữ liệu song ngữ, báo cáo và phân tích lỗ hổng), được chuẩn hóa và phân bổ riêng cho từng mô hình BGE-M3 embedding, BGE-Reranker và Qwen LLM. Thông tin chi tiết, số liệu chính xác (tên tập dữ liệu, số lượng bản ghi, dung lượng, nguồn, ngày công bố) và dữ liệu mẫu được cung cấp đầy đủ tại phụ lục kỹ thuật và khi nghiệm thu.

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — chuẩn hóa (dòng 3–8) và gán nhãn (dòng 9–16):
```
  "normalization": [
    "deduplicate",
    "schema validation",
    "language normalization",
    "security label review"
  ],
  "labeling": [
    "CVE-ID",
    "CWE-ID",
    "severity",
    "task",
    "expected answer",
    "source citation"
  ],
```

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — ba dataset theo mô hình (dòng 18–64):
```
    {
      "id": "dataset_bge_m3_embedding",
      "name": "BGE-M3 embedding CVE retrieval dataset",
      "model": "BGE-M3",
      "records": 50000,
      "size_mb": 38.8,
      "sources": [
        "NVD last 5 years",
        "MITRE CVE last 5 years",
        "MITRE CWE"
      ],
      "published_range": "2021-05-01..2026-04-30",
      "sample_file": "samples/bge_embedding_samples.jsonl",
      "training_file": "generated/dataset_bge_m3_embedding.jsonl"
    },
    {
      "id": "dataset_qwen3_reranker",
      "name": "Qwen3-Reranker query-document pairs",
      "model": "Qwen3-Reranker",
      "records": 42000,
      "size_mb": 54.5,
      "sources": [
        "NVD last 5 years",
        "MITRE CVE last 5 years",
        "Exploit-DB"
      ],
      "published_range": "2021-05-01..2026-04-30",
      "sample_file": "samples/reranker_samples.jsonl",
      "training_file": "generated/dataset_qwen3_reranker.jsonl"
    },
    {
      "id": "dataset_qwen_llm_instruction",
      "name": "Qwen LLM instruction dataset (REAL: HF cybersec + back-translation VI + tool-use, messages+system)",
      "model": "Qwen LLM",
      "records": 49756,
      "size_mb": 285.6,
      "sources": [
        "HuggingFace cybersec (Trendyol/Heimdall/Fenrir)",
        "Back-translation EN->VI (secAI LLM + QC bge-m3)",
        "Tool-use trajectories (secAI backend on Qdrant KB)",
        "NVD/MITRE CVE-CWE"
      ],
      "published_range": "2021-05-01..2026-04-30",
      "sample_file": "samples/qwen_instruction_samples.jsonl",
      "training_file": "generated/dataset_qwen_llm_instruction.jsonl",
      "format": "messages (chat, có system prompt secAI)"
    }
```

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — nhóm dữ liệu huấn luyện `dataset_groups` (dòng 66–91):
```
  "dataset_groups": [
    {
      "id": "cybersecurity_terms",
      "records": 4200,
      "description": "ATTT terms and bilingual glossary"
    },
    {
      "id": "rag_documents",
      "records": 62000,
      "description": "RAG documents from CVE/CWE/security references"
    },
    {
      "id": "instruction_tuning",
      "records": 28000,
      "description": "Instruction-answer samples"
    },
    {
      "id": "bilingual_security",
      "records": 9000,
      "description": "English-Vietnamese ATTT corpus"
    },
    {
      "id": "vulnerability_reports",
      "records": 13000,
      "description": "Vulnerability reports and analysis"
    }
  ],
```

**`01. Ho_so_huan_luyen/datasets/processed/manifest.json`** — số bản ghi và tập train/validation/test ba dataset (dòng 3–69):
```
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "source_manifest": "datasets/manifest.json",
      "records_declared": 50000,
      "source_file": "generated/dataset_bge_m3_embedding.jsonl",
      "records_processed": 50000,
      "train_path": "datasets/processed/dataset_bge_m3_embedding/train.jsonl",
      "validation_path": "datasets/processed/dataset_bge_m3_embedding/validation.jsonl",
      "test_path": "datasets/processed/dataset_bge_m3_embedding/test.jsonl",
      "train_records": 40000,
      "validation_records": 5000,
      "test_records": 5000,
      ...
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      ...
      "train_records": 33600,
      "validation_records": 4200,
      "test_records": 4200,
      ...
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      ...
      "train_records": 39804,
      "validation_records": 4976,
      "test_records": 4976,
      ...
    }
```

**`01. Ho_so_huan_luyen/finetuning/reports/data-provenance.json`** — checksum/dung lượng manifest (dòng 4–8) và ba dataset generated (dòng 9–33):
```
  "datasets_manifest": {
    "path": "datasets/manifest.json",
    "sha256": "f80648a597c6357acba93374e2ceb61aa9c65380421fbc11e94bb98810dd2d82",
    "size_bytes": 3263
  },
  "generated_datasets": [
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "declared_records": 50000,
      "path": "datasets/generated/dataset_bge_m3_embedding.jsonl",
      "sha256": "41360a9b75ef272eb7c27b3c2e9f1ce1f4466a4ad07c59d0058dc0f97a7a8158",
      "size_bytes": 38798473,
      "rows": 50000
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      "declared_records": 42000,
      "path": "datasets/generated/dataset_qwen3_reranker.jsonl",
      "sha256": "d143f0ce09e88b1f3deda3784f10ea48199f15b8089e1244d9918edcc4220f57",
      "size_bytes": 54495572,
      "rows": 42000
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      "declared_records": 49756,
      "path": "datasets/generated/dataset_qwen_llm_instruction.jsonl",
      "sha256": "38f5a97c15ce9b10d38c58798da78e9b628b589451940ccce1f041ec8ad051dc",
      "size_bytes": 285581744,
      "rows": 49756
    }
  ],
```

**`01. Ho_so_huan_luyen/finetuning/reports/data-provenance.json`** — dữ liệu mẫu và checksum (dòng 130–151):
```
  "sample_datasets": [
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "path": "datasets/samples/bge_embedding_samples.jsonl",
      "sha256": "3d593f87ef839e11f2a859c18a242464bee0de0e139b2dd7847a7cd9238e77d5",
      "size_bytes": 15804,
      "rows": 20
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      "path": "datasets/samples/reranker_samples.jsonl",
      "sha256": "a75541e1d0be5b8825a00213357d717f92cce6e4e3777156ca68c29086268679",
      "size_bytes": 27550,
      "rows": 20
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      "path": "datasets/samples/qwen_instruction_samples.jsonl",
      "sha256": "0e423a2518bac30eff5eae88b5149b9643658aba70950e69a4d511e6533106a3",
      "size_bytes": 106211,
      "rows": 20
    }
  ],
```

**`01. Ho_so_huan_luyen/datasets/phu-luc-ky-thuat-v1.md`** — vai trò/đường dẫn ba dataset (dòng 9–13) và quy mô/phân chia (dòng 17–24):
```
| Dataset | Vai trò | Mô hình sử dụng | Đường dẫn |
|---|---|---|---|
| `dataset_bge_m3_embedding` | Huấn luyện truy hồi embedding | BGE-M3 | `datasets/processed/dataset_bge_m3_embedding/` |
| `dataset_qwen3_reranker` | Huấn luyện xếp hạng lại | Qwen3-Reranker | `datasets/processed/dataset_qwen3_reranker/` |
| `dataset_qwen_llm_instruction` | Instruction tuning và agentic tool-use | Qwen LLM | `datasets/processed/dataset_qwen_llm_instruction/` |
```
```
| Dataset | Train | Validation | Test | Tổng |
|---|---:|---:|---:|---:|
| `dataset_bge_m3_embedding` | 40.000 | 5.000 | 5.000 | **50.000** |
| `dataset_qwen3_reranker` | 33.600 | 4.200 | 4.200 | **42.000** |
| `dataset_qwen_llm_instruction` | 39.804 | 4.976 | 4.976 | **49.756** |
| **Tổng số bản ghi theo ba dataset mô hình** | **113.404** | **14.176** | **14.176** | **141.756** |
```

**Kết luận:** Đối chiếu được: `datasets/manifest.json` (nhóm dữ liệu ATTT, chuẩn hóa, gán nhãn, ba dataset theo mô hình), `datasets/processed/manifest.json` (số bản ghi + train/validation/test), `finetuning/reports/data-provenance.json` (checksum/dung lượng generated + mẫu) và `datasets/phu-luc-ky-thuat-v1.md` (vai trò, quy mô, phân chia) thống nhất về ba dataset riêng cho BGE-M3, Qwen3-Reranker và Qwen LLM; số bản ghi, dung lượng, nguồn, phạm vi công bố, dữ liệu mẫu và checksum đều đối chiếu được.
