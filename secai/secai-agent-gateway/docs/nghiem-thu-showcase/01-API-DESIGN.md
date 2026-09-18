# API — Showcase hồ sơ nghiệm thu AI (Stage 1, đã triển khai)

> Base URL demo hiện tại `http://127.0.0.1:8199` (production: `http://<gateway>:8131`). JSON, **không cần header xác thực** (đồng bộ các endpoint `/ai/*` hiện có — xem `docs/api-guide.md`). GET là chính, chỉ §L dùng POST.
>
> **Mọi số liệu đọc từ HỒ SƠ GỐC** `HO-SO-GOC-NGHIEM-THU-AI/` (mirror Drive `17Qq9hXVl...`, đã tải đủ 394 file) — không tính lại, không lấy từ bản làm việc cục bộ `FINETUNE/` hay `KPI-VA-GEN-TEST/`. Kiểm tra bằng `python3 scripts/audit_hosogoc.py --api <base>`.

**Phạm vi Stage 1:** nhóm F (finetune), D (model & phần cứng), K (KPI bench), L (sanitize demo), E (evidence curated) — tổng **15 endpoint**. Nhóm KB (log đồng bộ KB) và nhóm S (duyệt hồ sơ gốc nguyên bản) **chưa làm ở Stage 1**.

Khung lỗi chung: `{ "error": "...", "message": "..." }`, `404` không tìm thấy, `400`/`422` tham số sai.

---

## Nhóm F — Finetuning evidence (`/ai/finetune/*`)

Nguồn: **hồ sơ gốc** `01. Ho_so_huan_luyen/` (`finetuning/` + `datasets/`). 7 endpoint, tất cả GET, chỉ đọc.

| # | Endpoint | Trả về |
|---|---|---|
| F1 | `GET /ai/finetune/datasets` | 3 dataset (id, component, name, model, `records`, `size_mb`, sources, published_range) + tóm tắt run (`run_id`, base_model, started/ended, duration_hours, total_steps, final_train_loss, best_eval_loss). Nguồn `datasets/manifest.json` + `finetuning/reports/training-evidence-index.json`. **Không có `size_tier`** — chỉ trả số lượng/dung lượng thật. |
| F2 | `GET /ai/finetune/datasets/{id}/sample?limit=5` | 5–20 dòng mẫu thật từ `datasets/samples/*.jsonl` (không trả file training gốc). |
| F3 | `GET /ai/finetune/runs/{run_id}/log?granularity=epoch\|step&max_points=300` | `points` (dữ liệu vẽ chart) + `log_stream` + `training_summary` — xem bên dưới. |
| F4 | `GET /ai/finetune/runs/{run_id}/charts` | 6 ảnh chart/run từ `finetuning/dashboards/figures/` (phục vụ qua mount `/ai/finetune/assets/*`) + **`tensorboard_url`** trỏ thẳng TensorBoard thật đang chạy, lọc sẵn đúng run. |
| F5 | `GET /ai/finetune/kpi-baseline` | Nguyên văn `finetuning/reports/approved_metrics.json`. |
| F6 | `GET /ai/finetune/dataset-appendix` | **Nguyên văn** `datasets/phu-luc-ky-thuat-v1.md` (split train/val/test, SHA-256 từng file, schema, quy tắc nghiệm thu). |
| F7 | `GET /ai/finetune/data-processing` | Làm sạch + gán nhãn — xem bên dưới. |

### F3 — `log_stream` + `training_summary`

`log_stream.lines` là log huấn luyện dựng lại dạng text tiếng Anh: header (hyperparameter thật từ `reports/hyperparameters.md`) + **mỗi dòng step lấy nguyên từ `logs/training-log.jsonl`** (timestamp/loss/lr/grad_norm/samples-per-s thật) + footer tổng kết. `granularity=epoch` thì mỗi dòng là 1 epoch (từ `reports/epoch-summary.csv`).

