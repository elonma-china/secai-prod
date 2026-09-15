Hồ sơ dataset huấn luyện, nhóm dữ liệu và nguồn dữ liệu
### 1. Nhóm dữ liệu huấn luyện (dataset_groups) — datasets/manifest.json

Kịch bản nêu 5 nhóm: thuật ngữ ATTT (kịch bản dòng 68–71), tài liệu RAG (73–76), instruction-tuning (78–81), song ngữ (83–86), báo cáo/phân tích lỗ hổng (88–91). Khối `dataset_groups` thực tế trong file là dòng 66–92 (mỗi object mở ngoặc `{` sớm hơn 1 dòng so với số kịch bản):

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — dòng 66–92:
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

### 2. Nguồn dữ liệu và phạm vi công bố — datasets/manifest.json

Nguồn NVD / MITRE CVE / MITRE CWE + phạm vi công bố (dòng 24–29):

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — dòng 24–29:
```
      "sources": [
        "NVD last 5 years",
        "MITRE CVE last 5 years",
        "MITRE CWE"
      ],
      "published_range": "2021-05-01..2026-04-30",
```

Nguồn NVD / MITRE CVE / Exploit-DB + phạm vi công bố (dòng 39–44):

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — dòng 39–44:
```
      "sources": [
        "NVD last 5 years",
        "MITRE CVE last 5 years",
        "Exploit-DB"
      ],
      "published_range": "2021-05-01..2026-04-30",
```

Nguồn HuggingFace cybersec, back-translation EN→VI, tool-use trajectories, NVD/MITRE + phạm vi công bố (dòng 54–60):

**`01. Ho_so_huan_luyen/datasets/manifest.json`** — dòng 54–60:
```
      "sources": [
        "HuggingFace cybersec (Trendyol/Heimdall/Fenrir)",
        "Back-translation EN->VI (secAI LLM + QC bge-m3)",
        "Tool-use trajectories (secAI backend on Qdrant KB)",
        "NVD/MITRE CVE-CWE"
      ],
      "published_range": "2021-05-01..2026-04-30",
```

### 3. Provenance: đường dẫn, checksum, dung lượng, số bản ghi — reports/data-provenance.json

Thông tin manifest (đường dẫn, checksum, dung lượng) — dòng 4–8:

**`01. Ho_so_huan_luyen/finetuning/reports/data-provenance.json`** — dòng 4–8:
```
  "datasets_manifest": {
    "path": "datasets/manifest.json",
    "sha256": "f80648a597c6357acba93374e2ceb61aa9c65380421fbc11e94bb98810dd2d82",
    "size_bytes": 3263
  },
```

Các dataset được tạo (số bản ghi khai báo, đường dẫn, checksum, dung lượng, số dòng) — dòng 9–34:

