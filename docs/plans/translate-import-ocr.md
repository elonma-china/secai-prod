# SecAI Translation Import & OCR — Implementation Plan

> **For agentic workers:** dùng `superpowers:subagent-driven-development` cho điều phối và `superpowers:executing-plans` trong từng worker. Thực hiện theo checkbox, không bỏ cổng kiểm tra. Người dùng đã cho phép triển khai sau rà plan; chỉ bắt đầu code sau Bước 0 và CRITERIA PASS. Lượt rà hiện tại kết thúc bằng báo cáo, không khởi chạy code trong lúc rà.

**Goal:** thêm dịch toàn bộ TXT/DOCX/DOC và dịch ảnh qua OCR tự host ≤4B tham số; giữ nguyên API cũ; bàn giao API cho FE cùng bằng chứng kiểm thử BE. FE implementation ngoài phạm vi theo cập nhật mới nhất.

**Architecture:** API mới `/ai/v2/translate/...`, dùng job có trạng thái bền vững và worker giới hạn tài nguyên. Tách extraction, OCR và translation khỏi router cũ; chỉ tái sử dụng helper thuần và runner hiện có. OCR chạy service riêng trên VPS/instance Vast đang kết nối, gateway gọi qua kênh được bảo vệ; không cài stack GPU vào môi trường gateway hoặc host OCR trên GPU local.

**Tech Stack:** Python ≥3.10, FastAPI/Pydantic, Linch, pytest/pytest-asyncio/httpx; Docker, SQLite hiện có và Redis tùy cấu hình. Backend lưu job được chốt ở Bước 1 theo topology thực tế; API giữ convention legacy, không bổ sung xác thực mới theo quyết định người dùng.

**Spec:** đặc tả multi-agent do người dùng cung cấp trong hội thoại ngày 2026-10-03; các ràng buộc và cổng kiểm tra được ghi lại dưới đây. Đây là PLAN, không phải báo cáo tính năng đã xây dựng.

## 0. Phạm vi đã chốt

Người dùng chốt **chỉ triển khai BE**, thiết kế API theo convention hiện có; người dùng xác nhận gọi API trực tiếp, không cần đăng nhập ở FE hoặc BE. Plan này đã hợp nhất các quyết định đó; lịch sử thay đổi nằm trong LOG, không dùng checklist cũ để thực thi.

- Không code FE, không tạo branch/worktree `feature/fe-devspace-translate`, không chạy acceptance UI trong đợt BE này. Đường dẫn FE đã chốt chỉ dùng đọc tham khảo hợp đồng gọi API.
- Hai coder sol chạy song song: import backend và OCR backend. Slot thứ ba dành warden hoặc reviewer/tester khi đủ điều kiện. Không tạo agent FE chỉ để đạt số lượng ban đầu.
- Không thêm auth middleware, JWT verifier, SSO hoặc endpoint đăng nhập. Không bắt buộc Authorization header mới. Astra A ghi chính xác request/response/error conventions từ API hiện có trước CODE.
- Job không yêu cầu token hoặc `user_id`, không có owner/tenant permission gate. Dùng ID từ 16 byte ngẫu nhiên CSPRNG (32 ký tự hex, 128 bit entropy), không có endpoint liệt kê job. Ai có job ID đều có thể đọc trạng thái/kết quả và hủy job; đây là truy cập theo ID, không xác thực danh tính. Không ghi job ID vào access/application logs và tài liệu bàn giao hướng dẫn FE tránh đưa ID vào URL trang/referrer; API access logs phải redact đoạn ID. Kết quả có TTL và trả Cache-Control: no-store.
- Rate limit theo IP và giới hạn global queue/resource; chỉ tin forwarded IP qua proxy cấu hình tin cậy. Không có quota theo danh tính người dùng.
- Vanguard vẫn kiểm upload, OCR, injection, resource, data và logic. Kiểm JWT/SSO/role/tenant ghi OUT OF SCOPE theo quyết định này; quyền truy cập bằng job ID ghi rõ trong report, không đánh dấu phân quyền người dùng là PASS. Test ID khó đoán, không lộ ID, không có list API và expiry vẫn bắt buộc. Nếu môi trường production không có lớp bảo vệ đáng tin, phải nêu rõ rủi ro tồn dư, không tuyên bố production-secure.
- Tài liệu bàn giao vẫn phục vụ FE, có curl/job lifecycle/limits/errors thực tế. Không ghi 401/403 hoặc token flow nếu BE thực tế không triển khai chúng.
- Vẫn có review độc lập, 30 mẫu ×3 lượt cho mỗi feature BE, vanguard, astra B integration. Không merge main/deploy production tự động. Lượt rà hiện tại chỉ sửa tài liệu; execution đã được người dùng cho phép, không cần hỏi lại cùng phạm vi.

## 1. Ràng buộc toàn cục

- Điều phối chỉ lập kế hoạch, phân công, chuyển nguyên văn feedback, quản lý trạng thái và báo cáo; không code tính năng.
- Import chỉ nhận `.txt`, `.docx`, `.doc`; từ chối mọi định dạng khác, kể cả PDF. Kiểm tra extension, nội dung và cấu trúc; MIME từ client không đáng tin.
- TXT: UTF-8/UTF-16/CP1258 và phát hiện encoding có kiểm soát. DOCX: đoạn văn, bảng, header/footer, footnote. DOC: extraction/conversion cô lập, không macro.
- OCR: tiền xử lý → OCR tự host trên Vast → LLM sửa lỗi có kiểm soát → dịch. Bảo vệ CVE/IP/CIDR/URL/hash/phần mềm/phiên bản/path/command.
- OCR ≥65% theo thước đo cố định; tối thiểu 30 mẫu ×3 lượt cho từng backend feature. Không dùng benchmark công bố của model thay kết quả trên bộ mẫu dự án.
- Không thay endpoint/schema/hành vi API cũ. Không chuyển code cũ sang implementation mới chỉ để tái sử dụng.
- Chạy toàn bộ test hiện có trước/sau; failure hoặc skip chưa giải thích không được coi PASS.
- Chỉ hai feature coder BE; mỗi sol có worktree riêng. Astra A và B là hai instance độc lập; reviewer/tester/vanguard không kế thừa lịch sử coder.
- Không sửa workspace bẩn của người dùng, không `git add .`, không tự khôi phục FE bị xóa, không commit secrets/model weights/dữ liệu thật.
- Không merge vào nhánh chính, không deploy production nếu chưa được người dùng xác nhận.
- Mọi thông số giới hạn/model/đường dẫn lấy từ config/env. Runtime giữ chính sách air-gap, không tự tải model hay gọi OCR SaaS.
- Tối đa 5 vòng FIX/REVIEW mỗi task, 3 lần quay lại từ vanguard; áp dụng các điều kiện dừng ở mục 10.

## 2. Bước 0 — hiện trạng đã đọc và công cụ

### 2.1. Hiện trạng thực tế