```json
{
  "run_id": "qwen-llm-instruction-ft-20260503",
  "granularity": "step",
  "total_steps": 1244,
  "points": [ /* dòng thô để FE vẽ chart */ ],
  "log_stream": {
    "format": "text/plain (English)",
    "source": "logs/training-log.jsonl + reports/hyperparameters.md",
    "lines": ["==== SecAI Fine-tuning Run ====", "…", "2026-05-03T08:32:13.118971Z | INFO | step 1 | epoch 0.0016 | loss 1.473286 | lr 5.4054e-06 | grad_norm 5.1523 | 0.48 samples/s", "…"],
    "line_count": 212
  },
  "training_summary": {
    "first_step_loss": 1.473286, "final_eval_loss": 0.74909, "epochs": 2,
    "gpu": "2x NVIDIA A100 80GB", "duration_hours": 46.0,
    "dataset_records": 49756, "dataset_groups": ["cybersecurity_terms", "…"]
  }
}
```
`training_summary` chỉ gồm **loss · GPU · thời gian · dataset** — không kèm chỉ số quy đổi hay diễn giải tự thêm.

### F7 — làm sạch & gán nhãn

Gom từ 4 file trong hồ sơ gốc, giữ nguyên số: `datasets/manifest.json` (danh mục bước chuẩn hoá + trường nhãn), `finetuning/reports/preprocessing-report.json` (pipeline, seed, dedup, split, rò rỉ), `finetuning/reports/eda-summary.json` (phân bố nhãn, CVE/CWE duy nhất), `datasets/processed/manifest.json` (schema + split từng dataset).

```json
{
  "cleaning": {
    "steps": ["deduplicate", "schema validation", "language normalization", "security label review"],
    "pipeline": ["load 4 pool", "unify -> messages", "…", "leakage check"],
    "seed": 20260707,
    "records_before_dedup": 58225, "dedup_removed": 8469, "records_after_dedup": 49756,
    "splits": {"train": 39804, "validation": 4976, "test": 4976},
    "leakage_pairs_between_splits": 0,
    "language_distribution": {"vi": 25679, "en": 24077},
    "source": "01. Ho_so_huan_luyen/finetuning/reports/preprocessing-report.json"
  },
  "labeling": {
    "fields": ["CVE-ID", "CWE-ID", "severity", "task", "expected answer", "source citation"],
    "schema_per_dataset": {"dataset_bge_m3_embedding": {"schema": ["…"], "train_records": 40000, "validation_records": 5000, "test_records": 5000}},
    "task_distribution": {"instruction": 24890, "…": 0},
    "type_distribution": {"instruction": 47256, "tool_use": 2500},
    "origin_distribution": {"external_real_en": 24077, "…": 0},
    "unique_cve": 524, "unique_cwe": 36, "system_prompt_coverage": "49756/49756",
    "source": "01. Ho_so_huan_luyen/datasets/manifest.json · datasets/processed/manifest.json · finetuning/reports/eda-summary.json"
  }
}
```

---

## Nhóm D — Model & phần cứng (`/ai/model-info`)

**Ràng buộc:** `contract_display_name` phải **khớp đúng chữ** với "Thông số kỹ thuật" trong hợp đồng, kể cả khi khác id kỹ thuật (`base_model.id` giữ id thật để làm link — 2 trường tách biệt).

