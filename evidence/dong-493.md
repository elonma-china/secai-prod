Danh mục mô hình runtime phục vụ hệ thống
Danh mục mô hình runtime đang phục vụ hệ thống gồm 03 mô hình — một LLM, một embedding, một reranker — đều là biến thể fine-tuned chuyên biệt cho ATTT (không dùng mô hình gốc mặc định).

Sổ tay checkpoint fine-tuning (`HO-SO-GOC-NGHIEM-THU-AI/01. Ho_so_huan_luyen/finetuning/checkpoints/manifest.json`) ghi lại phả hệ 03 checkpoint kèm mô hình phục vụ (trích nguyên văn các trường định danh):

- LLM: `base_model=DuyTa/Cyber-F1`, `serving_model=DuyTa/Cyber-F1-AWQ`, `covered_tasks=["translation", "vulnerability_report", "pentest_guidance", "exploit_code_generation", "remediation", "cve_qa"]`
- Embedding: `id=bge-m3-secai-embedding`, `base_model=unsloth/bge-m3`, `upstream_model=BAAI/bge-m3`
- Reranker: `id=qwen3-reranker-secai`, `base_model=Qwen/Qwen3-Reranker-0.6B`

Tên mô hình runtime thực tế được nêu nguyên văn trong `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/Perf/evaluation_code/.env.example`:

```
LLM_MODEL=<vd: cpatonn/DuyTa/Cyber-F1-AWQ-Instruct-2507-AWQ-4bit>
EMBEDDING_MODEL=DuyTa/sec-embedding
RERANKER_MODEL=DuyTa/sec-rerank
ACC_MODEL=DuyTa/Cyber-F1-AWQ
```

Hướng dẫn cài đặt (`HO-SO-GOC-NGHIEM-THU-AI/04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`) xác nhận kiến trúc phục vụ 03 mô hình (trích nguyên văn): "1. **Serving GPU** (thuê trên vast.ai): phục vụ ba mô hình gồm mô hình ngôn ngữ (LLM), mô hình embedding và mô hình reranker."

Kết quả đo KPI nhóm "models" xác nhận cả 03 mô hình runtime đang chạy và đạt ngưỡng (trích nguyên văn `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/kpi-summary.json`):

```json
{
"R-MODEL-EMB1200": {
  "name": "Embedding DuyTa/sec-embedding throughput (host)",
  "unit": " emb/s",
  "thr": "≥ 1200",
  "value": 2662.4,
  "pass": true,
  "group": "models"
},
"R-MODEL-RERANK180": {
  "name": "Reranker DuyTa/sec-rerank throughput (host)",
  "unit": " docs/s",
  "thr": "≥ 180",
  "value": 4862.4,
  "pass": true,
  "group": "models"
},
"R-LLM-TPS28": {
  "name": "DuyTa/Cyber-F1-AWQ LLM throughput",
  "unit": " tok/s",
  "thr": "≥ 28",
  "value": 55.2,
  "pass": true,
  "group": "models"
}
}
```

Hai tệp `env.example` (dòng 13/18/23) và `code/restore.sh` (dòng 55) nêu trong mẫu kiểm thử thuộc gói mã nguồn/triển khai (secai-deploy), không nằm trong 04 thư mục hồ sơ gốc phần AI.

**Kết luận:** Danh mục mô hình runtime gồm 03 biến thể fine-tuned — LLM `DuyTa/Cyber-F1-AWQ`, embedding `DuyTa/sec-embedding`, reranker `DuyTa/sec-rerank` — được ghi nhận thống nhất trong manifest fine-tuning, cấu hình đo KPI và hướng dẫn cài đặt, và đã đạt ngưỡng throughput khi đo kiểm.
