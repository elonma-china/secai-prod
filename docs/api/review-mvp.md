# API đánh giá A/B

Prefix `/review-api`; chỉ hoạt động khi `REVIEW_ENABLED=true`. Tất cả endpoint yêu cầu `Authorization: Bearer <token>`. Identity do credential server xác thực, không nhận user_id làm quyền truy cập. Cấu hình và workflow: [hướng dẫn vận hành](../review-operator-guide.md).

## Kiểu dữ liệu chung

Danh sách trả `{items, total, offset, limit}`; limit mặc định 50, tối đa 100. Timestamp UTC ISO-8601, ID do server cấp. Lỗi trả `detail`; 401 thiếu/sai token, 403 sai role, 404 đối tượng không tồn tại hoặc không thuộc người chấm, 409 xung đột trạng thái/nội dung, 413 vượt giới hạn upload, 422 dữ liệu không hợp lệ. Không đặt token vào query string.

## Endpoint

| Method/path | Quyền | Nội dung |
|---|---|---|
| GET /me | Đã xác thực | `{actor_id,role}` |
| GET /reviewers | Admin | Danh sách actor role reviewer, không có token/hash |
| POST /campaigns | Admin | `{title,rubric_version:"security_qa_v1"}` |
| GET /campaigns | Cả hai | Reviewer chỉ thấy campaign được giao |
| PATCH /campaigns/{id} | Admin | `{state:"open"\|"closed"}` |
| POST /campaigns/{id}/imports | Admin | Raw JSONL UTF-8, Content-Type application/x-ndjson |
| GET /campaigns/{id}/items | Admin | Danh sách snapshot; `disagreement=true` để lọc bất đồng |
| POST /items/{id}/assignments | Admin | `{reviewer_ids:["expert-1"]}` |
| DELETE /assignments/{id} | Admin | Chỉ assignment chưa nộp và item chưa duyệt; 204 |
| GET /assignments | Reviewer | `status=pending\|submitted`, `campaign_id` tùy chọn |
| GET /assignments/{id} | Người được giao | Nội dung blinded A/B, rubric và phiếu của mình |
| PUT /assignments/{id}/judgment | Người được giao | Quyết định theo vị trí hiển thị |
| GET /items/{id}/judgments | Admin | Phiếu đã chuẩn hóa theo candidate ID |
| PUT /items/{id}/adjudication | Admin | Quyết định cuối có lý do |
| GET /campaigns/{id}/stats | Admin | Tiến độ và bất đồng |
| GET /campaigns/{id}/export | Admin | `format=raw\|preference`, tải ZIP |

Campaign có `id,title,rubric_version,state,creator_id,created_at`. Đóng campaign chặn write, không chặn đọc/export. Snapshot và phiếu đã chốt không thay đổi khi mở lại campaign.

## Import

Mỗi dòng:

```json
{"external_id":"demo-001","prompt_messages":[{"role":"user","content":"HTTPS có thay thế phân quyền không?"}],"candidates":[{"id":"answer-1","text":"Không, ứng dụng vẫn phải kiểm tra quyền.","provenance":{"model":"synthetic"}},{"id":"answer-2","text":"Có, HTTPS đủ để cấp quyền.","provenance":{"model":"synthetic"}}],"evidence":"Ví dụ tổng hợp.","source_group":"demo-001","provenance":{"source":"fixture"}}
```

Candidate IDs và nội dung phải khác nhau. Metadata không có được ghi unknown theo schema; không suy đoán model từ văn bản. Chỉ nhận role user/assistant. Không đưa system prompt, thinking hoặc secret vào dataset. Upload toàn bộ tối đa 10 MiB/500 dòng, 1 MiB/mẫu, 64 KiB text/candidate. Kiểm tra trước khi ghi: có một dòng lỗi thì không import phần còn lại. Kết quả `{imported,skipped}`; external_id trùng cùng nội dung được skip, khác nội dung trả 409.

Ví dụ shell sau dùng biến token đã được nhập riêng; không bật shell tracing:

```sh
curl --fail-with-body \
  -H "Authorization: Bearer $REVIEW_TOKEN" \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @tests/fixtures/review_pairs.jsonl \
  "http://127.0.0.1:8187/review-api/campaigns/$CAMPAIGN_ID/imports"
```

## Phiếu chuyên gia

Assignment detail trả `left:{text}`, `right:{text}`, prompt_messages, evidence, rubric, rubric_version, status và judgment của người chấm. Payload không có model, provenance, source_group, candidate ID hoặc phiếu của người khác. Thứ tự được lưu trong assignment; server tự ánh xạ left/right sang candidate.

```json
{"decision":"left","error_tags":{"left":[],"right":["incorrect_fact"]},"comment":"Câu bên phải nhầm bảo vệ đường truyền với quyền truy cập."}
```

Decision: `left`, `right`, `tie`, `both_bad`, `insufficient_context`, `skip` (bỏ qua, không bắt buộc nhận xét). Tags: `incorrect_fact`, `unsupported_claim`, `incomplete`, `irrelevant`, `unsafe`, `other`. Comment tối đa 4.000 ký tự; bắt buộc khi both_bad/insufficient_context hoặc có tag other. Gửi lại payload giống hệt trả phiếu hiện tại; khác nội dung trả 409. MVP không có sửa phiếu đã nộp.

## Duyệt và thống kê