**Response 200 (rút gọn):**
```json
{
  "components": [
    {
      "role": "llm",
      "contract_display_name": "Qwen3-30B-A3B-Instruct-2507",
      "base_model": { "id": "Qwen/Qwen3-30B-A3B-Instruct-2507", "hf_url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507" },
      "license": "Apache-2.0",
      "finetuned_checkpoint": "qwen3-secai-lora",
      "serving_model": { "id": "DuyTa/Cyber-F1-AWQ", "hf_url": "https://huggingface.co/DuyTa/Cyber-F1-AWQ" },
      "dataset_ref": "dataset_qwen_llm_instruction",
      "covered_tasks": ["translation", "vulnerability_report", "pentest_guidance", "exploit_code_generation", "remediation", "cve_qa"]
    }
    /* + embedding (BGE-M3 → DuyTa/sec-embedding) và reranker (Qwen3-Reranker → DuyTa/sec-rerank) */
  ],
  "hardware": {
    "training": "2 x NVIDIA A100 80GB",
    "serving_min_commit": { "gpu": "1x NVIDIA A100 80GB", "ram_gb": 128, "ssd_tb": 2 },
    "serving_actual": "4 × NVIDIA A100 80GB (vast.ai) — vLLM: Cyber-F1-AWQ + sec-embedding + sec-rerank, nginx LB 4 replica/dịch vụ"
  },
  "retrieval_config": {
    "mode": "hybrid (dense + BM25 + RRF)",
    "dense_model": "DuyTa/sec-embedding",
    "sparse_model": "Qdrant/bm25",
    "qdrant_collection": "cve_kb_hybrid",
    "qdrant_sparse_vector_name": "bm25",
    "editable_via_ui": false,
    "measured": {
      "hit_rate_at_10_id_aware_pct": 98.78,
      "hit_rate_at_10_hybrid_pct": 80.14,
      "hit_rate_at_10_dense_only_pct": 30.2,
      "precision_at_1_pct": 98.23,
      "n_query": 735,
      "source": "03. Kpi_results/Model/quality/run_eval_all.log",
      "kpi_endpoint": "/ai/kpi-bench/tests/R-KPI-EMB96/result"
    }
  }
}
```
- `base_model`/`finetuned_checkpoint`/`serving_model` đọc sống từ `finetuning/checkpoints/manifest.json` (không hardcode id, tránh lệch).
- **License cả 3 model = Apache-2.0** (đã đối chiếu trực tiếp model card trên HuggingFace của DuyTa).
- `retrieval_config.measured` là bằng chứng hybrid search thật: dense-only 30.2% → hybrid 80.14% → id-aware 98.78%, lấy từ log chạy đo trong hồ sơ gốc.
- **Không có `supplementary_models`** (đã bỏ theo yêu cầu).
- `hardware.serving_actual` là chỗ vận hành phải điền tay theo instance đang chạy.

---

## Nhóm K — KPI Bench (`/ai/kpi-bench/*`) — MỚI

> **⚠️ Đã sửa 2026-09-08:** bản đầu đọc nhầm `KPI-VA-GEN-TEST` (bản tự kiểm toán cục bộ, SAI nguồn) — đã sửa lại đọc đúng hồ sơ gốc. Bảng số liệu dưới đây đã cập nhật theo hồ sơ gốc thật.

Đáp ứng đúng mẫu note họp: *tab theo nhóm → nút chạy → thanh tiến độ → % thật → click xem input/output/score*. Nguồn: **`HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/kpi-summary.json`** (mirror cục bộ đúng file trong hồ sơ gốc Drive) — rollup 20 KPI, có sẵn `name/unit/thr/value/pass/group`. API chỉ đọc, không tính lại. Nhóm đặt theo đúng tên hồ sơ gốc dùng (`quality`/`perf`/`models`), không tự đặt A/B/C. Hồ sơ gốc không có khái niệm "enforced" (tham khảo vs bắt buộc) — response không bịa thêm field này.

### K1. `GET /ai/kpi-bench/groups`

```json
{
  "groups": [
    { "id": "quality", "name": "Chất lượng AI", "style": "percent-bar", "test_count": 11 },
    { "id": "perf", "name": "Hiệu năng (Speed Test)", "style": "speed-gauge", "test_count": 5 },
    { "id": "models", "name": "Model (Embedding/Reranker/LLM)", "style": "percent-bar", "test_count": 4 }
  ]
}
```
`style` gợi ý UI: `percent-bar` = thanh tiến độ rồi hiện %; `speed-gauge` = đồng hồ đo kiểu speed-test (rows 73-93 "Chuyển thành cái giống speed test"). `id` lấy đúng theo field `group` mà hồ sơ gốc dùng — không tự đặt A/B/C.