| Hạng mục | Bằng chứng / hệ quả |
|---|---|
| Git | Root là monorepo `AIEngines`, không phải riêng gateway. Nhánh hiện tại `work`, HEAD `d00c82e667e29d2d5d1a6b02df7888aa95acb58e`; người dùng đã chốt base `main`. Local `main` và `origin/main` hiện cùng `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; chưa fetch, khóa SHA khi bắt đầu execution. |
| Workspace | Nhiều file untracked; FE tracked `secai/secai-fe-devspace` đang bị xóa, FE hiện có `secai/secai-agent-gateway/secai-fe-devspace` là untracked. Không tự commit việc chuyển thư mục này. |
| Backend | `app.py:create_app` dựng GatewayState, middleware và routes; dịch cũ trong `api/routes.py` là closure, không phải service có thể import trực tiếp. |
| Dịch cũ | Helper ở `api/messages.py`, `literal_mask.py`, `text_chunk.py`; profile `translate` không có tool; chunk chạy qua runner. Phải kiểm tra placeholder, không tin comment bảo đảm tuyệt đối. |
| Auth | Middleware đã đọc có CORS/rate limit; projects dùng `user_id` do client đưa vào. Theo quyết định người dùng, API mới không thêm identity provider và không dùng user_id để phân quyền job. |
| CORS/rate limit | CORS hiện chỉ cho Content-Type/X-API-Key; limit mặc định 15 request/phút/path có thể chặn polling. Cần bổ sung chỉ cho namespace mới, không đổi hành vi route cũ. |
| State | Có SQLite project/session và tùy chọn Redis; chưa xác minh durable job queue đáp ứng nhiệm vụ này. |
| FE | React/TypeScript/Vite; `TranslateScreen.tsx` gọi API dịch text/SSE; package hiện chỉ có dev/build/preview, chưa có test script. |
| Hardware | Snapshot: RTX 4090, 24.564 MiB tổng, 23.984 MiB trống; disk còn ~80 GiB. Đây chỉ là máy điều phối; OCR sẽ chạy trên Vast theo chỉ định người dùng. |
| CLI | Có git, uv, Docker, node/npm, nvidia-smi; không tìm thấy libreoffice/antiword/pip-audit/trivy trong PATH lúc khảo sát. Chưa kiểm tra Docker daemon/GPU runtime hoặc cài thêm gì. |
| Test | pytest, pytest-asyncio, httpx; `tests/conftest.py` có fixture môi trường hermetic. Chưa chạy baseline trong lượt lập plan. |

### 2.2. Inventory công cụ và mục đích

| Nhóm khả dụng trong phiên | Dùng cho |
|---|---|
| Collaboration spawn/followup/message/interrupt | Chạy astra/sol/reviewer/tester/vanguard/warden; brief tự đủ, `fork_turns=none`. |
| exec/apply_patch + git/uv/npm/Docker | Đọc repo, worktree, chạy test/build và ghi artifact; điều phối chỉ sửa tài liệu điều phối. |
| Web search/open | Đọc nguồn chính thức về OCR, version, license code/weights và hosting; ghi URL + ngày + revision. |
| T3 Code `preview_*` MCP | Có sẵn nhưng không cần trong đợt chỉ BE; acceptance dùng HTTP và model thật. Không yêu cầu browser smoke để hoàn thành BE. |
| T3 Code PR MCP | Link từng PR nếu sau này có tạo/làm PR, kiểm tra danh sách trước khi kết thúc; plan này chưa tạo PR. |
| Superpowers skills | writing-plans/brainstorming hiện dùng để lập plan; using-git-worktrees, TDD, systematic-debugging, review, verification áp dụng lúc triển khai đúng giai đoạn. |
| Ponytail | Giữ thay đổi nhỏ, tái sử dụng helper; không bỏ upload validation, giới hạn, test hay durable job đã chốt để giảm code. |
| audit-context-building / differential-review / security-best-practices | Lập bản đồ tin cậy và review upload/job-access/diff ở giai đoạn review/vanguard. |
| async-python-patterns / python-testing-patterns / property-based-testing | Worker async, timeout/cancel, kiểm thử encoding/chunk/token round-trip và race. |
| llm-evaluation | Thiết kế CER/token metrics, manifest, ground truth, các vòng đánh giá. |
| Semgrep/CodeQL/SARIF skills | Vanguard cân nhắc scan phù hợp và ghi kết quả; skill có sẵn không đồng nghĩa CLI/license scan đã sẵn sàng. |
| Playwright skills | Chỉ fallback khi T3 preview thiếu hoặc báo không hỗ trợ; không thay T3 preview đang khả dụng. |
| MCP khác: Google Drive, Pages, document control, Desktop Commander, plugin management, Pets, Hotline | Đã thấy tool trong phiên; không cần cho feature này, không xuất dữ liệu dự án sang connector chỉ vì có sẵn. |
| Imagegen và các skill deployment/Figma/Notion/Linear/PDF… | Có trong catalog nhưng không cần cho phạm vi này. Không dùng ảnh AI làm ground truth OCR; không thêm hỗ trợ PDF. |
| Nodeterm | Có hướng dẫn CLI từ người dùng, nhưng không thấy NODETERM_NODE_ID được đặt. Dùng collaboration của harness; không giả định canvas nodeterm hoạt động. |

Giới hạn hiện tại: 4 slot agent đồng thời tính cả điều phối. CODE dùng điều phối +2 sol BE; slot còn lại dùng warden/reviewer/tester theo điều kiện sẵn sàng. Không hứa có warden thường trực thứ năm. Astra/sol là model được chỉ định; vanguard/warden là vai trò, không giả định tồn tại model tên này. Nếu model được yêu cầu không khả dụng, báo blocker, không thay âm thầm.

### 2.3. Quyết định đã chốt và preflight execution

1. **Đã chốt: base `main`.** SHA local quan sát `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. Khi execution, fetch ref main (không pull/reset workspace), khóa BASE_SHA từ main đã xác minh; mọi nhánh dùng cùng SHA. Nếu local/remote phân kỳ, báo rõ trước khi chọn.
2. **Đã chốt: FE chuẩn là `secai/secai-agent-gateway/secai-fe-devspace` tính từ Git root**, tương ứng thư mục người dùng chỉ định bên trong gateway. FE chỉ được đọc tham khảo; không snapshot/commit/move FE, không tạo worktree FE và không stage các deletion ở đường dẫn cũ.
3. **Đã chốt: benchmark và host OCR trên VPS/instance Vast đang kết nối.** `deploy/README.md` xác nhận mô hình hiện host trên Vast; `deploy/status.sh` có cách resolve Vast/SSH. Khi execution, xác minh instance thực tế, GPU/VRAM/disk/CUDA, workloads và port trống bằng cấu hình an toàn. Không dùng instance ID ví dụ/hardcode endpoint, không dừng LLM/embedding/reranker hiện hữu, không tự thuê instance mới. Thiếu quota/quyền mới hỏi người dùng.
4. **Đã chốt: chỉ BE, API gọi trực tiếp không đăng nhập/token/user_id.** Job truy cập bằng ID ngẫu nhiên; ai có ID đều có thể tra cứu/hủy. Ghi hợp đồng này trong criteria/report/docs theo mục 0.

Đã hỏi gộp; người dùng xác nhận main, Vast và FE bên trong gateway. Phạm vi BE và convention legacy đã chốt; không còn câu hỏi identity provider chặn lập kế hoạch. Lượt rà chỉ cập nhật tài liệu. Trước code còn preflight Git/Vast/baseline và astra A; không còn câu hỏi phạm vi cần hỏi lại.

## 3. Bản đồ ảnh hưởng và ownership dự kiến

Các đường dẫn module Python dưới đây tương đối với `src/secai_gateway/`; deploy/tests/docs/pyproject.toml/uv.lock tương đối với gateway; FE_ROOT đã chốt là `secai-fe-devspace` bên trong gateway (`secai/secai-agent-gateway/secai-fe-devspace` từ Git root). Monorepo worktree phải tạo từ Git root, rồi cd vào `secai/secai-agent-gateway` để test.