Quản trị đọc judgments với reviewer_id, winning_candidate_id, candidate_error_tags, comment và submitted_at, rồi gửi:

```json
{"outcome":"winner","winning_candidate_id":"answer-1","reason":"Đối chiếu tiêu chí: HTTPS không thay thế kiểm tra quyền."}
```

Outcome khác: `tie`, `both_bad`, `insufficient_context`, `skip` (bỏ qua), không kèm winner. Cần ít nhất một assignment và tất cả đã nộp. Quyết định bất biến/idempotent; payload khác bị 409. Lý do bắt buộc cho mọi quyết định.

Stats: `items`, `assignments`, `submitted`, `pending_adjudication`, `adjudicated`, `disagreements`, `multi_reviewed_items`. Bất đồng so kết quả đã chuẩn hóa, không so trực tiếp left/right. Mẫu số là số item có ít nhất hai phiếu nộp; không diễn giải đây là mức chất lượng model.

## Export

ZIP chứa `raw.jsonl` hoặc `preference.jsonl` và `manifest.json`. Raw giữ snapshot, assignments, judgments và adjudication. Preference chỉ xuất item có quyết định winner đã duyệt, gồm item_id, prompt dạng messages, chosen, rejected, rubric_version và adjudication_id. Hòa/cả hai sai/thiếu ngữ cảnh/chưa duyệt không được chuyển thành cặp huấn luyện giả.

Manifest ghi schema_version, rubric_version, campaign_id, exported_at, format, counts và files với SHA-256/số records. Export dùng snapshot nhất quán, thứ tự ổn định. Credential không bao giờ nằm trong export. Bên ML cần chuyển định dạng và kiểm tra đúng tokenizer/chat template; API này không tự gọi MLOps hay thực hiện training.


## Lời mời sinh cặp trong chat

`POST /review-api/live-pairs`, bearer token review, JSON:

```json
{"prompt_messages":[{"role":"user","content":"HTTPS có thay thế phân quyền không?"}],"answer":"Không. HTTPS bảo vệ đường truyền.","source_group":"session-reference","always_offer":true}
```

`source_group` chỉ là metadata, không cấp quyền đọc session. API không nhận session_id để ghi hội thoại. Chỉ nhận ngữ cảnh user/assistant công khai, tối đa 30 messages, tổng body 256 KiB và câu trả lời 64 KiB. Backend lấy mẫu/cooldown; không được mời trả `{offered:false}`; được mời trả `{offered:true,assignment:<blinded assignment>}`. Nộp nhãn qua endpoint judgment hiện có của module review. B được sinh độc lập ở session riêng với chat profile và guard hiện có. Không đổi contract hoặc hành vi của `/ai/chat` và `/sessions/{sid}/chat`.

Khi tích “Nhận lời mời đánh giá A/B”, Devspace gửi `always_offer:true` cho từng lượt hoàn tất: bỏ qua lấy mẫu và tạm bỏ cooldown cho luồng chủ động này. Request cũ không có cờ vẫn dùng sampling/cooldown. Các lượt của cùng reviewer xếp hàng để mỗi người chỉ chạy một lần sinh B đồng thời. Auth, giới hạn request/body, redaction và trạng thái campaign vẫn áp dụng. Hai phương án được giữ dưới từng câu trong cuộc trò chuyện đang mở; chuyển cuộc trò chuyện hoặc tải lại trang không khôi phục widget. Nếu sinh B lỗi, timeout hoặc trùng A, frontend hiện lỗi và nút “Thử lại A/B”, không tạo nội dung giả hoặc cặp trùng nhau.


## Sửa template báo cáo bằng prompt

Hai endpoint mới dùng cùng bearer review; không thay đổi `/ai/generate-report` hoặc `/ai/generate-statistical-report`.

- `POST /review-api/report-templates/revise`: `{template, instruction, input_clean_confirmed: true}` -> `{draft}`.
- `POST /review-api/report-templates/render`: `{template, logs, input_clean_confirmed: true}` -> `{report}`.

Template Markdown tối đa 64 KiB, instruction 8 KiB, logs 128 KiB, body 256 KiB. Phải xác nhận boolean true (không nhận chuỗi hoặc số). Không có bộ phát hiện prompt injection chuyên dụng; đây là xác nhận trách nhiệm của người dùng, không phải chứng minh dữ liệu an toàn. Guard secret/public-input hiện có vẫn áp dụng.

Revise trả bản nháp, không lưu đè template. Nếu mất/đổi placeholder `{{...}}` có sẵn hoặc bản nháp vượt 64 KiB thì trả 422 để giữ bản gốc. Chỉ xử lý văn bản, không thực thi template/Jinja/code. Model lỗi/trống/timeout 45 giây trả 503. Hai profile mới không có tools và dùng session tạm riêng được cleanup. Render tạo nội dung dựa trên dữ liệu cung cấp; người dùng cần kiểm tra kết quả trước khi dùng.

### Ràng buộc prompt sau review

`prompt_messages` của import và live phải bắt đầu và kết thúc bằng role `user`. Candidate answers được đặt riêng, không nối vào cuối prompt. Import sai cấu trúc trả 422 và rollback cả batch. Live generation khôi phục history công khai vào session UUID riêng, chỉ truyền câu hỏi cuối vào runner để guard phân loại đúng lượt hiện tại; history không được coi là bằng chứng grounding.