### K2. `GET /ai/kpi-bench/groups/{group_id}/tests`

Danh sách test trong nhóm, đủ để FE dựng 1 tab/nhóm với nút chạy riêng từng test (đúng ý *"Có nút chạy cho riêng loại đó"*).

```json
{
  "group_id": "quality",
  "tests": [
    { "test_id": "R-KPI-EMB96", "name": "Embedding Hit Rate@10", "unit": "%", "threshold": "> 96" },
    { "test_id": "R-KPI-RERANK90", "name": "Reranking Precision@1", "unit": "%", "threshold": "> 90" },
    { "test_id": "R-KPI-FAITH98", "name": "Faithfulness (RAGAS)", "unit": "%", "threshold": "> 98" },
    { "test_id": "R-KPI-RELEV90", "name": "Answer Relevance (LLM-judge)", "unit": "%", "threshold": "> 90" },
    { "test_id": "R-KPI-ENTITY100", "name": "Entity Precision CVE/CWE", "unit": "%", "threshold": "= 100" },
    { "test_id": "R-KPI-HALLU2", "name": "Hallucination Rate", "unit": "%", "threshold": "< 2" },
    { "test_id": "R-LLM-SAST85", "name": "SAST accuracy", "unit": "%", "threshold": "≥ 85" },
    { "test_id": "R-LLM-EXPLOIT85", "name": "Exploit Explanation", "unit": "%", "threshold": "≥ 85" },
    { "test_id": "R-LLM-REMED60", "name": "Auto-remediation", "unit": "%", "threshold": "≥ 60" },
    { "test_id": "R-SEC-PII", "name": "Sanitization PII/Secret F1", "unit": "%", "threshold": "> 90" },
    { "test_id": "R-KPI-RADAR90", "name": "Radar coverage area", "unit": "%", "threshold": "> 90" }
  ]
}
```
(Nhóm `perf`: `R-PERF-TPS`, `R-PERF-TTFT`, `R-PERF-RPS`, `R-PERF-CCU`, `R-CTRL-RESP15`. Nhóm `models`: `R-MODEL-EMB1200`, `R-MODEL-RERANK180`, `R-LLM-TPS28`, `R-CHATFLOW-TPS25`. Đủ 20 KPI đúng như hồ sơ gốc liệt kê — không bớt, không thêm mã ngoài file.)

### K3. `GET /ai/kpi-bench/tests/{test_id}/result`

**Đây là endpoint chính** — 1 lần gọi, trả đủ để FE vừa chạy hiệu ứng "xoay xoay" vừa hiện kết quả thật khi hết hiệu ứng. Không có job/token bất đồng bộ (không cần thiết — dữ liệu đã tính sẵn, "chạy" chỉ là hiệu ứng UI).