| Owner | Thêm mới | File dùng chung có thể sửa tối thiểu |
|---|---|---|
| sol-1 | `api/translate_v2/{router,schemas}.py`, `translation/{settings,types,service,documents,jobs,worker}.py`, init modules, tests tương ứng | `app.py` để gắn router mới; `api/middleware.py` chỉ routing middleware v2 và redaction log v2; cấu hình access log bổ sung có scope v2; `pyproject.toml`, `uv.lock` cho dependency upload/extraction có pin; `.env.example` nếu có, nếu không tạo config mẫu mới |
| sol-2 | `translation/{ocr_client,ocr_preprocess,ocr_correction,technical_tokens}.py`, `prompts/ocr_correction.md` riêng, `deploy/ocr/`, `evals/translation_ocr/`, test OCR | Sở hữu entry profile mới `ocr_correction` trong `prompts/profiles.yaml` (tools rỗng, base false); không đổi entry cũ. Không sửa shared files do sol-1 sở hữu; gửi nhu cầu qua điều phối. OCR runtime/dependency/container nằm riêng |
| astra A | `docs/criteria/{import-file-translate,image-ocr-translate}.md`, `docs/api/translate-v2-contract.md` | Chốt schema và interfaces trước CODE, không code sản phẩm |
| reviewer/tester/vanguard | `docs/reviews/`, `docs/test-results/`, `docs/security-tests/`, test harness riêng | Không sửa feature code; phát hiện test thiếu thì trả feedback, không tự sửa để PASS |
| điều phối/warden | `docs/loop/STATE.md`, `docs/loop/LOG.md`, checkpoint notes | Điều phối là người ghi chính STATE/LOG; warden gửi đề xuất để tránh ghi đè đồng thời |
| sol-docs | `docs/api/translate-import-ocr.md` | Chỉ tài liệu và bằng chứng curl staging |

Không chủ động sửa `api/routes.py`, `api/schemas.py`, helper dịch cũ, profile dịch cũ hay global Settings. Chỉ thêm profile OCR correction vào registry hiện có nếu cần dùng runner; không sửa defaults của profile cũ. Config v2 ưu tiên class mới; sửa file cũ ngoài bảng cần astra xét và ghi lý do. Nếu bắt buộc thay hành vi API cũ, dừng xin quyết định người dùng.

## 4. Thiết kế và hợp đồng API đề xuất để astra A đóng băng

**Decision 2026-10-03:** Astra A đã freeze `docs/api/translate-v2-contract.md` và hai criteria. Contract/criteria là nguồn chuẩn khi chi tiết đề xuất dưới đây khác (schema, key tombstone, 7GiB GPU, correction). Đây là design freeze, không phải implementation PASS. Sol-1 được phép sửa tối thiểu `agent/runner.py` để bỏ date-context riêng cho `ocr_correction`; giữ nguyên hành vi profile cũ. Worker standalone cùng host, session memory và DB riêng; không reuse runner web vì session_id=None vẫn có thể persist. CP1258 dùng strict round-trip + heuristic chữ Việt và warning theo contract, không dùng confidence detector chưa xác minh. Sol-2 cung cấp checkpoint technical_tokens sớm cho sol-1; acceptance import và OCR dùng staging composition hai SHA.

### 4.1. Phương án kiến trúc

- Sync upload cho mọi file: ít code nhưng timeout, không hủy/theo dõi/recover tốt; không chọn cho mục tiêu file lớn/OCR.
- **Đề xuất:** job API thống nhất + một worker có bounded concurrency + service OCR riêng. Với staging một host, job SQLite DB riêng có atomic claim/lease là lựa chọn nhẹ, phù hợp stack hiện tại; không dùng session DB làm queue.
- Gateway+job worker cùng host dùng SQLite; OCR remote trên Vast tự nó không đòi đổi queue. Chỉ nếu job workers thật sự phân tán nhiều host thì astra A xét queue bền vững dùng Redis đã có cùng kho artifact chia sẻ; quyết định trước CODE, không hỗ trợ đồng thời hai backend lưu job trong giai đoạn đầu.

Không dùng `BackgroundTasks`/`create_task` trong web process làm cơ chế bền vững duy nhất. Worker dùng lease/recovery (heartbeat, fencing token chống worker cũ ghi kết quả sau reclaim), kết quả publish nguyên tử; file tạm có TTL/cleanup. Staging chạy namespace/volume/port riêng.

### 4.2. API mới

| Method + URL | Hành vi |
|---|---|
| `POST /ai/v2/translate/files` | Multipart: `file`, `source_lang` optional/auto/en/vi, `target_lang` bắt buộc en/vi; trả 202 job. Chỉ TXT/DOCX/DOC. |
| `POST /ai/v2/translate/images` | Multipart tương tự; giai đoạn đầu PNG/JPEG; trả 202 job OCR→correction→translation. PDF/SVG/GIF/TIFF không hỗ trợ. |
| `GET /ai/v2/translate/jobs/{job_id}` | Trạng thái, stage, số block đã xử lý/tổng, warnings và lỗi an toàn. |
| `GET /ai/v2/translate/jobs/{job_id}/result` | JSON chứa bản dịch toàn bộ và ordered blocks; OCR kèm raw/corrected text và cảnh báo. Truy cập bằng job ID; không có kiểm tra danh tính người dùng. |
| `DELETE /ai/v2/translate/jobs/{job_id}` | Yêu cầu hủy idempotent; không nhầm với thu hồi HTTP request upload ở FE. |

API mới gọi trực tiếp: không Authorization header, không token/user_id và không đăng nhập. Job ID tạo từ 16 byte CSPRNG dưới dạng 32 ký tự hex (128 bit entropy), không tuần tự; ai giữ ID có thể đọc/hủy job. Không có endpoint list/search job. ID không tồn tại trả 404; ID hết hạn trả 410 trong thời gian giữ tombstone, sau đó 404. `Idempotency-Key` tùy chọn do client tạo ngẫu nhiên ít nhất 128 bit, scope theo route+key; cùng key khác payload trả 409. Cùng key+payload trả lại cùng job nên key cũng cần giữ kín như job ID, không log; không coi key là identity. Rate limit theo IP đáng tin và global limits; response chứa job/kết quả dùng Cache-Control: no-store.

```json
{"job_id":"<opaque-job-id>","kind":"file","status":"queued","stage":"queued","progress":{"completed":0,"total":null},"status_url":"/ai/v2/translate/jobs/<opaque-job-id>","result_url":"/ai/v2/translate/jobs/<opaque-job-id>/result"}
```

```json
{"job_id":"<opaque-job-id>","status":"succeeded","source_lang":"en","target_lang":"vi","translated_text":"...","blocks":[{"id":"b0001","kind":"paragraph","order":0,"translated_text":"..."}],"warnings":[]}
```

```json
{"error":{"code":"UNSUPPORTED_FILE_TYPE","message":"Chỉ hỗ trợ .txt, .docx, .doc.","request_id":"uuid"}}
```

