# Tài liệu ghép FE — API showcase nghiệm thu AI

Base URL: `http://<gateway>:8131` (demo đang chạy: `http://127.0.0.1:8199`).
Tất cả GET, trả JSON, không cần header xác thực, cùng origin nên không vướng CORS. Riêng `/ai/sanitize-demo` là POST.

Lỗi: HTTP 404/400/422, body `{ "error": "...", "message": "..." }`. FE lấy `message` để hiển thị.

**File kèm sẵn để ghép nhanh:**
- `openapi.json` — spec OpenAPI 3.1 của 18 path showcase. Nhập vào Swagger/Postman hoặc chạy `openapi-generator` sinh client typed.
- `secai-showcase-client.js` — client fetch không phụ thuộc framework, mỗi endpoint 1 hàm. Import là dùng ngay.

```js
import { SecaiShowcase } from "./secai-showcase-client.js";
const api = new SecaiShowcase("http://127.0.0.1:8131");
const { groups } = await api.kpiGroups();
```

---

## 1. Finetuning — `/ai/finetune/*`

| Hàm client | Endpoint | Dùng để |
|---|---|---|
| `datasets()` | `GET /ai/finetune/datasets` | Bảng 3 dataset huấn luyện |
| `datasetSample(id, limit)` | `GET /ai/finetune/datasets/{id}/sample` | Vài dòng dữ liệu mẫu thật |
| `runLog(runId, gran, max)` | `GET /ai/finetune/runs/{runId}/log` | Log huấn luyện + điểm vẽ chart loss |
| `runCharts(runId)` | `GET /ai/finetune/runs/{runId}/charts` | Ảnh chart + link TensorBoard |
| `kpiBaseline()` | `GET /ai/finetune/kpi-baseline` | KPI baseline đã duyệt |
| `datasetAppendix()` | `GET /ai/finetune/dataset-appendix` | Phụ lục kỹ thuật (SHA-256, split) |
| `dataProcessing()` | `GET /ai/finetune/data-processing` | Làm sạch + gán nhãn dữ liệu |

`datasets()` → `{ datasets: [{ id, component, name, model, records, size_mb, sources[], published_range, run:{ run_id, base_model, started_at, ended_at, duration_hours, total_steps, final_train_loss, best_eval_loss } }] }`. Dùng `run.run_id` cho các hàm còn lại.

`runLog(runId)` → `{ points[], log_stream:{ lines[], line_count, source }, training_summary:{ first_step_loss, final_eval_loss, epochs, gpu, duration_hours, dataset_records, dataset_groups[] } }`. Đổ `log_stream.lines` vào `<pre>`; vẽ chart loss từ `points`.

`runCharts(runId)` → `{ charts:[{ name, format, url }], tensorboard_url }`. Ảnh: `<img src={api.assetUrl(filename)}>` hoặc gán thẳng `charts[i].url` (đường dẫn tương đối, cùng origin). `tensorboard_url` mở tab mới (TensorBoard thật cổng 6006).

---

## 2. Model & phần cứng — `/ai/model-info`

`modelInfo()` → `{ components:[{ role, contract_display_name, base_model:{id,hf_url}, license, finetuned_checkpoint, serving_model:{id,hf_url}, dataset_ref, covered_tasks[] }], hardware:{ training, serving_min_commit:{gpu,ram_gb,ssd_tb}, serving_actual }, retrieval_config:{ mode, dense_model, sparse_model, qdrant_collection, measured:{ hit_rate_at_10_id_aware_pct, hit_rate_at_10_dense_only_pct, precision_at_1_pct, n_query } } }`.

- `dataset_ref` trỏ sang `id` ở mục 1 → bấm vào model gọi `datasetSample(dataset_ref)`.
- `retrieval_config.measured` là số đo hybrid vs dense-only để làm bằng chứng.

---

## 3. KPI Bench — `/ai/kpi-bench/*`

Luồng: `kpiGroups()` → 3 nhóm → `kpiTests(groupId)` → danh sách test → bấm "Chạy" → `kpiResult(testId)`.