```json
{
  "test_id": "R-KPI-FAITH98",
  "name": "Faithfulness (RAGAS)",
  "group_id": "quality",
  "unit": "%",
  "threshold": "> 98",
  "value": 98.48,
  "pass": true,
  "time_gen_ms": 38800,
  "source": {
    "artifact": "Model/quality/raw-logs/faithfulness-summary.json",
    "measured_at": "2026-06-22"
  },
  "samples": [
    { "id": "acc_dd_0003", "true_label": "faithful to KB", "predicted_label": 1.0, "score": 1.0 }
  ],
  "samples_returned": 20
}
```
- **`time_gen_ms`**: thời lượng FE phát log cho KPI này — **backend quyết**. Tính = thời gian đo THẬT của KPI (khoảng cách 2 mốc bước trong `run_eval_all.log`, hoặc tổng `seconds` trong file đo riêng) × hệ số thu nhỏ, kẹp trong `[min, max]`. Chỉnh nhanh/chậm bằng 3 cấu hình `SHOWCASE_TIME_GEN_SCALE_MS_PER_S` / `_MIN_MS` / `_MAX_MS`, không sửa FE. Hiện tại: scale 25, min 8s, max 40s → retrieval 38.8s, faithfulness 19.1s, còn lại 8s.
- **`run_log`**: `{source, lines, line_count}` — log chạy đo THẬT của **riêng KPI đó**: cắt đúng bước tương ứng trong `Model/quality/run_eval_all.log` (bước 3 retrieval, 4 llm_kpi, 5 task_kpi, 7 sanitization, 8 faithfulness, 9 build_reports) hoặc phát nguyên văn file đo riêng (`tp_embed_host.log`, `ccu_rps.json`, `llm35.json`…). FE phát dần các dòng này trong `time_gen_ms`, đóng dấu thời gian bằng đồng hồ thật lúc chạy.
- `samples`: field trong `raw_log` không cố định 1 khung `{input,output,score}` — mỗi loại test có schema riêng thật (SAST: `{id,capability,runs,passes,pass,method}`; Faithfulness: `{id,true_label,predicted_label,score}`), API trả nguyên như file gốc, không ép khuôn. Test nào **chưa đồng bộ được** `raw_log` từ hồ sơ gốc (Drive tạm chặn tải ẩn danh) trả `samples: null` + `samples_note` giải thích rõ — `value`/`pass` vẫn luôn đúng vì lấy từ `kpi-summary.json` (không phụ thuộc `raw_log`).
- Không có field `enforced` — hồ sơ gốc không phân biệt "cam kết cứng" vs "tham khảo", nên API không tự thêm khái niệm này vào.
- `pass` lấy nguyên từ hồ sơ gốc — snapshot 2026-06-22 hiện **cả 20/20 KPI đều `pass: true`**.
- `source.artifact` là đường dẫn **trong hồ sơ gốc** (tính từ `03. Kpi_results/`) — cho phép đối chiếu thủ công bất cứ lúc nào.

### K4. Biến thể cho style `speed-gauge` (nhóm `perf`)

Cùng endpoint K3, response thay `samples` bằng `gauge`:
```json
{
  "test_id": "R-PERF-TPS",
  "name": "TPS (token/s, mỗi luồng)",
  "unit": "tok/s",
  "threshold": "20–35",
  "value": 55.2,
  "pass": true,
  "time_gen_ms": 8000,
  "gauge": { "ramp_points": [5.5, 19.3, 35.9, 46.9, 55.2], "note": "Dãy số ramp chỉ để hiệu ứng kim đồng hồ chạy dần — điểm CUỐI luôn là giá trị đo thật, các điểm trước là nội suy thuần UI." },
  "source": { "artifact": "Perf/perf/summary.md" }
}
```

### Bảng tra cứu đủ 20 KPI (test_id → giá trị, theo `03. Kpi_results/kpi-summary.json` — hồ sơ gốc, đo 2026-06-22)