| HTTP | Stable code / thời điểm |
|---|---|
| 400 | `EMPTY_FILE`, `INVALID_LANGUAGE`, `INVALID_REQUEST` |
| 404 / 410 | `JOB_NOT_FOUND` / `JOB_EXPIRED` (theo ID/tombstone, không kiểm owner) |
| 409 | `RESULT_NOT_READY`, `IDEMPOTENCY_CONFLICT`, `JOB_ALREADY_FINISHED`, `JOB_FAILED`, `JOB_CANCELLED` |
| 413 | `FILE_TOO_LARGE`, `TEXT_TOO_LONG`, `IMAGE_TOO_LARGE`, `ARCHIVE_LIMIT_EXCEEDED` |
| 415 | `UNSUPPORTED_FILE_TYPE`, `FILE_TYPE_MISMATCH` |
| 422 | `CORRUPT_DOCUMENT`, `UNSUPPORTED_ENCODING`, `UNSAFE_DOCUMENT`, `NO_TEXT_DETECTED` |
| 429 | `RATE_LIMITED`, `QUEUE_FULL`; có Retry-After |
| 503 | `DEPENDENCY_UNAVAILABLE` trước khi nhận job |

Nếu đã trả 202, lỗi extraction/OCR/LLM xuất hiện ở job `failed` với stable code, không giả vờ HTTP của request upload đổi thành 4xx sau đó. Job status GET vẫn 200 khi đọc thành công bản ghi failed. Lỗi LLM rỗng/placeholder sai/timeout có code `EMPTY_TRANSLATION`/`TOKEN_INTEGRITY_FAILED`/`PROCESSING_TIMEOUT`; không trả succeeded với bản dịch một phần.

Trạng thái: `queued → running → succeeded|failed`; `queued|running → cancelling → cancelled`; `stage` là extracting/preprocessing/ocr/correcting/translating. Update state dùng transaction/compare-and-swap: chỉ một terminal outcome thắng. DELETE trên job active trả 202 trạng thái cancelling/cancelled; lặp lại trên cancelling/cancelled trả cùng trạng thái; trên succeeded/failed trả 409 `JOB_ALREADY_FINISHED`, không xóa kết quả. GET result khi active trả 409 `RESULT_NOT_READY`, failed/cancelled trả 409 `JOB_FAILED`/`JOB_CANCELLED`; lỗi stage vẫn nằm trong status. Terminal immutable trừ expiry/cleanup. Progress gồm stage và số block dịch; completed không giảm, total chỉ chốt khi extraction xong, không bịa phần trăm OCR.

### 4.3. Giới hạn khởi điểm đề xuất, phải chốt trước code

| Giới hạn | Giá trị ban đầu |
|---|---|
| File/ảnh | 10 MiB/file, request multipart tối đa 11 MiB; tối đa 8 part và 4 KiB/field text; 1 file/request; max 20 megapixel ảnh, cạnh max 10.000 px |
| Text | 200.000 Unicode code point sau extraction, max 10.000 block, không cắt ngầm |
| DOCX | Max 2.000 ZIP entries, tổng inflate 50 MiB, ratio 100:1; kiểm tra cả trong khi giải nén |
| Chunk | Max 6.000 ký tự, max 300 chunk/job; semantic split trước, bảo vệ literal span khỏi cắt đôi |
| Worker | 2 job đang chạy tổng; max 4 LLM calls tổng; OCR GPU concurrency=1 |
| Queue/rate | 20 queued toàn cục, 2 active/IP; upload 5/phút/IP, status/result 60/phút/IP; cấu hình IP proxy tin cậy |
| Timeout | Conversion 60s, OCR 120s/ảnh, LLM 120s/chunk, deadline job 30 phút; max 1 retry/chunk hoặc OCR request lỗi tạm thời, vẫn trong deadline job; không retry toàn bộ job vô hạn |
| Storage | Input/intermediate xóa khi terminal (tối đa 5 phút nếu crash/reaper); result và idempotency record TTL 24h từ terminal; tombstone thêm 24h chỉ chứa ID/status/expiry; reaper mỗi 5 phút; volume quota 5 GiB |

Các giá trị là design budget, không phải hiệu năng đã đo. Astra A có thể chỉnh trước khi freeze bằng evidence và LOG; sau freeze không nới chỉ để vượt test. V2 có bucket upload và bucket status/result/cancel theo IP+nhóm route (không bucket mới theo từng job ID), dùng store/transaction chia sẻ để giới hạn có hiệu lực cross-process. Middleware cũ nhường đúng prefix `/ai/v2/translate/` cho limiter v2; giữ nguyên xử lý mọi route cũ. CORS cho namespace mới nhận Idempotency-Key, không Authorization/cookie; cấu hình theo sub-app hoặc middleware có scope, không mở rộng CORS của legacy. Test 60 polling/phút trên v2 không bị default 15/phút chặn; route cũ giữ nguyên.

### 4.4. Extraction và bảo toàn dữ liệu

- TXT không có magic bytes cố định: kiểm tra BOM, decode strict, loại dấu hiệu binary/format khác, kết hợp detector và confidence. Không `errors=ignore/replace`. Khi mơ hồ trả lỗi hoặc cảnh báo có policy đã chốt, không tuyên bố luôn phát hiện đúng CP1258.
- DOCX: kiểm tra ZIP + content types + Word parts, XML parser không external entities/DTD. Duyệt paragraph/table xen kẽ theo document order; header/footer gắn section, footnote gắn reference. Định nghĩa thứ tự logic: header section → body với footnote ngay sau block chứa tham chiếu → footer section; lặp header/footer có chủ ý theo section, không tự nhân theo trang.
- Output là text/ordered blocks, không cam kết dựng lại pagination, hình ảnh, font hoặc file Word giữ layout. Bảng giữ hàng/cột qua block metadata; mapping nguồn→đích truy vết được. Không silently bỏ part chưa hỗ trợ: warning hoặc lỗi theo criteria.
- DOC: thử antiword trên mẫu có bảng/header/footer/footnote; nếu không đáp ứng đủ cấu trúc thì dùng LibreOffice headless → DOCX trong sandbox không mạng, user profile tạm, macro disabled, timeout/memory/process limit. Không chọn công cụ chỉ vì nhỏ nếu làm mất nội dung. Reject macro/OLE/active external content theo policy trước converter; fixture chứng minh không có outbound fetch.
- Lưu file bằng random internal ID; tên upload chỉ là metadata đã giới hạn/escape. Không nối filename vào path/shell; không `shell=True`.
- Translation service mới dùng `CompletionRunner.complete("translate", prompt, None)` cùng helper thuần đã kiểm tra, không loopback gọi API cũ. Kiểm tra token theo occurrence trước/sau (không chỉ set), chống placeholder có sẵn trong input và masking lồng nhau. Mã dài vượt budget giữ nguyên thành block literal, chỉ dịch prose xung quanh; không cắt token và không gửi chunk quá model budget. Retry giới hạn rồi fail nếu integrity sai, không append dữ liệu bị mất vào sai chỗ.

### 4.5. OCR và correction

- EXIF orientation → deskew/rotation có kiểm tra → denoise nhẹ/contrast; giữ original, tránh biến ảnh sạch thành xấu. Giới hạn pixels trước decode lớn; strip metadata và không thực thi nội dung.
- OCR trả blocks/reading order và confidence nếu model thật có cung cấp. Không bịa confidence nếu không có.
- LLM correction tool-less, nội dung OCR là untrusted data. LLM chỉ đề xuất edit có vị trí/bằng chứng, không thêm đoạn mới. Giữ nguyên token kỹ thuật hợp lệ đã nhận diện; IPv4/CIDR xác minh thêm bằng `ipaddress`, regex chỉ kiểm tra hình thức, không chứng minh token đúng với ảnh.
- Token mơ hồ không tự đổi CVE/IP/hash chỉ dựa vào câu văn; giữ raw, đánh dấu `needs_review`. Sửa token chỉ khi có bằng chứng độc lập (crop/OCR thứ hai hoặc người dùng); không coi việc LLM tự khẳng định là bằng chứng.
- So sánh raw/corrected và record edits, reject edit ngoài vùng cho phép; dịch dùng text đã kiểm tra. Raw OCR/corrected text truy cập bằng job ID; không có identity verification và không ghi nội dung/ID vào log.

