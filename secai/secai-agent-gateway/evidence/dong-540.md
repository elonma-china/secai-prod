Context Window tối đa: 16K tokens
File `code/.env.example` (biến `LLM_CONTEXT` tại dòng 35) mà kịch bản yêu cầu mở thuộc gói mã nguồn/triển khai, không nằm trong hồ sơ gốc phần AI (4 thư mục 01–04).

Kết quả thật thay thế — thông số context của mô hình được khai báo ngay trong hồ sơ huấn luyện. `01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json` (trích nguyên văn, cắt giữa ghi [...]):

```
"base_model": "DuyTa/Cyber-F1",
"upstream_model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
"serving_model": "DuyTa/Cyber-F1-AWQ",
"model_family": "Qwen3-30B-A3B-Instruct-2507",
[...]
"max_seq_length": 32768,
```

Giá trị `max_seq_length` = 32768 token (32K), lớn hơn mốc yêu cầu context window ≥ 16K token; mô hình huấn luyện/serving là DuyTa/Cyber-F1 → DuyTa/Cyber-F1-AWQ (họ Qwen3-30B-A3B-Instruct-2507).

**Kết luận:** Cấu hình context của mô hình đạt `max_seq_length` = 32768 token trong qwen-lora.json, vượt mốc yêu cầu ≥ 16K token.