**`01. Ho_so_huan_luyen/finetuning/reports/data-provenance.json`** — dòng 9–34:
```
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

Các tập dữ liệu đã xử lý (đường dẫn, checksum, dung lượng, số dòng) — kịch bản nêu dòng 35–128, khối thực tế 35–129:

**`01. Ho_so_huan_luyen/finetuning/reports/data-provenance.json`** — dòng 35–129:
```
  "processed_datasets": [
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "split": "train",
      "path": "datasets/processed/dataset_bge_m3_embedding/train.jsonl",
      "sha256": "3a3361c5a1822c98b8a58322aac549713a2fe1cbcb7175ac6c014125bbf10785",
      "size_bytes": 29612786,
      "rows": 40000
    },
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "split": "validation",
      "path": "datasets/processed/dataset_bge_m3_embedding/validation.jsonl",
      "sha256": "9fc70267b1d06aad64be768b93c11841a2b98cfb60424d298169c0572a8151f0",
      "size_bytes": 3685754,
      "rows": 5000
    },
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "split": "test",
      "path": "datasets/processed/dataset_bge_m3_embedding/test.jsonl",
      "sha256": "d0032958464b37137994c6a04670751aff4861d47705cc3fbbd0ac203a85e027",
      "size_bytes": 3728704,
      "rows": 5000
    },
    {
      "dataset_id": "dataset_bge_m3_embedding",
      "split": "dataset-card",
      "path": "datasets/processed/dataset_bge_m3_embedding/dataset-card.json",
      "sha256": "235a1895d1daf91e6dcfaf64dd116bf58dc8eac59ae3336f597647c98da8e88a",
      "size_bytes": 654
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      "split": "train",
      "path": "datasets/processed/dataset_qwen3_reranker/train.jsonl",
      "sha256": "a3cfc36603c6e8201165ee8e6d702aeda0c08dab5fed07c34271703b40316ae9",
      "size_bytes": 42892265,
      "rows": 33600
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      "split": "validation",
      "path": "datasets/processed/dataset_qwen3_reranker/validation.jsonl",
      "sha256": "435392b910760b84640ee45b6eaad3e448ad1083a76d16de07bef28725135087",
      "size_bytes": 5382224,
      "rows": 4200
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      "split": "test",
      "path": "datasets/processed/dataset_qwen3_reranker/test.jsonl",
      "sha256": "e1c6e8cf426c2c9d8a57d40df000daad5a1d43202d6f9bfdb829cd3b0bcf1dbf",
      "size_bytes": 5337592,
      "rows": 4200
    },
    {
      "dataset_id": "dataset_qwen3_reranker",
      "split": "dataset-card",
      "path": "datasets/processed/dataset_qwen3_reranker/dataset-card.json",
      "sha256": "a8aff0d1909bd7d02db9620916e3226ae8d15b805b708a8e9cb8cef5d0f5e736",
      "size_bytes": 657
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      "split": "train",
      "path": "datasets/processed/dataset_qwen_llm_instruction/train.jsonl",
      "sha256": "333fc3b169b21f21e6423bb771fccea89a6867816f60fa63ca076c8f5ff268d7",
      "size_bytes": 228709280,
      "rows": 39804
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      "split": "validation",
      "path": "datasets/processed/dataset_qwen_llm_instruction/validation.jsonl",
      "sha256": "47008d9624a3078132716539da118bf1a907d2ba39b6529da54a2afb89d9badc",
      "size_bytes": 28265812,
      "rows": 4976
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      "split": "test",
      "path": "datasets/processed/dataset_qwen_llm_instruction/test.jsonl",
      "sha256": "86cf9fb05b94f8e9e650bd1d5a0322de78f330c7593d69db4e384ca8d3e99119",
      "size_bytes": 28606652,
      "rows": 4976
    },
    {
      "dataset_id": "dataset_qwen_llm_instruction",
      "split": "dataset-card",
      "path": "datasets/processed/dataset_qwen_llm_instruction/dataset-card.json",
      "sha256": "ba9f8e2d10f311ef4a1284c282592eff6433875463ab605beba3a46d1169597c",
      "size_bytes": 1710
    }
  ],
```

Các file dữ liệu mẫu (đường dẫn, checksum, dung lượng, số dòng) — kịch bản nêu dòng 130–151, khối thực tế 130–152:

**`01. Ho_so_huan_luyen/finetuning/reports/data-provenance.json`** — dòng 130–152:
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

**Kết luận:** Đối chiếu được: `datasets/manifest.json` khai báo đủ 5 nhóm dữ liệu và các nguồn NVD/MITRE CVE/MITRE CWE/Exploit-DB/HuggingFace/back-translation/tool-use kèm phạm vi công bố; `reports/data-provenance.json` cung cấp đường dẫn, checksum, dung lượng và số bản ghi/số dòng cho manifest, dataset được tạo, tập đã xử lý và file mẫu — trích nguyên văn đúng các khoảng dòng nêu trong kịch bản (có ghi chú lệch 1 dòng do vị trí dấu ngoặc).