## 5. Bước 1 — astra A đóng băng tiêu chí (chưa chạy)

Instance mới model `gpt-6-astra`, context rỗng; nhận spec, plan, BASE_SHA, map và quyết định API anonymous/Vast/BE-only đã chốt. Chỉ viết criteria/contract, không feature code.

- [ ] Tạo hai file criteria theo mục 3, mỗi tiêu chí có ID, phép đo, command/evidence cần nộp, PASS/FAIL; lập bảng requirement→criterion→test.
- [ ] Chốt `docs/api/translate-v2-contract.md`: routes/schema/errors/anonymous-access/limits/states/idempotency/cancel/retention, output document order và typed Python interfaces; JSON examples dùng cho FE, không code TypeScript.
- [ ] Hợp đồng nội bộ tối thiểu: `DocumentBlock(id, kind, order, text, metadata)`, `ExtractedDocument(blocks, warnings)`, `OcrResult(blocks, raw_text, warnings)`; async `translate_blocks(blocks, src, tgt)`, `recognize_image(image)`, `correct_ocr(result)`; kiểu exception/stable codes được ghi chính xác trong contract.
- [ ] Đóng băng đầu ra OCR và job processor interface để sol-2 dùng fake job adapter trong lúc sol-1 viết queue; sol-1 sở hữu toàn bộ router/jobs và chỉ nối OCR qua interface này.
- [ ] Chốt performance criteria trên phần cứng được cấp: đề xuất p95 nhận job ≤2s sau upload (không tính mạng upload), status ≤300ms ở 10 client; OCR warm p95 ≤30s/ảnh ≤2MP; peak OCR VRAM không vượt quota Vast đã đo và dành riêng (trần đã freeze 7168MiB, dự phòng free 2048MiB theo công thức mục 10), tổng process GPU không OOM hoặc làm suy giảm SLA dịch vụ hiện hữu; gateway+worker RSS ≤2GiB không tính model/converter. Nếu baseline cho thấy không thực tế, đề xuất điều chỉnh có lý do trước code.
- [ ] Chốt đường chạy correction qua profile mới: `runner.complete("ocr_correction", prompt, None)` với JSON edit-list contract; không tools, không reuse profile translate, không sửa behavior profile cũ. Test khả năng runner parse/timeout/sanitize thực tế.
- [ ] Chốt test baseline và staging safety: DB/volume/port riêng, provider LLM test, không dữ liệu thật, không dùng scan toàn monorepo làm mục tiêu tấn công.
- [ ] Điều phối đọc đầy đủ artifacts, xác nhận không tiêu chí bị bỏ sót, ghi hash contract/criteria, chuyển CRITERIA→CODE chỉ khi mọi blocker đã giải quyết.

## 6. Bước 2 — hai sol BE song song trong worktree riêng

### 6.1. Git và bootstrap

Tất cả nhánh tách từ cùng BASE_SHA đã chốt; không dựa vào branch agent khác chưa review.

| Agent | Model | Branch | Trách nhiệm |
|---|---|---|---|
| sol-1 | gpt-6-sol | `feature/import-file-translate` | API/job anonymous mới, service dịch mới, import/extraction và test |
| sol-2 | gpt-6-sol | `feature/image-ocr-translate` | OCR service/hosting/benchmark/correction và test |
| FE (ngoài phạm vi) | — | Không tạo branch | Chỉ đọc tham khảo API; không triển khai trong đợt BE |
| astra B (sau gate) | gpt-6-astra | `integration/translate-features` | Merge có kiểm soát, review tích hợp, regression |

- [ ] Điều phối tạo worktree từ Git root bằng `git worktree add -b <branch> <workspace>/<name> <BASE_SHA>` sau khi kiểm tra branch/path chưa tồn tại. Không dùng absolute path máy cá nhân trong code/config commit.
- [ ] Đối chiếu diff giữa work đã khảo sát và main đã khóa để cập nhật map trước CODE; cấp bản criteria/contract cùng hash cho mỗi agent; copy tài liệu không làm thay đổi BASE_SHA. Không copy/stage FE untracked hoặc các deletion của workspace gốc.
- [ ] Trong mỗi worktree, dùng dependency hiện có/lock đã xác minh; chạy baseline `python -m pytest -q` trong virtualenv cô lập, lưu JUnit/stdout, version và exit code. Không cập nhật lock để che baseline fail.
- [ ] Nếu baseline fail, phân biệt môi trường với lỗi có sẵn và báo qua điều phối; chưa được khẳng định no-regression hoặc toàn bộ PASS. Không sửa unrelated baseline ngoài scope để làm xanh.

### 6.1a. Interface dùng chung và staging trước integration

- Astra A đóng băng chữ ký/type/errors trong contract; sol-1 sở hữu `translation/types.py`, schema/router/job/store/settings/service. Sol-2 sở hữu OCR modules + entry profile mới. Không để hai agent tự tạo hai bộ DocumentBlock khác nhau.
- Sol-2 có thể viết test với contract double tạm trong test harness riêng khi sol-1 chưa có checkpoint; không commit bản copy types/service vào feature branch. Khi checkpoint shared interfaces của sol-1 sẵn, reviewer/tester ghép hai SHA trong worktree staging tạm để chạy end-to-end.
- Staging composition thực hiện bằng merge/cherry-pick cục bộ trong test worktree riêng, ghi BASE_SHA + hai feature SHA + composition SHA; không push/main merge và không gọi đây là integration đã PASS. Người tạo staging không chỉnh code ngoài thao tác ghép; conflict logic trả owner qua điều phối.
- Reviewer của từng branch vẫn kiểm diff feature so với BASE_SHA; HTTP/OCR acceptance cần staging composition, không có yêu cầu bất khả thi rằng nhánh OCR độc lập phải chứa queue/router chưa được giao cho nó. Mỗi lần một SHA đổi, bằng chứng cross-feature bị đánh dấu stale và chạy lại affected+regression.
- Job worker load OCR integration qua interface/lazy import và feature flag, để branch import chạy được khi OCR module chưa có. Trạng thái thiếu OCR chỉ làm image job unavailable, không làm import/legacy khởi động thất bại.

### 6.2. sol-1 — import và nền job

Files theo ownership mục 3. Các test mới dự kiến `tests/test_translate_v2_{contracts,job_access,jobs,documents,service}.py`.

- [ ] Viết test đỏ cho extension/signature mismatch (DOC phải có OLE/CFB magic và WordDocument stream, không chỉ là OLE bất kỳ), PDF, TXT encoding/không mất dấu, DOCX đầy đủ parts/order, DOC legacy, empty/corrupt/oversize và active content.
- [ ] Viết test job state/atomic claim/opaque ID access/no-list/no-store/cancel/deadline/restart recovery/TTL/idempotency và giới hạn queue cross-process.
- [ ] Implement extraction + translation theo interfaces đã freeze; bảo vệ literal trước chunking, ghép đúng thứ tự khi completion đảo thứ tự; partial failure không succeeded.
- [ ] Implement router/store/worker mới, nối app tối thiểu; health/readiness v2 fail rõ khi thiếu dependency của feature đang gọi (converter hoặc OCR), không làm API cũ mất khả dụng khi feature tắt.
- [ ] Test upload streaming vượt limit giữa chừng/Content-Length giả/multipart nhiều part, temp cleanup cả khi disconnect và timeout.
- [ ] Chạy unit mới + suite cũ; commit checkpoint CODE bằng explicit file list; gửi hash/diff/test/risk cho điều phối.