| test_id | Nhóm | Tên | Ngưỡng | Giá trị đo | Đạt? |
|---|---|---|---|---|---|
| R-KPI-EMB96 | quality | Embedding Hit Rate@10 | >96% | 98.78% | ✓ |
| R-KPI-RERANK90 | quality | Reranking Precision@1 | >90% | 98.23% | ✓ |
| R-KPI-FAITH98 | quality | Faithfulness (RAGAS) | >98% | 98.48% | ✓ |
| R-KPI-RELEV90 | quality | Answer Relevance | >90% | 92.88% | ✓ |
| R-KPI-ENTITY100 | quality | Entity Precision | =100% | 100.0% | ✓ |
| R-KPI-HALLU2 | quality | Hallucination Rate | <2% | 0.8% | ✓ |
| R-LLM-SAST85 | quality | SAST accuracy | ≥85% | 85.0% | ✓ |
| R-LLM-EXPLOIT85 | quality | Exploit Explanation | ≥85% | 100.0% | ✓ |
| R-LLM-REMED60 | quality | Auto-remediation | ≥60% | 100.0% | ✓ |
| R-SEC-PII | quality | Sanitization PII/Secret F1 | >90% | 98.11% | ✓ |
| R-KPI-RADAR90 | quality | Radar coverage area | >90% | 103.9% | ✓ |
| R-PERF-TPS | perf | TPS chatflow | 20–35 | 55.2 tok/s | ✓ |
| R-PERF-TTFT | perf | TTFT p50 | 900–1500ms | 1192 ms | ✓ |
| R-PERF-RPS | perf | RPS ổn định | ~0.6 | 0.701 | ✓ |
| R-PERF-CCU | perf | CCU đồng thời | ~30 | 30 luồng | ✓ |
| R-CTRL-RESP15 | perf | Response p50 | ≤15000ms | 13751 ms | ✓ |
| R-MODEL-EMB1200 | models | Embedding throughput (host) | ≥1200/s | 2662.4/s | ✓ |
| R-MODEL-RERANK180 | models | Reranker throughput (host) | ≥180/s | 4862.4/s | ✓ |
| R-LLM-TPS28 | models | LLM throughput | ≥28 tok/s | 55.2 tok/s | ✓ |
| R-CHATFLOW-TPS25 | models | Full chatflow throughput | ≥25 tok/s | 105.6 tok/s | ✓ |

**Về `R-CHATFLOW-TPS25`:** đợt tự kiểm toán nội bộ (`KPI-VA-GEN-TEST`, KHÔNG phải hồ sơ gốc) ghi chú dòng này có `pass:true` **gán tay trong script build**, không script nào tính lại tái lập được — API/bảng trên vẫn hiển thị đúng như hồ sơ gốc ghi (không tự ý sửa/ẩn), chỉ nêu rủi ro này ở đây để biết, không đưa vào response.

---

## Nhóm L — Live demo (`/ai/sanitize-demo`) — MỚI

Đáp ứng row 15/94: *"vẫn là chạy đoạn code kia"* — demo **chạy thật**, không phải xem log tĩnh. Dùng thẳng module sản xuất `secai_gateway/sanitize/` (đã có, đang chạy trong pipeline chat thật) — không phải bản mock riêng.

### L1. `POST /ai/sanitize-demo`

**Request:**
```json
{ "text": "Email liên hệ: nguyen.van.a@company.vn, số điện thoại 0987xxxxxx, API key sk-abc123..." }
```
**Response 200:**
```json
{
  "original_length": 98,
  "sanitized_text": "Email liên hệ: [REDACTED_EMAIL], số điện thoại [REDACTED_PHONE], API key [REDACTED_SECRET]",
  "entities_found": [
    { "type": "EMAIL", "count": 1 },
    { "type": "PHONE", "count": 1 },
    { "type": "API_KEY", "count": 1 }
  ],
  "engine": "secai_gateway.sanitize (cascade regex + classifier)"
}
```
Không lưu lại text khách paste vào (chỉ xử lý trong request, không ghi log nội dung — tránh chính API demo lại vi phạm nguyên tắc PII nó đang chứng minh).

---

## Nhóm KB — Log đồng bộ dữ liệu (`/ai/kb/sync-log`) — MỚI

Đáp ứng row 70: hiện log crontab đồng bộ KB hằng ngày, **chỉ xem, không cho sửa qua UI** (sửa phải qua file cấu hình khi khởi động hệ thống).

### KB1. `GET /ai/kb/sync-log?days=7`

Nguồn: log thật của `run_sync.sh` (`/home/ict/Downloads/CVE-DB/*.log` — đường dẫn cấu hình qua env `KB_SYNC_LOG_DIR`, ngoài repo).