`kpiGroups()` → `{ groups:[{ id, name, style, test_count }] }`. `style`: `percent-bar` (nhóm quality/models) hoặc `speed-gauge` (nhóm perf).

`kpiTests(groupId)` → `{ group_id, tests:[{ test_id, name, unit, threshold }] }`.

`kpiResult(testId)` → `{ test_id, name, group_id, unit, threshold, value, pass, time_gen_ms, source:{artifact, raw_log?}, run_log:{ source, lines[], line_count }, samples[]?|detail?|gauge? }`.

Cách render 1 lần bấm "Chạy":
1. Hiện thanh tiến độ + phát dần `run_log.lines` trong **`time_gen_ms`** (backend đã tính sẵn thời lượng — FE chỉ việc dùng, không tự đặt).
2. Hết thời gian: hiện `value` + huy hiệu `pass` (xanh/đỏ).
3. Chi tiết: `samples[]` (bảng input/output/score, nhóm percent-bar) hoặc `gauge.ramp_points` (đồng hồ, nhóm perf) hoặc `detail` (object tổng hợp, vd radar).

---

## 4. Sanitize demo — `POST /ai/sanitize-demo`

`sanitize(text)` → `{ original_length, sanitized_text, entities_found:[{type,count}], engine }`. Chạy module che PII/secret thật của gateway. Textarea + nút, hiện `sanitized_text` + bảng `entities_found`.

---

## 5. Evidence theo dòng sheet — `/ai/evidence/*`

Mỗi dòng nghiệm thu = 1 file `dong-<số>.md`. Dòng 1 = nội dung chỉ tiêu (khoá mapping), phần dưới = kết quả thực hiện (trích từ hồ sơ gốc).

| Hàm | Endpoint | Trả về |
|---|---|---|
| `evidenceList()` | `GET /ai/evidence` | `{ items:[{ id, contract_text, format, updated_at }] }` |
| `evidence(id)` | `GET /ai/evidence/{id}` | `{ id, contract_text, format, content }` |
| `evidenceMatch(text)` | `GET /ai/evidence/match?contract_text=` | `{ contract_text, count, items:[{id,format,content}] }` |

- `content` là Markdown → render bằng thư viện markdown (vd marked/markdown-it). Nếu `format="html"` thì bọc trong `<iframe sandbox>`.
- Nhiều dòng có thể cùng 1 chỉ tiêu (ô gộp trong sheet) → `evidenceMatch` trả **tất cả** file khớp, không chỉ 1.

---

## 6. Duyệt hồ sơ gốc — `/ai/source-dossier/*`

Cây thư mục trái + nội dung phải.

`tree(path)` → `{ path, entries:[{ name, path, type:"dir"|"file", kind?, size_bytes?, modified_at? }] }`. `kind`: `text`/`image`/`binary`.

`file(path)` → `{ path, name, kind, size_bytes, content?, truncated?, preview_lines?, image_url? }`.
- `kind="text"`: đổ `content` vào `<pre>` (md có thể render markdown). File >2MB có `truncated=true` + chỉ 200 dòng đầu.
- `kind="image"`: `<img src={api.rawUrl(path)}>`.
- `kind="binary"`: `content=null`, báo không xem được.

Đường dẫn có dấu cách/tiếng Việt — client đã `encodeURIComponent`, nếu tự gọi thì nhớ encode.

---

## Cần thêm gì để ghép nhanh

Đã cung cấp đủ 3 thứ: `openapi.json` (sinh client typed), `secai-showcase-client.js` (dùng ngay), và trang demo `/showcase-demo/` (tham chiếu cách render). FE không cần thêm file nào khác — mọi endpoint đọc trực tiếp, không cần khởi tạo/đăng nhập.

Hai giá trị vận hành backend phải điền trước khi nghiệm thu (không phải việc FE): `model-info.hardware.serving_actual` (theo máy chủ đang chạy) và endpoint gateway thật để FE trỏ base URL.