### 6.3. sol-2 — OCR

Files theo ownership mục 3; tests `tests/test_translate_v2_{ocr_preprocess,ocr_tokens,ocr_correction,ocr_client}.py`; model serving trong `deploy/ocr/` có Dockerfile/config pinned.

- [ ] Nghiên cứu model theo mục 7, khóa license/revision/parameter count; benchmark shortlist trước khi chọn model production.
- [ ] Tạo failing tests cho rotation/no-text/decode bomb/timeouts, CVE/IP round-trip, injection trong raw OCR, LLM thêm nội dung/sửa token sai.
- [ ] Implement preprocessor, client gọi service Vast và correction riêng; LLM correction dùng instruction độc lập, tools rỗng, không reuse profile translate để giả làm correction.
- [ ] Cung cấp runnable service riêng trên Vast + readiness + cấu hình offline model cache; port/service/volume/GPU quota riêng, kết nối tunnel/private network hoặc TLS+service auth, không public OCR không xác thực; checksum/revision/image digest, không image `latest` cho deliverable.
- [ ] Kiểm tra service trong sandbox/GPU queue trên Vast; đo ảnh hưởng lên LLM/embedding/reranker trước/sau, kiểm tunnel reconnect/network timeout/service-auth failure. Không restart container/LLM sẵn có. Bộ thư viện OCR không đi vào gateway lock.
- [ ] Test đơn vị + test service với model thật + benchmark; commit CODE và ghi model selection evidence. Fake OCR không thay live acceptance.

### 6.4. Bàn giao FE, không triển khai FE

Không tạo task/branch/worktree/code/build/test FE. Sol tài liệu cung cấp multipart examples, job polling/cancel, errors, raw/corrected OCR và warnings để phía FE tích hợp sau. FE hiện tại chỉ đọc tham khảo convention.

## 7. Nghiên cứu OCR và benchmark model

Khảo sát web sơ bộ ngày 2026-10-03 đã thấy các nguồn dưới đây. Không coi đây là model selection hoàn tất; không tuyên bố phiên bản nào là mới nhất toàn bộ họ model trước khi agent kiểm tra release và weights thực tế.

