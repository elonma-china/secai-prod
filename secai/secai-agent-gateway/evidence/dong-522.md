Chỉ số đánh giá và nghiệm thu: Thời gian huấn luyện/fine-tuning trên từng tập dữ liệu được ghi nhận cụ thể theo cấu hình phần cứng triển khai (GPU) và đối soát trong quá trình huấn luyện.

Kịch bản cột F ghi "(bên trên)" — dùng chung kịch bản chứng minh với dòng 520 (mở `01. Ho_so_huan_luyen/finetuning/`, đối chiếu thời gian huấn luyện/fine-tuning từng dataset theo GPU). Trích các file thời gian huấn luyện được nêu:

**`01. Ho_so_huan_luyen/finetuning/reports/training-time-summary.csv`** — dòng 1–4 (thời gian huấn luyện từng tập dữ liệu theo GPU):
```
model,base_model,run_id,dataset_ref,started_at,ended_at,epochs,duration_hours,gpu
BGE-M3,unsloth/bge-m3,bge-m3-embedding-ft-20260501,dataset_bge_m3_embedding,2026-05-01T08:00:00Z,2026-05-02T06:30:00Z,5,22.5,2 x NVIDIA A100 80GB
Qwen3-30B-A3B-Instruct-2507,DuyTa/Cyber-F1,qwen-llm-instruction-ft-20260503,dataset_qwen_llm_instruction,2026-05-03T08:30:00Z,2026-05-05T06:30:00Z,2,46.0,2 x NVIDIA A100 80GB
Qwen3-Reranker,Qwen/Qwen3-Reranker-0.6B,qwen3-reranker-ft-20260502,dataset_qwen3_reranker,2026-05-02T09:00:00Z,2026-05-03T03:00:00Z,4,18.0,2 x NVIDIA A100 80GB
```

Cột `dataset_ref` gắn từng thời lượng huấn luyện với đúng tập dữ liệu (`dataset_bge_m3_embedding`, `dataset_qwen_llm_instruction`, `dataset_qwen3_reranker`); cột `gpu` ghi cấu hình phần cứng `2 x NVIDIA A100 80GB`; cột `duration_hours` ghi tổng thời lượng (22,5 / 46,0 / 18,0 giờ).

**`01. Ho_so_huan_luyen/finetuning/reports/finetuning-summary.md`** — dòng 57, 59–63 (đối soát trong nhật ký huấn luyện):
```
Hạ tầng: **2 × NVIDIA A100 80GB** (LLM notebook yêu cầu ≥ 80 GB VRAM cho LoRA 16-bit MoE).

| Run | Thời gian (UTC) | Epoch | Train loss cuối | Eval loss tốt nhất |
|---|---|---:|---:|---:|
| `bge-m3-embedding-ft-20260501` | 2026-05-01 08:00 → 2026-05-02 06:30 (22,5 h) | 5 | 0.2012 | 0.1868 |
| `qwen3-reranker-ft-20260502` | 2026-05-02 09:00 → 2026-05-03 03:00 (18,0 h) | 4 | 0.1495 | 0.1393 |
| `qwen-llm-instruction-ft-20260503` | 2026-05-03 08:30 → 2026-05-05 06:30 (46,0 h) | 2 | 0.6720 | 0.7307 |
```

**Kết luận:** Đối chiếu được: thời gian huấn luyện/fine-tuning của từng tập dữ liệu được ghi cụ thể theo cấu hình phần cứng (2 × NVIDIA A100 80GB) trong `training-time-summary.csv` (cột `dataset_ref` + `gpu` + `duration_hours`) và được đối soát lại trong `finetuning-summary.md` (dòng 57, 61–63).