```json
{
  "schedule": { "cron": "0 3 * * *", "frequency_label": "Hằng ngày, 03:00", "editable_via_ui": false },
  "runs": [
    { "date": "2026-09-08", "status": "ok", "source": "sync_cve.log", "summary": "<dòng cuối log thật>" },
    { "date": "2026-09-07", "status": "ok", "source": "sync_cve.log", "summary": "..." }
  ]
}
```
`days` giới hạn số ngày gần nhất trả về (đúng ý *"họ sẽ check hôm nay, hôm qua, hôm trước... để thống nhất là từ hôm start hệ thống tới giờ đều đặn"*).

**Việc cần làm rõ trước khi implement:** log hiện tại (`kb_backfill.log`, `poc_catalog_sync.log`, `sync_cve.log`…) là log tự do (text người đọc), chưa có định dạng ngày/trạng thái rõ ràng để parse tự động — cần chốt quy ước ghi log tối thiểu (1 dòng `DATE|STATUS|SUMMARY` mỗi lần chạy) trước khi API này đọc được chính xác, hoặc parse bằng regex tạm theo định dạng hiện có.

---

## Nhóm S — Source dossier / Loại 1 (`/ai/source-dossier/*`) — MỚI

Duyệt **nguyên bản** hồ sơ gốc đã tải (`HO-SO-GOC-NGHIEM-THU-AI/`, đã loại ảnh chụp màn hình — xem `00-TONG-QUAN.md` §0.2).

### S1. `GET /ai/source-dossier/tree?path=`

```json
{
  "path": "",
  "entries": [
    { "name": "01. Ho_so_huan_luyen", "type": "dir" },
    { "name": "02. Bo_build_finetuning", "type": "dir" },
    { "name": "03. Kpi_results", "type": "dir" },
    { "name": "04. Huong_dan_cai_dat", "type": "dir" }
  ]
}
```
Gọi lại với `path=03. Kpi_results` để đi sâu — trả `{"name","type":"file","size_bytes","previewable":true|false}` cho file.

### S2. `GET /ai/source-dossier/file?path=...`

- File text nhỏ (`.md/.json/.jsonl/.csv/.yml/.txt`, **≤ 2MB**): trả `{"path","size_bytes","content":"..."}`.
- File **> 2MB** (vd `real_cybersec_instruction.jsonl` 231MB) hoặc không phải text: trả `{"path","size_bytes","previewable":false,"message":"File quá lớn để xem trực tuyến. Xem trực tiếp trên máy chủ tại HO-SO-GOC-NGHIEM-THU-AI/<path>.","head_preview":"<vài dòng đầu nếu là text>"}` — **không** nhúng nguyên nội dung vào response.
- `404` nếu path không tồn tại hoặc trỏ ra ngoài `HO-SO-GOC-NGHIEM-THU-AI/` (chặn path traversal `../`).

---

## Nhóm E — Evidence curated / Loại 2 (`/ai/evidence/*`)

Giữ nguyên E1/E2/E3 như v1 — **chỉ thêm ràng buộc**: mỗi file evidence phải có dòng `Nguồn:` trích đường dẫn cụ thể trong hồ sơ gốc (đọc qua §S để đối chiếu). Chi tiết quy ước cập nhật ở `02-EVIDENCE-FILE-CONVENTION.md`.

---

## Việc còn thiếu để triển khai (vận hành, không phải thiết kế)

- `hardware.serving_actual` (D1) và `KB_SYNC_LOG_DIR` (KB1) cần set tay theo hạ tầng đang chạy — cập nhật mỗi khi đổi instance/đường dẫn.
- `license` từng model (D1) — cần điền tay từ HF model card, chưa có sẵn trong hồ sơ gốc dạng máy đọc được.
- Xác nhận cách chatbot trả lời khi khách hỏi trực tiếp số KPI có cùng nguồn `kpi-registry.json` không (xem `00-TONG-QUAN.md` §2 — rủi ro lệch số).
- Định dạng log KB đồng bộ (KB1) cần chuẩn hoá tối thiểu để parse được tự động.