| Ứng viên/nguồn chính thức | Trạng thái trong plan |
|---|---|
| [PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) | Model card nêu 0.9B, Apache-2.0; ứng viên ưu tiên benchmark, chưa đo tiếng Việt/VRAM tại đây. |
| [dots.ocr](https://huggingface.co/rednote-hilab/dots.ocr) | URL hiện redirect sang dots-studio; phải ghi publisher/revision thực tế, tổng tham số gồm vision encoder và license weights/code, không suy kích thước từ riêng LLM. |
| [DeepSeek-OCR-2](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2) | Đã tìm thấy card chính thức; sol-2 xác minh tổng tham số/license và bản mới nhất đủ điều kiện trước tải. |
| [Qwen2.5-VL-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct) | Ứng viên dòng nhỏ; kiểm tra tổng tham số checkpoint và license cụ thể, không suy từ license của bản lớn khác. |
| [Surya](https://github.com/datalab-to/surya), [Marker](https://github.com/datalab-to/marker), [Chandra](https://github.com/datalab-to/chandra) | Tách toolkit/pipeline khỏi model. README Surya/Marker nêu điều kiện riêng cho weights; không coi code license là giấy phép weights hoặc tự cho rằng dùng thương mại miễn phí. |

- [ ] Lập bảng revision, params tổng, code license, weights license, quyền sử dụng SecAI, runtime/CUDA, remote-code audit, disk estimate; model >4B hoặc license không đáp ứng bị loại trước benchmark, ghi lý do.
- [ ] So sánh ít nhất 3 checkpoint/model đủ điều kiện nếu tài nguyên cho phép, một model trên GPU mỗi lần. Nếu không thể benchmark đủ do license/quota, ghi BLOCKED cho phần đó thay vì đếm ba prompt cùng model thành ba model; có thể tiếp phần độc lập. Ba phương án khắc phục accuracy khi fail có thể là đổi model/preprocessing/prompt, ghi riêng với số model đã benchmark. Ưu tiên Paddle, dots, DeepSeek hoặc Qwen tùy kết quả xác minh; Datalab chỉ vào shortlist nếu vượt license/size gate.
- [ ] Tải weights ở bước chuẩn bị có mạng được phép, kiểm checksum/revision rồi inference offline; không để model tự fetch code/trọng số runtime.
- [ ] Bộ calibration tách khỏi acceptance; không tuning trên ground truth acceptance. Giữ manifest/checksum và ground truth Unicode do người đánh giá kiểm tra, không để LLM tự chấm chính nó.
- [ ] Mỗi ứng viên: cùng ảnh/crop/pixel cap/GPU Vast đã ghi model và quota, warmup riêng, 3 lượt có seed shuffle; đo OCR-only và full pipeline riêng, model load/cold start riêng.
- [ ] Báo cáo p50/p95, throughput, peak VRAM/RSS, lỗi/OOM, CER trước/sau correction, token metrics, cấu hình/quantization. Chọn accuracy vượt gate trước, sau đó tốc độ/VRAM/giấy phép; không chỉ chọn điểm vendor cao nhất.

### Thước đo cố định

`CER = (S + D + I) / N` theo Levenshtein ở Unicode code point sau NFC và chuẩn hóa CRLF→LF; giữ dấu, hoa/thường, dấu câu. Giữ nguyên khoảng trắng và xuống dòng sau chuẩn hóa CRLF→LF, không trim/collapse whitespace; dùng thứ tự đọc của manifest cho text ghép, không đổi policy giữa model/lượt.

`Accuracy = 100 × max(0, 1 - CER)`; báo cả micro-CER toàn corpus và macro accuracy trung bình ảnh. Gate đề xuất: micro accuracy và macro accuracy **đều ≥65% từng lượt**, raw OCR lẫn corrected OCR; báo riêng tập tiếng Việt/Anh/screenshot để tránh trung bình che điểm kém. Các subset này cũng phải ≥65% khi đủ mẫu đã định trước. Corrected OCR không được kém raw ở cấp corpus hoặc giảm token accuracy.

CVE/IP/CIDR: exact-match theo occurrence có alignment với ground truth, báo precision/recall/F1 riêng từng loại, không chỉ tỷ lệ token có regex hợp lệ. Gate đề xuất CVE/IP F1 ≥95%, token kỹ thuật hợp lệ đầu vào correction được bảo toàn 100%; astra A xác nhận trước CODE. URL/hash/version/path/command báo riêng và test bảo toàn 100% sau correction/translation đối với literal đã xác định.

Ảnh blank: không tính CER vì N=0, dùng metric hallucination/no-text; blank phải không sinh chữ và API báo NO_TEXT_DETECTED. Crash/timeout của ảnh có chữ tính output rỗng trong CER và một functional failure, không bỏ khỏi thống kê. Chất lượng dịch đánh giá thêm đủ nội dung/đúng ngôn ngữ/thuật ngữ/thứ tự/token; CER của OCR không đo chất lượng dịch.

Artifacts: `docs/benchmarks/ocr-model-selection.md`, `evals/translation_ocr/manifest.json`, result JSONL/CSV cho mỗi model/lượt, logs đã bỏ dữ liệu nhạy cảm. Không benchmark/tải weights ở lượt lập plan này.

## 8. Bước 3 và 4 — review độc lập, test nhiều lượt

### 8.1. Review

Mỗi branch có reviewer sol mới, `fork_turns=none`, worktree riêng checkout commit cần review. Nhận criteria/contract/diff/baseline/evidence, không nhận hội thoại coder.

- [ ] Đọc diff và map; đánh dấu từng criterion PASS/FAIL/BLOCKED với evidence.
- [ ] Tự chạy tests mới+cũ, kiểm API legacy schema/SSE/error/limit behavior, upload/job-ID-access/resource/security boundaries.
- [ ] Không sửa feature code. Một tiêu chí FAIL hoặc thiếu evidence là chưa PASS.
- [ ] Feedback bắt buộc `ID | tiêu chí hoặc test case | bằng chứng | mức độ | gợi ý sửa`; gửi điều phối, điều phối chuyển nguyên văn cho coder.
- [ ] Sau fix, reviewer độc lập đọc commit mới và chạy lại affected+regression; lưu `docs/reviews/<task>-round-N.md`. Commit review report riêng; nếu không đổi code, checkpoint ghi lại cùng code hash, không tạo commit rỗng giả tiến độ.

### 8.2. Acceptance: ít nhất 30 mẫu ×3 lượt mỗi feature

Tester sol riêng, test worktree riêng; chỉ bắt đầu sau branch review PASS. Dùng dữ liệu tổng hợp công khai/được phép, không production.

| Bộ | Phân bổ tối thiểu 30 mẫu |
|---|---|
| Import | 8 TXT: UTF-8/BOM/UTF-16 LE/BE/CP1258/mixed newline/technical/long; 8 DOCX: mixed paragraphs/tables/header/footer/footnote/sections/Unicode/long; 6 DOC: VI/EN/tables/header-footer/footnote/long; 8 invalid: empty/corrupt/mismatch/PDF/oversize/binary-as-txt/unsafe archive/unsupported format |
| OCR | 30 ảnh có chữ: 8 VI in rõ, 4 EN in rõ, 8 screenshot CVE/IP/URL/code, 6 điện thoại nghiêng/phối cảnh, 4 blur/low-contrast/rotation; bảo đảm giao nhau giữa tiếng Việt có dấu và token kỹ thuật |
| OCR negative bổ sung | Blank, corrupt, pixel bomb, giả extension, PDF/SVG, prompt injection, metadata bất thường; không dùng negative để làm đẹp CER |

- [ ] Ba lượt dùng seed shuffle khác nhau hoặc mẫu mới cùng strata; ghi rõ không phải 90 mẫu độc lập nếu lặp cùng 30 mẫu. Disable result cache khi đo hoặc báo cache riêng.
- [ ] Mỗi lượt nộp sample IDs, expected/actual, pass rate, accuracy raw/corrected và CVE/IP metrics, time/VRAM, lỗi và so sánh lượt.
- [ ] Functional/error-contract phải 100% đúng kỳ vọng; OCR gate theo mục 7 từng lượt; test HTTP thêm upload thật, polling, reconnect/cancel và regression API dịch text cũ; không yêu cầu UI.
- [ ] FAIL → FIX → REVIEW → TEST lại; không sửa expected/ground truth chỉ để xanh. File `docs/test-results/<task>-round-N.md` kèm machine-readable result.

## 9. Bước 4.5, 5 và 6 — vanguard, tích hợp, bàn giao

### 9.1. Vanguard

Instance mới hoàn toàn, model sol làm vai trò vanguard, context rỗng; không kế thừa coder/reviewer và không sửa feature code. Nhận spec/criteria/commit hash/environment/evidence paths; tự dựng kịch bản trước khi chạy.

- [ ] Tạo `docs/security-tests/<feature>-test-plan.md`, ID từng test, mục tiêu, bước, expected, severity.
- [ ] Upload: spoof/polyglot/double extension, macro/OLE/external link/XXE, zip bomb/nesting; path traversal/null/Unicode/name dài; empty/oversize/concurrent/disconnect.
- [ ] Image: oversized dimensions/decode bomb, malicious metadata, format lạ/corrupt, OCR injection; kiểm cả original và preprocessing outputs.
- [ ] LLM: injection ở correction và translation, system prompt leakage, unauthorized tools, CVE/IP mutation có chủ đích; không có tool calls là assertion, không chỉ prompt yêu cầu.
- [ ] Anonymous access: không yêu cầu token/user_id; ID CSPRNG ≥128 bit, không list/enumeration qua ID tuần tự, no-store, log redaction, expiry, unknown-ID behavior và mass assignment. JWT/role/tenant/owner tests ghi OUT OF SCOPE do quyết định người dùng, không đánh dấu PASS. Ai có ID đọc/hủy được là hành vi đã chốt, không dựng auth mới để thay đổi nó.
- [ ] OWASP: command/SQL/template injection, SSRF/external relationships, JSON Content-Type đúng/no HTML execution qua API, CSRF không áp dụng cho cookie auth vì không dùng cookie, v2 CORS/headers, safe errors. Không báo FE XSS đã được kiểm vì FE ngoài scope.
- [ ] Resource: throttling upload lẫn polling, flood queue, long job, timeout, RSS/VRAM OOM, no unbounded retries.
- [ ] Data: cleanup/TTL/file permissions, logs không text/token/credential, retention, internal TLS/storage theo chuẩn deploy đã chốt, không outbound SaaS.
- [ ] Logic: duplicate/idempotency, race/claim, worker restart, cancel/partial chunk failure, ordering/format loss, expiry trong khi đọc.
- [ ] Dependency: pip-audit cho BE/OCR runtime và container audit nếu có; npm audit ngoài scope vì không đổi FE, model license/source/checksum và secret scan BE/OCR vẫn bắt buộc; thiếu scanner cần provision hoặc BLOCKED, không coi chưa scan là PASS.
- [ ] Regression: toàn suite legacy và contract snapshot trước/sau, chỉ nhắm staging cô lập.
- [ ] Báo `docs/security-tests/<feature>-report.md`: ID/severity/repro/impact/fix/evidence. Gate: zero Critical/High; Medium phải sửa hoặc có chấp nhận rủi ro bằng văn bản của người dùng; Low ghi nhận.
- [ ] Sau fix: reviewer→functional tests→vanguard chạy toàn bộ nhóm liên quan và regression. Tối đa 3 lần quay lại; không chỉ chạy riêng case cũ fail.

### 9.2. Astra B tích hợp

Instance astra mới, context rỗng, worktree integration riêng; chỉ merge sau các gate review/test/vanguard của branches đã PASS. Với interface cross-branch, có thể dùng workspace staging tạm phục vụ test trước đó, nhưng không coi staging tạm là nhánh integration đã duyệt.

- [ ] Kiểm report áp dụng đúng SHA cuối của mỗi branch; không dùng PASS của SHA cũ cho code mới.
- [ ] Tạo `integration/translate-features` từ BASE_SHA; merge lần lượt sol-1 → sol-2; không có branch FE trong phạm vi mới, checkpoint mỗi merge.
- [ ] Conflict cơ học xử lý tối thiểu; conflict đổi logic/API trả điều phối→owner sửa→review. Không tự bỏ code/test để merge sạch.
- [ ] Run toàn bộ old+new BE tests, full functional corpus khi integration làm đổi đường chạy; smoke API qua HTTP với OCR/LLM thật trên staging, không yêu cầu FE build/UI smoke.
- [ ] Job-ID access/correction/shared wiring thay đổi sau vanguard bắt buộc vanguard kiểm lại affected surface+regression; không dùng report branch thay report integrated behavior.
- [ ] Lưu `docs/reviews/integration-translate-features.md`, SHA và residual risks. Không merge main.

### 9.3. Sol tài liệu

Chỉ sau integration PASS: sol mới trong worktree riêng tạo `docs/api/translate-import-ocr.md` từ implementation thực tế.

- [ ] Mỗi endpoint: method/path/anonymous-access/header/multipart/schema/limits, response thật, error table và thời điểm lỗi sync/async.
- [ ] Curl file/image, poll/status/result/cancel/idempotency; tạo fixtures không nhạy cảm, chạy ví dụ trên staging thật rồi ghi output đã bỏ secrets.
- [ ] FE notes: progress/backoff/retry/expiry/cancel, OCR uncertainty, cách tích hợp và 400/404/409/410/413/415/422/429/503 thực tế; không ghi flow đăng nhập/401/403 không tồn tại.
- [ ] Security/job-ID-access/rate/retention và model config; không đưa credential chạy thật vào ví dụ.
- [ ] Review docs khớp contract/OpenAPI, commit docs; astra B đưa docs vào integration, chạy check liên quan nếu chỉ docs thay đổi.
- [ ] Điều phối báo cáo cuối đầy đủ trước khi yêu cầu xác nhận merge main. Không tự deploy/merge main khi user chỉ đồng ý plan.

## 10. Máy trạng thái, warden và recovery

```text
PLAN → PREPARED → CRITERIA → CODE → REVIEW → TEST → VANGUARD
                                  ↑ FAIL    |       |
                                  └── FIX ←─┘←──────┘
VANGUARD PASS → INTEGRATION → DOCUMENTATION → READY_FOR_USER_MERGE
                                              (không tự MERGE_MAIN)
```

- GPU OCR dùng Vast; local chỉ điều phối và test CPU. Client không được chọn OCR service URL; URL/credential nằm trong cấu hình server.
- State theo từng task; review/test task A không chờ B nếu còn slot và dependencies đủ. GPU queue một slot; agent có thể làm việc CPU trong lúc chờ.
- Warden là instance độc lập được gọi sau mỗi transition/feedback batch hoặc khi hơn 15 phút không có tiến triển có bằng chứng. Hai coder BE để lại một slot cho warden; nếu slot đang review/test, điều phối ghi heartbeat và xếp lịch warden ở checkpoint kế tiếp.
- Warden theo dõi số lỗi unresolved theo severity/ID, thời gian, counters, checkpoint SHA và resource; không phán đoán tiến bộ chỉ từ số dòng code.
- Cùng lỗi lặp 2 lần sau tuyên bố fixed → đổi cách tiếp cận hoặc coder khác, ghi root-cause hypothesis trước lần tiếp. Không giảm lỗi sau 2 vòng → dừng task, đưa phương án và tiếp task độc lập nếu chưa chạm global stop.
- Global stop: quá 5 fix/review/task hoặc quá 3 vanguard return; OCR dưới 65% sau ít nhất 3 phương án model/preprocess/prompt; Critical không sửa được; cần đổi API cũ; thiếu VRAM/disk/quota/credential; hoặc hoàn thành. Dừng khởi chạy việc mới, checkpoint những agent đang chạy ở trạng thái an toàn, báo người dùng; không hạ gate.
- Bảo vệ dung lượng: kiểm trước mỗi model download/build; đề xuất giữ ≥20GiB trống, không xóa file/cache của người dùng để tiếp tục. VRAM dùng trần OCR-owned 7168MiB và dự phòng free 2048MiB: trước load khi owned=0 yêu cầu free>=9216MiB; trước inference khi model đã nạp yêu cầu `free_MiB >= 2048 + max(0, 7168 - owned_ocr_current_MiB)` từ số đo process OCR. Lấy mẫu suốt lượt chạy để giữ OCR peak<=7168MiB và free>=2048MiB; serialize load/inference. Không tính hai lần bộ nhớ model đã nạp, không nới budget/reserve.
- Mọi feedback chuyển nguyên văn `ID | tiêu chí hoặc test case | bằng chứng | mức độ | gợi ý sửa`, lưu file bất biến theo round/SHA.
- Điều phối ghi STATE/LOG sau mọi transition, 5–10 dòng summary mỗi giai đoạn lớn. STATE chứa agent ID/model/role/worktree/branch/SHA, vòng fix/review/test/vanguard, evidence paths, blockers, next action, GPU lease.
- Resume: đọc STATE+LOG → kiểm Git/worktree/agent/process còn sống → đối chiếu SHA/evidence → tiếp task chưa hoàn tất. Không tự tạo lại agent đang chạy hoặc rerun GPU job chưa xác định trạng thái.
- Checkpoint code/review/test bằng commit hoặc hash đã có. Không force reset/delete worktree chưa bàn giao; không force-push. Một branch mới sau fix phải có revalidation mới.
- Agent brief luôn ghi ownership, không một mình trong repo, không revert người khác; mọi giao tiếp qua điều phối, không tự sửa module của agent khác.

## 11. Review focus và bằng chứng hoàn thành

1. TXT không có signature duy nhất/CP1258 mơ hồ: test detector và fail rõ, không mất ký tự.
2. Word body/table/header/footer/footnote không cùng một luồng pagination: golden extraction/order tests và công bố logical order.
3. Chunk cắt đôi literal hoặc LLM bỏ/đổi placeholder: property tests + live corruption cases, không âm thầm thành công.
4. API anonymous: ai có job ID đều đọc/hủy được; kiểm entropy, không list API, không log ID, no-store và TTL. Không tuyên bố có phân quyền owner/tenant hoặc BOLA PASS.
5. Pass của một branch không chứng minh integrated pipeline: evidence theo SHA, real OCR/LLM/API smoke và vanguard lại khi wiring thay đổi.

Checklist bàn giao cuối:

- [ ] Branches và SHA; danh sách files added/modified, shared-file justification.
- [ ] Criteria từng task + frozen API contract + requirement/test matrix.
- [ ] Baseline/post-change test counts/pass/fail/skip và OpenAPI/legacy behavior comparison.
- [ ] OCR model/license/revision/checksum selection, 3 config benchmark, metrics/latency/VRAM không điền số giả.
- [ ] Import/OCR 30 mẫu ×3 lượt, token metrics và BE API smoke thật; FE smoke ngoài phạm vi.
- [ ] Số vòng fix/review/test/vanguard từng task; finding list/status/risk acceptance Medium nếu có.
- [ ] Vanguard integrated gate, API handoff docs, STATE/LOG và resume instructions.
- [ ] READY_FOR_USER_MERGE; chờ xác nhận riêng trước merge main.

## 12. Trạng thái hiện tại

Plan đã viết dựa trên đọc code và khảo sát công cụ/web sơ bộ. Chưa tạo agent, worktree, criteria chính thức hay API mới; chưa tải model, chạy baseline/benchmark/test, commit hoặc merge. Người dùng đã cho phép execution sau rà soát; lượt hiện tại chỉ hoàn tất rà tài liệu và báo kết quả. Preflight Git/Vast/baseline cùng astra A là các bước đầu execution, không phải bằng chứng đã hoàn tất trong plan.
