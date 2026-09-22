# Gói fine-tune secAI

Đây là gói bàn giao độc lập cho Cyber-F1 v2, BGE-M3 và Qwen3-Reranker. Gói chỉ giữ các split
`train`/`validation`/`test` bất biến, code train, code đánh giá và notebook Colab; không có
pipeline sinh dữ liệu, curation, deduplication, filtering hay hard-negative mining.

## Cấu trúc

```
data/embedding/{train,validation,test}.jsonl
data/reranker/{train,validation,test}.jsonl
data/llm/{train,validation,test}.jsonl
configs/qwen35_cyber_f1.json
configs/finetune_thresholds.yml
src/secai_ft/{tools_schema.py,trainers/unsloth_sft.py}
scripts/train_llm.py
scripts/train_embedding.py
scripts/train_reranker.sh
scripts/eval_embedding.py
scripts/eval_reranker.py
scripts/export_llm_qa_prompts.py
scripts/eval_llm_qa.py
scripts/check_finetune_thresholds.py
```

Bản ghi embedding cần `record_id`, `query`, `positive`, `label`. Bản ghi reranker cần
`record_id`, `query`, `positive`, `negative` (chuỗi hoặc danh sách).

## Cyber-F1 v2 — Qwen3-30B-A3B-Instruct-2507 MoE

`data/llm` là toàn bộ split [Cyber_F1_v2](https://huggingface.co/datasets/DuyTa/Cyber_F1_v2):
47.034 train, 5.877 validation và 5.885 test. Mô tả chi tiết nguồn gốc, schema và giới hạn ở
[DATASET_CYBER_F1_V2.md](DATASET_CYBER_F1_V2.md).

Data dùng `messages` kiểu OpenAI. Assistant `content: null` chỉ hợp lệ khi message đó có
`tool_calls`. `tools_schema.py` snapshot đúng bốn tool của release: `qdrant_search`,
`neo4j_search`, `poc_search`, `poc_fetch`; phải đồng bộ với harness khi deploy.

Trainer single-GPU đã kiểm thử cần GPU 96 GB: bf16 LoRA rank 8, AdamW 8-bit, Unsloth gradient
checkpointing, context cap 32.768 token, backend Triton MoE cho Blackwell và fallback riêng cho
Torch 2.8/Blackwell grouped-MM.

### Chạy LLM

Smoke trên Colab:

```bash
python scripts/prepare_llm_colab_bundle.py
```

Sau đó mở `notebooks/Qwen35_Cyber_F1_Colab.ipynb`. Chạy đầy đủ tại máy local:

```bash
PYTHONPATH=src python scripts/train_llm.py
```

Trước khi chạy tốn GPU, kiểm tra context thực tế sau chat template và tool schema:

```bash
PYTHONPATH=src python scripts/audit_llm_context.py \
  --model DuyTa/Cyber-F1 --dataset data/llm/train.jsonl --max-seq-length 32768
```

### Đánh giá LLM qua harness

`../benchmarks/data/llm_qa_harness_v1.jsonl` là bộ 1.000 Q/A held-out. File chỉ có `id`,
`question`, `answer`, tuyệt đối không có system prompt, tool call, tool input hay tool result.
Harness chỉ nhận `question`, tự gọi tool cấu hình của nó và trả candidate answer. Gold `answer`
chỉ được dùng ở scorer.

Xuất prompt an toàn (không lộ gold answer) rồi chạy bằng harness/runtime:

```bash
python scripts/export_llm_qa_prompts.py --output outputs/llm-qa-prompts.jsonl
# Harness cần trả JSONL: {"id": "...", "answer": "candidate answer"}
python scripts/eval_llm_qa.py \
  --predictions outputs/llm-qa-predictions.jsonl \
  --judge-url "$JUDGE_BASE_URL/v1/chat/completions" \
  --judge-model <model-judge-doc-lap> \
  --output ../benchmarks/reports/llm-qa.json
```

Judge phải là model độc lập với model đang đo. Scorer tính accuracy (mẫu đạt khi điểm correctness
≥0,75), mean correctness để chẩn đoán, answer relevance, CVE/CWE entity precision và
hallucination rate.

## BGE-M3

Dùng bộ cài Colab/Unsloth đã pin trong `notebooks/BGE_M3_Colab.ipynb`, rồi chạy:

```bash
python scripts/train_embedding.py --output-dir outputs/bge-m3
python scripts/eval_embedding.py --model outputs/bge-m3 \
  --test data/embedding/test.jsonl --output ../benchmarks/reports/embedding.json
```

Evaluator trả `recall@1/5/10`, `mrr@10`, `ndcg@10`, `map@100`.

## Qwen3-Reranker-0.6B

Cài SWIFT tương thích rồi khởi động lại runtime:

```bash
pip install -U ms-swift 'datasets==4.8.4'
```

`USE_HF=1` buộc SWIFT tải model từ Hugging Face thay vì ModelScope.

```bash
bash scripts/train_reranker.sh
python scripts/eval_reranker.py \
  --model outputs/qwen3-reranker-06b/<run-or-checkpoint> \
  --test data/reranker/test.jsonl \
  --output ../benchmarks/reports/reranker.json
```

Evaluator trả pairwise accuracy, `precision@1`, `recall@1/2`, `mrr@2`, `ndcg@2`, `map@2`.
Mỗi nhóm test có đúng một positive và một hay nhiều negative.

## Ngưỡng kích hoạt fine-tune

`configs/finetune_thresholds.yml` là policy có version, viết ở dạng YAML tương thích JSON nên chỉ
cần standard library Python. Ngưỡng được rút từ KPI của `SecAI-Platform-main`: Hit@10 >96%,
reranker P@1 >90%, answer relevance >90%, entity precision =100%, hallucination <2%, accuracy
≥85%. Lưu ý: test embedding/reranker local chỉ phát hiện regression của model, không thay thế KPI
Qdrant end-to-end của Platform.

Sau khi có các report, chạy gate cho đúng component vừa đo:

```bash
python scripts/check_finetune_thresholds.py \
  --report embedding=../benchmarks/reports/embedding.json \
  --report reranker=../benchmarks/reports/reranker.json \
  --report llm=../benchmarks/reports/llm-qa.json
```

Gate ghi `../benchmarks/reports/finetune-decision.json`:

- `FINETUNE_REQUIRED`: nêu rõ target lỗi (`embedding`, `reranker`, hoặc `llm`);
- `NO_FINETUNE_REQUIRED`: mọi report được cung cấp đều qua ngưỡng;
- `INSUFFICIENT_EVIDENCE`: không có report dùng được.

Gate không tự khởi động job tốn GPU. Sau khi review một run tốt, mới chủ động chấp nhận baseline
để gate đo thêm suy giảm tương đối:

```bash
python scripts/approve_finetune_baseline.py --approve \
  --report embedding=../benchmarks/reports/embedding.json \
  --report reranker=../benchmarks/reports/reranker.json \
  --report llm=../benchmarks/reports/llm-qa.json
```
