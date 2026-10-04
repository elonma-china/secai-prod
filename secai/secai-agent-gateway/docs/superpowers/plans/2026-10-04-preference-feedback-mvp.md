# Kế hoạch MVP đánh giá A/B và phản hồi chuyên gia

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task after user review. Steps use checkbox syntax. Do not spawn agents unless execution is separately authorized to use them.

**Goal:** Bàn giao công cụ trong SecAI để quản trị nhập và phân công mẫu A/B, chuyên gia đánh giá, quản trị duyệt kết quả và xuất dữ liệu preference có truy vết.

**Architecture:** Thêm module review trong FastAPI và một màn hình React; lưu snapshot và nhãn trong SQLite riêng. API bất đồng bộ chuyển các giao dịch SQLite đồng bộ sang worker thread. Không gọi model trong request chấm điểm; nguồn MVP là cặp câu trả lời nhập từ JSONL.

**Tech Stack:** Python >= 3.10, FastAPI, Pydantic v2, sqlite3, React 18, TypeScript, Vite; tận dụng Markdown component hiện có.

**Spec:** Đặc tả đề xuất trong phần tiếp theo của chính tài liệu này. Đây là kế hoạch để duyệt, chưa phải thiết kế đã được chấp thuận hay mã đã triển khai.

## Đặc tả và giả định phạm vi

- Giao diện tiếng Việt nằm trong SecAI; nhóm thử nghiệm 1 quản trị viên và 2–3 chuyên gia.
- Bắt đầu bằng hỏi đáp an ninh mạng dạng văn bản. Cùng một ngữ cảnh có đúng hai câu trả lời hoàn chỉnh.
- MVP nhập JSONL chứa cả hai câu trả lời. Quản trị có thể lấy dữ liệu từ lần chạy đánh giá đã có; MVP không tự gọi LLM tạo B, không đưa nút A/B ngẫu nhiên vào chat đang chạy.
- Bản mở rộng sau MVP mới thêm lấy mẫu từ session, tạo B trong phiên cô lập và đánh giá ngay trong chat. Việc này cần thiết kế riêng để giữ nguyên lịch sử và ngữ cảnh grounding.
- Không huấn luyện Reward Model/DPO, không tự triển khai model, không hứa hẹn tăng chất lượng model trong đợt bàn giao công cụ.
- Bàn giao dữ liệu trung gian rõ nghĩa `prompt/chosen/rejected`; bên huấn luyện chịu trách nhiệm adapter/chat template theo trainer thực tế. Chưa khẳng định tương thích trực tiếp với dịch vụ MLOps hiện có.

## Global Constraints

- Python >= 3.10; giữ pin `linch>=1.2.1,<2`.
- Chỉ thêm /review-api/*; tuyệt đối không sửa API hiện hữu (endpoint, auth, JSON, SSE, rate limit hoặc hành vi) khi chưa có phép rõ ràng của người dùng.
- Không ghi candidate B, nhãn, prompt nội bộ hoặc bản nháp vào lịch sử hội thoại.
- Không bỏ redaction, anti-XSS, air-gap hay cổng thực thi.
- Không dùng `user_id` do client gửi để chứng minh danh tính người chấm.
- Không gọi mạng thật trong unit test; mock toàn bộ dịch vụ ngoài được sử dụng.
- Không sửa dữ liệu/bằng chứng nghiệm thu cũ; dữ liệu review đặt riêng.
- Không commit khóa, credential, dataset cá nhân, SQLite database hoặc export thật.
- Không triển khai Docker hay chạy live eval trong giai đoạn lập kế hoạch.

## Luồng và màn hình

1. Quản trị tạo đợt đánh giá, nhập JSONL và phân công mẫu cho chuyên gia.
2. Chuyên gia mở danh sách được giao, xem ngữ cảnh và hai câu trả lời, chấm rồi chuyển mẫu tiếp theo.
3. Backend hoán đổi A/B ổn định theo mỗi assignment; ẩn model và nguồn candidate khỏi payload của chuyên gia.
4. Chuyên gia chọn `left`, `right`, `tie`, `both_bad`, `insufficient_context` và có thể gắn nhãn lỗi cho từng candidate.
5. Hai lựa chọn cuối yêu cầu lý do. Nhãn lỗi v1: `incorrect_fact`, `unsupported_claim`, `incomplete`, `irrelevant`, `unsafe`, `other`; `other` yêu cầu diễn giải.
6. Người chấm không thấy phiếu của người khác trước khi nộp. Phiếu đã nộp bất biến trong MVP.
7. Quản trị xem kết quả, bất đồng và ra quyết định cuối kèm lý do. Không tự suy ra nhãn huấn luyện bằng đa số.
8. Export raw giữ tất cả loại phản hồi; export preference chỉ lấy kết quả đã duyệt có người thắng rõ ràng.

Ba vùng UI trong một màn hình: đợt đánh giá/quản trị; hàng đợi/màn hình A/B; duyệt kết quả/export. Tận dụng component Markdown có sẵn; không render raw HTML không được lọc.

Rubric v1 hiển thị cố định: ưu tiên đúng kỹ thuật và có bằng chứng; sau đó đủ ý và phù hợp câu hỏi; không chọn chỉ vì câu trả lời dài. Phân biệt không có bằng chứng với đã xác minh sai. Đây là hướng dẫn lựa chọn, chưa phải công thức reward số.

## Dữ liệu và trạng thái

SQLite riêng `data/reviews.db`, đường dẫn cấu hình `REVIEW_DB_PATH`; bảng/schema được tạo idempotent, có `schema_version=1`.

| Bảng | Trường và ràng buộc chính |
|---|---|
| campaigns | id, title, rubric_version=`security_qa_v1`, state=`open/closed`, creator_id, created_at |
| items | id, campaign_id, external_id, prompt_messages, candidate_1, candidate_2, evidence, provenance, content_sha256; unique(campaign_id, external_id) |
| assignments | id, item_id, reviewer_id, left_candidate_id, right_candidate_id, created_at; unique(item_id, reviewer_id) |
| judgments | id, assignment_id unique, decision, candidate_error_tags, comment, submitted_at |
| adjudications | id, item_id unique, outcome, winning_candidate_id nullable, reason, admin_id, created_at |
| audit_events | id, actor_id, action, entity_id, created_at, details; không ghi credential |

- Snapshot chứa ngữ cảnh user/assistant được phép hiển thị, hai candidate có ID ổn định, bằng chứng được phép chia sẻ và metadata nguồn. Không nhập system prompt, tool secret hoặc thinking.
- Giới hạn đề xuất: upload 10 MiB, tối đa 500 mẫu/lần, 64 KiB văn bản/candidate, 1 MiB/mẫu, 4.000 ký tự/nhận xét. Đọc upload có giới hạn, không tải vô hạn trước khi kiểm tra.
- Import kiểm tra toàn bộ trước khi ghi; dòng lỗi trả vị trí và lý do. External ID trùng cùng hash là bỏ qua; trùng khác hash trả 409, rollback cả đợt import.
- Từ chối candidate rỗng hoặc hai candidate giống nhau sau strip. Metadata thiếu được ghi rõ `unknown`; không suy đoán model/version.
- Khi phân công, item phải có tối thiểu một reviewer; một phần mẫu sẽ giao hai người để đo đồng thuận.
- Item chỉ được duyệt sau khi mọi assignment đã nộp. Admin có thể bỏ assignment chưa nộp với audit trước khi duyệt. Sau duyệt không thêm/bỏ assignment hoặc sửa snapshot.
- Nộp lại cùng payload trả bản ghi hiện tại; khác payload trả 409. Transaction và unique constraint xử lý hai tab gửi đồng thời.
- Campaign closed chặn import/phân công/nộp/duyệt; vẫn xem và xuất. Admin được mở lại với audit; quyết định đã duyệt vẫn bất biến.
- MVP chưa hỗ trợ sửa nhãn đã duyệt; phát hiện lỗi thì loại cả campaign khỏi sử dụng và tạo đợt thay thế, không ghi đè dataset đã xuất.

## Danh tính và phân quyền MVP

Đề xuất tối thiểu khi chưa xác minh có SSO: mỗi người có một bearer token ngẫu nhiên entropy cao do quản trị cấp ngoài UI; backend cấu hình mapping token hash -> actor_id/role (`admin` hoặc `reviewer`). Lưu cấu hình ở file riêng `REVIEW_CREDENTIALS_FILE`, không vào git; hash token bằng SHA-256, không dùng mật khẩu do người dùng tự đặt. UI nhập token một lần và giữ trong memory, logout xóa token; không lưu localStorage hoặc URL. Bắt buộc HTTPS khi triển khai ngoài localhost, không ghi Authorization vào log, hỗ trợ thu hồi bằng cập nhật file và restart.

`REVIEW_ENABLED=false` mặc định; bật mà thiếu credential hợp lệ thì fail closed. Dùng cùng origin và Vite/reverse proxy cho /review-api; không sửa CORS hoặc middleware chung của API hiện có. Admin xem toàn bộ các campaign thuộc công cụ nội bộ; reviewer chỉ thấy assignment của mình. Việc nhập snapshot đồng nghĩa quản trị cho phép chuyên gia được giao đọc nội dung đó; không tạo API quét tất cả session.

Nếu hệ thống đích có SSO, thay dependency `get_review_actor` bằng identity từ SSO đã xác thực; không tự tin header actor do browser gửi. Đây là điểm cần xác nhận trước triển khai thực tế.

## Hợp đồng API đề xuất

Prefix `/review-api`; lỗi theo FastAPI `detail`; timestamp UTC ISO-8601, ID UUID; danh sách `items/total/offset/limit`, limit mặc định 50 và tối đa 100.

| Endpoint | Quyền và hành vi |
|---|---|
| GET /me | Danh tính và role từ credential |
| GET /reviewers | Admin lấy ID chuyên gia để phân công; không trả token/hash |
| POST /campaigns | Admin tạo title, rubric_version |
| GET /campaigns | Admin xem tất cả; reviewer chỉ campaign có assignment của mình |
| PATCH /campaigns/{id} | Admin đổi open/closed, ghi audit |
| POST /campaigns/{id}/imports | Admin upload JSONL; trả imported/skipped |
| GET /campaigns/{id}/items | Admin xem mẫu, trạng thái nộp/duyệt, lọc bất đồng |
| POST /items/{id}/assignments | Admin gửi reviewer_ids; lặp cùng người không nhân bản |
| DELETE /assignments/{id} | Admin bỏ assignment chưa nộp và item chưa duyệt |
| GET /assignments | Reviewer hàng đợi của mình; lọc pending/submitted |
| GET /assignments/{id} | Nội dung A/B và rubric; không lộ model hay phiếu khác |
| PUT /assignments/{id}/judgment | Nộp quyết định, error_tags, comment |
| GET /items/{id}/judgments | Admin xem phiếu được chuẩn hóa về candidate ID |
| PUT /items/{id}/adjudication | Admin chốt outcome/candidate_id/reason, bất biến và idempotent |
| GET /campaigns/{id}/stats | Admin số mẫu, assignment hoàn tất, chờ duyệt, bất đồng |
| GET /campaigns/{id}/export?format=raw\|preference | Admin tải JSONL cùng manifest qua ZIP |

Export snapshot nhất quán trong transaction đọc, thứ tự item ID ổn định. Manifest gồm schema/rubric version, campaign ID, thời điểm, counts và SHA-256 file. Raw giữ provenance và phiếu; preference gồm `item_id`, `prompt` (messages), `chosen`, `rejected`, `rubric_version`, `adjudication_id`. Không có winner thì không xuất preference. Chưa chia train/test tự động: giữ source_group để bên ML chia theo hội thoại/tài liệu, tránh rò rỉ giữa tập.

## Review Focus

1. Hai tab cùng nộp hoặc mạng retry: chỉ một phiếu, payload khác bị 409 — Task 1/3.
2. A/B đảo vị trí: export vẫn chọn đúng candidate, không dùng nhãn left/right làm danh tính — Task 3/5.
3. Người chấm đổi ID hoặc giả user_id: không đọc/ghi assignment người khác — Task 2/3.
4. JSONL chứa script, secret hoặc dòng quá lớn: giới hạn ở API; UI không thực thi HTML; không xuất credential — Task 3/4/5.
5. Thiếu ngữ cảnh, hòa, cả hai sai hoặc chưa duyệt: giữ raw, không tạo cặp preference giả — Task 3/5.

## Bản đồ file dự kiến

- Tạo `src/secai_gateway/api/reviews.py`: router, upload giới hạn, permission và gọi store.
- Tạo `src/secai_gateway/api/review_schemas.py`: Pydantic input/output, enums và giới hạn.
- Tạo `src/secai_gateway/api/review_auth.py`: ReviewActor và xác thực token.
- Tạo `src/secai_gateway/state/review_store.py`: transactions, trạng thái, audit và export snapshot.
- Sửa `src/secai_gateway/config.py`, `app.py`: chỉ thêm settings/state/router review, không sửa middleware dùng chung.
- Tạo `secai-fe-devspace/src/reviewApi.ts`, `reviewTypes.ts`, `screens/ReviewScreen.tsx`, `screens/review/ReviewPair.tsx`, `screens/review/ReviewAdmin.tsx`.
- Sửa `secai-fe-devspace/src/App.tsx`, `types.ts`, `styles.css`: navigation và style cần thiết.
- Tạo `tests/test_review_store.py`, `test_review_auth.py`, `test_review_api.py`, `test_review_export.py`.
- Tạo `docs/api/review-mvp.md`, `docs/review-operator-guide.md` và fixture giả `tests/fixtures/review_pairs.jsonl`.

## Task 1 — Schema và lưu dữ liệu (2–3 ngày)

**Interfaces:** `ReviewStore(path: Path)` với `import_items(campaign_id: str, items: list[ReviewItemInput], actor_id: str) -> ImportResult`, `submit_judgment(assignment_id: str, reviewer_id: str, payload: JudgmentInput) -> JudgmentOut`. Models nằm trong review_schemas; store không phụ thuộc Request/FastAPI. Mọi write và audit chung một transaction.

- [ ] Viết test `test_import_atomic_conflict`, `test_duplicate_import_skips`, `test_judgment_retry_idempotent`, `test_parallel_submission_unique`, `test_restart_preserves_snapshot` với tmp_path.
- [ ] Chạy `.venv/bin/python -m pytest -q tests/test_review_store.py`; xác nhận fail do chức năng chưa có.
- [ ] Tạo schema/models/store tối thiểu theo bảng; transaction sqlite timeout hữu hạn, foreign_keys=ON, không chia sẻ connection qua thread.
- [ ] Chạy lại test, yêu cầu toàn bộ pass.

## Task 2 — Auth và tích hợp app (2 ngày)

**Interfaces:** `ReviewActor(actor_id: str, role: Literal['admin','reviewer'])`; `get_review_actor(request: Request) -> ReviewActor`, `require_review_admin` làm dependency. GatewayState giữ review_store khi bật.

- [ ] Viết `test_disabled_routes_unavailable`, `test_missing_or_revoked_token_401`, `test_reviewer_admin_action_403`, `test_invalid_credential_config_fails_closed`.
- [ ] Chạy `.venv/bin/python -m pytest -q tests/test_review_auth.py`, xác nhận fail đúng lý do.
- [ ] Thêm settings, auth dependency và router; dùng `asyncio.to_thread` tại ranh giới gọi store; giữ nguyên CORS và middleware của API hiện có.
- [ ] Chạy lại test auth và `tests/test_projects_sessions.py`; identity review không thay cơ chế session hiện tại.

## Task 3 — API quản lý và đánh giá (3–4 ngày)

**Interfaces:** Các endpoint trong bảng hợp đồng; `ReviewItemInput` chứa external_id, prompt_messages, candidates đúng 2 phần tử có id/text/provenance, evidence và source_group. `JudgmentInput` dùng decision theo vị trí hiển thị; store chuẩn hóa candidate ID bằng assignment đã lưu, không tin mapping client.

- [ ] Viết test import giới hạn/ký tự không hợp lệ, idempotency, phân trang, state closed, assignee-only, 2 reviewer độc lập, bắt buộc reason, IDOR, payload không lộ model.
- [ ] Thêm test `test_shuffle_stable_per_assignment`, `test_judgment_maps_to_candidate`, `test_adjudicate_requires_all_submissions`, `test_duplicate_adjudication_conflict`.
- [ ] Chạy `.venv/bin/python -m pytest -q tests/test_review_api.py`, xác nhận lỗi chức năng thiếu.
- [ ] Triển khai endpoint/schema/store cần thiết; kiểm tra Content-Length và thực tế số byte; import atomic; audit tạo/phân công/nộp/duyệt.
- [ ] Chạy lại suite review; xác nhận HTTP 401/403/404/409/413/422 đúng từng tình huống đã ghi trong test.

## Task 4 — UI quản trị và chuyên gia (4–5 ngày)

**Interfaces:** `reviewApi.ts` dùng endpoint Task 3 và token memory; `ReviewScreen` quản lý identity, campaign, queue và view; `ReviewPair` nhận assignment, onSubmit, saving/error. Không dùng api.ts hiện tại để tự động gắn token vào request chat.

- [ ] Tạo checklist UI trong operator guide: đăng nhập token, nhập 10 mẫu, giao hai người, lọc queue, chấm đủ năm kết quả, duyệt và logout.
- [ ] Thêm menu “Đánh giá A/B”, form token, hàng đợi, pair layout và admin view. UI không hiển thị nút admin cho reviewer nhưng API vẫn là nơi thực thi quyền.
- [ ] Có loading/empty/error/retry; giữ nội dung phiếu khi lỗi mạng; disable submit khi gửi, xác nhận nộp thành công trước khi chuyển mẫu.
- [ ] Kiểm tra bàn phím, focus, label, màn hình hẹp, câu trả lời dài và Markdown/code chứa HTML độc hại với fixture giả.
- [ ] Chạy `npm ci` và `npm run build` trong secai-fe-devspace; sửa lỗi build thuộc thay đổi. Dùng T3 preview nếu có để chạy checklist hai danh tính và ghi kết quả thực tế.

## Task 5 — Duyệt, thống kê và export (2–3 ngày)

**Interfaces:** `ReviewStore.export_snapshot(campaign_id: str) -> ReviewExportSnapshot`; xây JSONL/manifest chỉ từ snapshot đó. Stats bất đồng tính trên candidate ID và decision, không trên vị trí A/B; hiển thị cả tử/mẫu số cho mẫu có >=2 phiếu.

- [ ] Viết `test_export_approved_preferences_only`, `test_export_correct_after_shuffle`, `test_raw_retains_non_winners`, `test_manifest_hash_and_counts`, `test_export_consistent_during_submission`.
- [ ] Viết fixture một A thắng, một B thắng, hòa, cả hai sai, thiếu ngữ cảnh và mẫu chưa duyệt; assert đúng số dòng, đúng prompt/chosen/rejected và không có token.
- [ ] Chạy `.venv/bin/python -m pytest -q tests/test_review_export.py`, xác nhận fail trước triển khai.
- [ ] Hoàn thiện adjudication/stats/export và nối nút UI; định dạng JSONL chuẩn Unicode, content-disposition filename an toàn.
- [ ] Chạy lại test và import file export bằng trình đọc JSONL để xác nhận schema, hash, counts. Không gọi training thật.

## Task 6 — Kiểm chứng và bàn giao (3–4 ngày)

- [ ] Hoàn thiện docs API, rubric v1, hướng dẫn cấp/thu hồi token, khởi chạy, backup/restore SQLite nhất quán và nhập/xuất; mô tả rõ giới hạn nguồn A/B.
- [ ] Chạy toàn bộ `.venv/bin/python -m pytest -q`; ghi rõ test fail/skip do dependency Bughunt ngoài checkout nếu có. Không coi suite chưa xong là pass.
- [ ] Chạy `git diff --check`, frontend build; chạy UI checklist từ import -> assignment -> judgment -> adjudication -> export, kiểm tra reload/restart vẫn giữ dữ liệu.
- [ ] Khi có dữ liệu được phép dùng, tổ chức pilot 100–200 mẫu, 2–3 chuyên gia; ít nhất 20% mẫu chấm độc lập bởi hai người. Ghi thời gian/mẫu, mức đồng thuận và tỷ lệ thiếu ngữ cảnh để sửa rubric, không tuyên bố cải thiện model.
- [ ] Bàn giao source, cấu hình mẫu không có secret, hướng dẫn vận hành, fixture giả, export mẫu/manifest và báo cáo kiểm thử. Deployment thực tế là bước riêng theo môi trường đã thống nhất.

## Tiêu chí nghiệm thu

- Admin nhập, phân công, theo dõi và duyệt được; reviewer chỉ truy cập phần được giao.
- Cùng assignment giữ nguyên thứ tự A/B sau reload; metadata model không lộ trong reviewer API.
- Năm lựa chọn được lưu chính xác, không mất hoặc nhân đôi phiếu khi retry/concurrent submit.
- Snapshot và phiếu tồn tại qua restart, không làm thay đổi hội thoại chat hiện có.
- Export preference chỉ có mẫu đã duyệt với winner rõ ràng; raw giữ đầy đủ phản hồi; manifest kiểm tra được.
- Markdown không thực thi script, credential không xuất hiện trong export/log, upload quá hạn bị từ chối.
- Có chứng cứ thực hiện UI checklist và kết quả backend/frontend checks; các lỗi môi trường được ghi rõ.
- Thành công của MVP đo bằng workflow và dữ liệu đúng; mức tăng chất lượng model không thuộc nghiệm thu này.

## Tiến độ và điểm cần xác nhận

Ước lượng 16–21 ngày công, khoảng 4–5 tuần cho một lập trình viên full-stack quen repo, có chuyên gia hỗ trợ rubric và QA. Đây là dự trù, chưa phải cam kết; thêm SSO/tự sinh B hoặc review trên chat sẽ làm đổi phạm vi.

Trước khi thực thi cần xác nhận: dùng UI riêng như đề xuất hay A/B ngay trong chat; nguồn cặp A/B ban đầu; dùng token nội bộ hay SSO sẵn có; nơi triển khai và người chịu trách nhiệm tiếp nhận dataset. Kế hoạch mặc định UI riêng + JSONL + token nội bộ để có phạm vi hoàn chỉnh, kiểm chứng được.

Các task triển khai theo thứ tự 1 -> 2 -> 3 -> 4 -> 5 -> 6; không cần hạ tầng worker/model mới trong MVP. Chỉ commit đường dẫn thuộc tính năng khi có yêu cầu commit; không stage toàn Git root nhiều dự án.


## Cập nhật phạm vi trong khi triển khai

Người dùng đã yêu cầu ưu tiên ship nhanh, chấp nhận mức hoàn thiện MVP 70% nhưng test cần pass và phải review. Không coi test lỗi là được phép bỏ qua; kiểm chứng cốt lõi và giới hạn môi trường được ghi riêng.

Bổ sung bắt buộc sau kế hoạch gốc: lời mời sinh câu B sau chat với xác suất đề xuất 10%, cooldown600s, session riêng; không thay API chat. Bổ sung sửa template báo cáo bằng prompt: revise -> preview -> apply -> render với API/profile mới không tool; người dùng xác nhận input sạch, không cung cấp detector injection chuyên dụng, giữ guard cũ. Template MVP lưu memory và nhập/xuất Markdown, chưa có thư viện template dùng chung. Hai phần này thay thế các câu “ngoài MVP” tương ứng trong đặc tả gốc. Không thực hiện huấn luyện RLHF/DPO hoặc chứng minh hiệu quả chất lượng model.

Nhánh thực hiện `feat/expert-review-mvp`, worktree `/home/ict/secai-review-mvp-wt`; frontend nested `secai/secai-agent-gateway/secai-fe-devspace` được snapshot từ workspace người dùng, không sửa bản đang dùng. Các agent triển khai đều GPT-6.1-Sol medium theo yêu cầu; bản gốc workspace không được chỉnh sửa.
