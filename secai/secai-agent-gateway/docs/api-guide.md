# IAS.AI Gateway — API Reference

Toàn bộ API AI **đang chạy thật trên production**. Base URL: `http://<gateway>:8131`. Nền tảng chat: deploy 2026-09-02; bổ sung quản lý cuộc trò chuyện kiểu ChatGPT (§1.2) deploy 2026-09-18 (image `secai-agent-gateway:0.4.0-session`, nhánh `feat/session`).

**Không cần header xác thực** cho các endpoint AI (trừ `/admin/refresh-models`). **Không CORS-preflight** — chỉ `POST`/`GET`, header `Content-Type: application/json`.

**Ngôn ngữ:** chỉ nhận **tiếng Việt hoặc tiếng Anh**. Tin nhắn phần lớn ký tự phi Latin (Trung/Nhật/Hàn/Cyrillic...) bị từ chối tự động ở mọi endpoint chat — xem [§8 Mã lỗi](#8-mã-lỗi-chung).

---

## Mục lục
0. [SSE — quy ước dùng chung cho mọi endpoint `/stream`](#0-sse--quy-ước-dùng-chung)
1. [Chat](#1-chat)
   - [1.1 Chat theo session cụ thể — `/sessions/{sid}/chat`](#11-chat-theo-session-cụ-thể--sessionssidchat)
   - [1.2 Quản lý cuộc trò chuyện — `/sessions`, `/projects/{pid}/sessions`](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions)
   - [1.3 Quản lý project (thư mục chứa hội thoại) — `/projects`](#13-quản-lý-project-thư-mục-chứa-hội-thoại--projects)
2. [Hội thoại có lịch sử — `/ai/conversations/*`](#2-hội-thoại-có-lịch-sử--aiconversations) ⚠️ *chưa có trong bản build này*
3. [Dịch](#3-dịch--aitranslate)
4. [Báo cáo](#4-báo-cáo--aigenerate-report)
5. [Báo cáo thống kê](#5-báo-cáo-thống-kê--aigenerate-statistical-report)
6. [Nghiên cứu khai thác (exploit)](#6-nghiên-cứu-khai-thác--aiexploit)
7. [Health / Admin](#7-health--admin)
8. [Mã lỗi chung](#8-mã-lỗi-chung)
9. [Ghi chú vận hành & giới hạn đã biết](#9-ghi-chú-vận-hành--giới-hạn-đã-biết)

---

## 0. SSE — quy ước dùng chung

Mọi endpoint có hậu tố `/stream` trả `text/event-stream`. Dùng `fetch` + `ReadableStream` (**không** `EventSource` — đây là POST). Mỗi frame cách nhau `\n\n`, gồm dòng `event:` và `data:`.

| `event:` | `data` | Ý nghĩa |
|---|---|---|
| `token` | `{"delta": "..."}` | Nối vào bong bóng trả lời (typewriter) |
| `tool_call` | `{"id","name","input","summary"}` | Đang tra cứu — hiện chip trạng thái nếu muốn |
| `tool_result` | `{"id","name","is_error","duration_ms","result"}` | Kết quả tra cứu (cắt ở 4000 ký tự) |
| `regenerate` | `{"hook": "...", "reason": "..."}` | Bản nháp vừa stream bị hook nội bộ loại (vd trả lời chưa tra KB) — model viết lại từ đầu. **FE phải xoá sạch buffer `token` đã tích luỹ** khi nhận frame này (các `token` tiếp theo là câu trả lời mới) |
| `error` | `{"error": "..."}` | Lỗi runtime — vẫn có `token`+`final` theo sau |
| `final` | *(khác nhau theo endpoint — xem từng mục)* | **Nguồn chân lý** — luôn thay toàn bộ text đã render bằng nội dung `final` |
| — | `data: [DONE]` | Đóng stream |

⚠️ Khi hook grounding buộc model viết lại (thường gặp với câu hỏi CVE/CWE model trả lời mà chưa tra KB), stream phát 1 frame **`regenerate`** rồi bắt đầu lại chuỗi `token` mới. FE phải reset buffer khi thấy `regenerate`, nếu không bản nháp bị loại sẽ dính liền với bản viết lại. Dù vậy vẫn **luôn tin `final`**, không tự ghép `token` làm nguồn hiển thị/lưu cuối cùng.

**Mẫu parse JS (dùng chung mọi endpoint stream):**
```javascript
const res = await fetch(`${BASE}/ai/chat/stream`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buf = "", answer = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  let idx;
  while ((idx = buf.indexOf("\n\n")) >= 0) {
    const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
    const event = frame.match(/^event: (.+)$/m)?.[1];
    const data = frame.match(/^data: (.+)$/m)?.[1];
    if (!data || data === "[DONE]") continue;
    const payload = JSON.parse(data);
    if (event === "token") answer += payload.delta;
    else if (event === "final") answer = payload.content; // nguồn chân lý, ghi đè
  }
}
```

---

## 1. Chat

```
POST /ai/chat              (alias: POST /chat_no_stream)
POST /ai/chat/stream
```

**Request** (mọi endpoint AI dùng chung khung này — field khác `message` cũng được chấp nhận: `question`/`prompt`/`content`/`text`):
```json
{ "user_id": "user-42", "message": "SQL injection là gì?" }
```

**Response 200 (non-stream):**
```json
{
  "response": "SQL injection là...",
  "answer": "SQL injection là...",
  "message": "SQL injection là...",
  "metadata": { "run_id": "...", "profile": "chat", "tool_registry": ["qdrant_search","neo4j_search","poc_search"] }
}
```

**`final` frame (stream):** `{"content","answer","message","metadata"}` — cùng nội dung, khung khác.

**Grounding tự động:** câu hỏi nhắc mã CVE/CWE/CAPEC/ATT&CK sẽ khiến model **bắt buộc** tra cứu KB nội bộ (`qdrant_search`/`neo4j_search`) trước khi trả lời — quan sát được qua frame `tool_call` xuất hiện trước `token` đầu tiên. Câu hỏi khái niệm thuần trả lời thẳng.

**Bộ nhớ hội thoại:** đơn giản, 1 luồng/`user_id` (24h). Muốn **nhiều cuộc hội thoại song song + lưu bền lâu dài** → dùng [§2](#2-hội-thoại-có-lịch-sử--aiconversations) thay vì endpoint này.

**Ví dụ curl:**
```bash
curl -X POST http://<gateway>:8131/ai/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "user-42", "message": "SQL injection là gì?"}'
```

### 1.1 Chat theo session cụ thể — `/sessions/{sid}/chat`

```
POST /sessions/{sid}/chat
POST /sessions/{sid}/chat/stream
```

Biến thể của mục 1 khi caller **tự chỉ định session id** trên URL thay vì để gateway tự suy ra một luồng riêng từ `user_id` băm. Dùng khi cần nhiều cuộc hội thoại độc lập. Tạo session trước — hoặc **không thuộc project nào** (`POST /sessions`, kiểu "cuộc trò chuyện" độc lập như ChatGPT) hoặc **gắn với 1 project** (`POST /projects/{pid}/sessions`) — nhận về `session_id` rồi chat vào đúng `sid` đó. Xem chi tiết cách tạo/liệt kê/đổi tên/di chuyển session ở [§1.2](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions).

**Request:** giống hệt khung ở mục 1 (`message`/`question`/`prompt`/`content`/`text`, `user_id` tuỳ chọn) — `sid` nằm trên URL, không lặp lại trong body:
```json
{ "user_id": "user-42", "message": "SQL injection là gì?" }
```

**Response 200 (non-stream) / `final` frame (stream):** cùng khung `{"response"/"content","answer","message","metadata"}` như mục 1 — dùng chung cổng lọc đầu vào (ngôn ngữ không hỗ trợ, prompt-injection, PII) và bước làm sạch đầu ra với `/ai/chat`, chỉ khác bộ nhớ hội thoại đi vào đúng `sid` thay vì luồng theo `user_id`.

**404 `{"detail": "session not found"}`** nếu `sid` chưa tồn tại — endpoint này **không tự tạo session mồ côi**; phải tạo trước qua `POST /sessions` (standalone) hoặc `POST /projects/{pid}/sessions` (trong project). Với bản `/stream`, lỗi 404 trả về trước khi stream bắt đầu (không phải một SSE `error` frame).

**Tiêu đề & hoạt động:** mỗi lượt chat hợp lệ (kể cả khi bị chặn bởi cổng lọc và trả lời từ chối) cập nhật `last_activity` trên session; nếu session chưa có `title`, tự đặt bằng ~60 ký tự đầu của tin nhắn — chỉ lần đầu, không ghi đè tiêu đề đã có (kể cả tiêu đề đặt thủ công). Chỉ tin nhắn bị `422` (vd chứa bí mật) mới KHÔNG cập nhật gì — mọi trường hợp còn lại đều được coi là "đã có lượt chat".
Với bản `/stream`: việc cập nhật này diễn ra **ngay khi tin nhắn được chấp nhận, trước cả cổng lọc đầu vào và trước khi bắt đầu stream** — không đợi tới lúc stream kết thúc. Lý do: FE có thể huỷ kết nối SSE giữa chừng (vd người dùng chuyển sang cuộc trò chuyện khác), khi đó phần code chạy sau khi stream xong sẽ không bao giờ chạy — nếu chỉ cập nhật ở đó, lượt chat đầu tiên bị ngắt giữa chừng sẽ mãi mãi không có tiêu đề/`last_activity` và tải lại sẽ hiện "Cuộc trò chuyện mới". Việc cập nhật này là best-effort: một lỗi lưu meta không làm hỏng câu trả lời.

**Ví dụ:**
```bash
curl -X POST http://<gateway>:8131/sessions/proj:p1:sess:abc123/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "user-42", "message": "SQL injection là gì?"}'
```

### 1.2 Quản lý cuộc trò chuyện — `/sessions`, `/projects/{pid}/sessions`

CRUD cho **cuộc trò chuyện** (session) — như ChatGPT: một cuộc trò chuyện có thể **đứng độc lập** ("standalone", không thuộc project nào) hoặc **thuộc về 1 project**. `session_id` là chuỗi **mờ (opaque)** — FE không nên tự parse hay giả định định dạng (nội bộ hiện dùng `sess:{uuid}` cho standalone và `proj:{pid}:sess:{uuid}` cho session tạo trong project, nhưng việc chuyển session giữa project **không đổi `session_id`** — xem PATCH bên dưới).

| Method | Path | Việc gì |
|---|---|---|
| POST | `/sessions` | Tạo cuộc trò chuyện standalone (không thuộc project nào) |
| GET | `/sessions?user_id=&scope=standalone\|all` | Liệt kê cuộc trò chuyện của `user_id` (mặc định `scope=standalone`) |
| PATCH | `/sessions/{sid}` | Đổi tên và/hoặc chuyển cuộc trò chuyện vào/ra khỏi 1 project |
| GET | `/sessions/{sid}` | Xem chi tiết 1 cuộc trò chuyện (kèm `meta` đầy đủ) |
| DELETE | `/sessions/{sid}` | Xoá 1 cuộc trò chuyện |
| GET | `/sessions/{sid}/messages` | Lấy lịch sử tin nhắn |
| POST | `/projects/{pid}/sessions` | Tạo cuộc trò chuyện **thuộc project `pid`** |
| GET | `/projects/{pid}/sessions` | Liệt kê cuộc trò chuyện thuộc project `pid` |

**`SessionOut`** — hình dạng response chung cho mọi endpoint tạo/liệt kê/đổi (`POST /sessions`, `GET /sessions`, `PATCH /sessions/{sid}`, `POST`/`GET /projects/{pid}/sessions`):
```json
{
  "session_id": "sess:6f1a...",
  "title": "SQL injection là gì?",
  "created_at": "2026-09-18T09:00:00+00:00",
  "updated_at": "2026-09-18T09:05:00+00:00",
  "project_id": null,
  "last_activity": "2026-09-18T09:05:00+00:00"
}
```
`project_id` là `null` cho session standalone, hoặc id project khi thuộc 1 project. `last_activity` là mốc thời gian của lượt chat gần nhất (rơi về `created_at` nếu chưa chat lượt nào) — **danh sách sắp theo `last_activity` giảm dần, KHÔNG phải `updated_at`** (đổi tên cũng cập nhật `updated_at` nhưng không đẩy session lên đầu danh sách). Không phân trang.

**Tạo standalone:**
```bash
curl -X POST http://<gateway>:8131/sessions \
  -d '{"user_id": "user-42", "title": "Câu hỏi nhanh"}'
```
`400` nếu body có `project_id` khác `null` (dùng `POST /projects/{pid}/sessions` để tạo session thuộc project), hoặc `title` dài quá 200 ký tự.

**Liệt kê:** `GET /sessions?user_id=user-42` → chỉ session standalone của `user-42`. Thêm `scope=all` để gồm cả session thuộc project. `scope` khác `standalone`/`all` → `422`.

**Đổi tên / di chuyển:**
```bash
curl -X PATCH http://<gateway>:8131/sessions/sess:abc123 \
  -d '{"user_id": "user-42", "title": "Tên mới", "project_id": "proj-xyz"}'
```
- Bỏ trống `project_id` trong body → không di chuyển (giữ nguyên project hiện tại, hoặc vẫn standalone).
- `project_id: null` tường minh → chuyển ra standalone.
- `project_id: "<pid>"` → chuyển vào project đó (phải tồn tại và thuộc đúng chủ, nếu không → `404`).
- `400` nếu không có gì để cập nhật (body rỗng), `title` rỗng/toàn khoảng trắng, hoặc `title` > 200 ký tự.
- `404` nếu `sid` không tồn tại **hoặc** không phải một session hợp lệ (chặn thao tác lên các row hội thoại "cũ" như `chat:<hash>`/`exploit:...`); `403` nếu `user_id` không phải chủ session.

**Quyền sở hữu:** một `user_id` chỉ thấy/sửa được session của chính mình. Đọc (GET) sai chủ → `404` (không tiết lộ sự tồn tại); sửa (PATCH/DELETE/tạo trong project sai chủ) → `403`.

**Rate limit:** `/sessions`, `/projects`, và mọi path bắt đầu bằng `/projects/` hoặc `/sessions/` (trừ `/sessions/{sid}/chat[/stream]`, vẫn 90/60s như mục 1.1) = **120 req/60s** — CRUD nhẹ hơn nhiều so với 1 lượt LLM, nhưng nhiều user FE chia sẻ chung 1 IP proxy nên mức mặc định 15/60s sẽ chặn nhầm traffic hợp lệ.

---

### 1.3 Quản lý project (thư mục chứa hội thoại) — `/projects`

CRUD cho **project** — cái "thư mục" gom nhiều cuộc trò chuyện (như ChatGPT Projects). Session có thể đứng độc lập hoặc thuộc 1 project; tạo/liệt kê session **bên trong** 1 project dùng `/projects/{pid}/sessions` ở [§1.2](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions). Mục này là CRUD cho **bản thân project**.

| Method | Path | Việc gì |
|---|---|---|
| POST | `/projects` | Tạo project mới |
| GET | `/projects?user_id=` | Liệt kê project của `user_id` |
| GET | `/projects/{pid}?user_id=` | Xem chi tiết 1 project |
| PATCH | `/projects/{pid}` | Đổi tên project |
| DELETE | `/projects/{pid}?user_id=` | Xoá project **và toàn bộ session thuộc nó** (cascade) |

**`ProjectOut`** — hình dạng response chung cho tạo/liệt kê/xem/đổi tên (`created_at`/`updated_at` chuẩn hoá về ISO-8601 như `SessionOut`):
```json
{
  "project_id": "proj:8c2e...",
  "name": "Đợt pentest Q3",
  "owner": "user-42",
  "created_at": "2026-09-18T09:00:00+00:00",
  "updated_at": "2026-09-18T09:00:00+00:00"
}
```

**Tạo / Đổi tên:**
```bash
curl -X POST http://<gateway>:8131/projects \
  -d '{"user_id": "user-42", "name": "Đợt pentest Q3"}'
curl -X PATCH http://<gateway>:8131/projects/proj:8c2e \
  -d '{"user_id": "user-42", "name": "Tên mới"}'
```
- `name` **bắt buộc**; rỗng/toàn khoảng trắng → `400 {"error":"bad_request","message":"name is required"}`.
- `user_id` bỏ trống/rỗng → chủ sở hữu mặc định `"anonymous"` (nhất quán với caller ẩn danh ở các endpoint khác).

**Liệt kê:** `GET /projects?user_id=user-42` → mảng `ProjectOut` của đúng `user_id` đó (không phân trang).

**Xoá (cascade):**
```bash
curl -X DELETE "http://<gateway>:8131/projects/proj:8c2e?user_id=user-42"
```
→ xoá project **cùng mọi session con** của nó, trả `{"deleted": <số session đã xoá kèm theo>}`. Session standalone (không thuộc project) không bị ảnh hưởng.

**Quyền sở hữu:** một `user_id` chỉ thấy/sửa project của chính mình.
- `GET` sai chủ hoặc `pid` không tồn tại → `404 {"error":"not_found","message":"project not found: <pid>"}` (không tiết lộ sự tồn tại).
- `PATCH`/`DELETE` sai chủ → `403 {"error":"forbidden","message":"not the project owner"}`.

**Rate limit:** chung nhóm `/projects*` = **120 req/60s** (xem [§1.2](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions)).

---

## 2. Hội thoại có lịch sử — `/ai/conversations/*`

> ⚠️ **Nhóm endpoint `/ai/conversations/*` mô tả trong mục này KHÔNG có trong bản build hiện tại của gateway** (gọi vào sẽ trả `404`). Muốn nhiều cuộc hội thoại song song + lưu bền, dùng [§1.2](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions) (`/sessions`, `/projects/{pid}/sessions`) thay thế. Giữ lại nội dung mục này để tham khảo hình dạng response tương lai/lịch sử — đừng tích hợp FE vào các path bên dưới.

Bản mở rộng của mục 1: **nhiều cuộc hội thoại/`user_id`**, lưu bền không hết hạn, đổi tên tự động + thủ công.

| Method | Path | Việc gì | `user_id` |
|---|---|---|---|
| POST | `/ai/conversations/chat` | Gửi tin (không stream) | tuỳ chọn* |
| POST | `/ai/conversations/chat/stream` | Gửi tin (SSE) | tuỳ chọn* |
| GET | `/ai/conversations` | Liệt kê hội thoại | **bắt buộc** |
| GET | `/ai/conversations/{id}` | Đọc transcript đầy đủ | **bắt buộc** |
| POST | `/ai/conversations` | Tạo hội thoại rỗng | **bắt buộc** |
| PATCH | `/ai/conversations/{id}` | Đổi tên | **bắt buộc** |
| DELETE | `/ai/conversations/{id}` | Xoá | **bắt buộc** |

\* Không `user_id` vẫn chat được nhưng **không lưu lại** — luôn gửi `user_id` nếu cần lịch sử.

**Gửi tin — request:** `{"user_id","conversation_id"?,"message"}` (để trống `conversation_id` → tạo mới). **Response** = superset mục 1, thêm `conversation_id` (top-level + trong `metadata`). Stream thêm 1 frame đầu `event: conversation` `{"conversation_id"}`.

**Tiêu đề tự động:** vài giây sau lượt đầu, hệ thống tự tóm tắt thành tiêu đề ngắn (không cần FE gọi gì). Đổi tên thủ công (`PATCH`) **luôn thắng**, kể cả khi tự động chưa chạy xong.

**Liệt kê:**
```bash
curl "http://<gateway>:8131/ai/conversations?user_id=user-42&limit=20&offset=0"
```
```json
{ "conversations": [ {"conversation_id","title","created_at","updated_at","message_count"} ], "total": 1, "limit": 20, "offset": 0 }
```

**Đọc transcript:**
```bash
curl "http://<gateway>:8131/ai/conversations/<id>?user_id=user-42"
```
```json
{ "conversation_id","title","created_at","updated_at","message_count",
  "messages": [ {"role":"user|assistant","content":"...","seq":1,"created_at":...} ],
  "total": 4, "limit": 200, "offset": 0 }
```

**Tạo / Đổi tên / Xoá:**
```bash
curl -X POST   http://<gateway>:8131/ai/conversations -d '{"user_id":"user-42","title":"Tên tuỳ chọn"}'
curl -X PATCH  http://<gateway>:8131/ai/conversations/<id> -d '{"user_id":"user-42","title":"Tên mới"}'
curl -X DELETE "http://<gateway>:8131/ai/conversations/<id>?user_id=user-42"
```

**404** = không tồn tại **hoặc** không đúng chủ (không phân biệt, tránh dò tìm). **400** = thiếu `user_id`, hoặc `title` rỗng khi đổi tên.

---

## 3. Dịch — `/ai/translate`

```
POST /ai/translate
POST /ai/translate/stream
```
**Request:** `{"text": "...", "target_lang"?: "en"}` (không có `target_lang` → tự nhận diện, dịch Việt↔Anh).
**Response:** `{"translated_text","translation","metadata"}` (2 field text giống nhau).
```bash
curl -X POST http://<gateway>:8131/ai/translate \
  -d '{"text": "Cross-site scripting cho phép chèn script độc hại."}'
# → {"translated_text":"Cross-site scripting allows injecting malicious scripts.", ...}
```

---

## 4. Báo cáo — `/ai/generate-report`

```
POST /ai/generate-report
POST /ai/generate-report/stream
```
**Request:** `{"content": "log/dữ liệu thô cần viết báo cáo", "sections"?: ["overview","details","recommendations"]}`.
**Response:** `{"report", "metadata"}` — markdown, chia theo `sections`.
```bash
curl -X POST http://<gateway>:8131/ai/generate-report \
  -d '{"content": "Phát hiện SQL Injection tại /login qua tham số username, chưa vá."}'
```

---

## 5. Báo cáo thống kê — `/ai/generate-statistical-report`

```
POST /ai/generate-statistical-report
POST /ai/generate-statistical-report/stream
```
**Request:** `{"from_date","to_date","summary"?,"detail"?}`.
**Response (khung legacy Gemini, giữ tương thích ngược):**
```json
{ "report_title": "...", "report_summary": "...", "report_detail": "...",
  "report": "...", "statistical_report": "...", "metadata": {...} }
```
`report_title/summary/detail` được model trả JSON rồi hệ thống tách ra; nếu model không trả đúng JSON, cả 2 field `report`/`statistical_report` vẫn có nguyên văn, còn 3 field tách chỉ `report_summary` có nội dung (fallback).

---

## 6. Nghiên cứu khai thác — `/ai/exploit`

```
POST /ai/exploit
POST /ai/exploit/stream
```
**Request:** `{"question": "CVE-2024-3400"}` (hoặc form field `vulnerability_type`/`target_system`/`context`).
**Response:** `{"description","exploit_guide","metadata"}` — 2 field text giống nhau (PoC + phân tích, markdown 3 mục: Mã khai thác, Cách chạy, Tổng quan lỗ hổng).
`metadata` thêm: `evidence_ids`, `edb_ids`, `provenance`, `candidate_status` (`generated`/`not_generated`/...), `reference_code_injected`.

**Câu hỏi trống/chào hỏi thuần** (không có mã CVE) → trả lời ngắn xin mã CVE thay vì báo cáo rỗng.

> ⚠️ Các endpoint khai thác nâng cao trước đây (`/ai/bughunt/stream`, `/ai/exec`, `/ai/verify`, `/ai/tool`, `/ai/exploit/run`) **đã bị gỡ khỏi production** theo yêu cầu (2026-09-02) — gọi vào các path này giờ trả `404`.

---

## 7. Health / Admin

```
GET /health
```
```json
{ "status": "ok|degraded", "llm": true, "redis": "connected", "searchsploit": true,
  "exploitdb_commit": "...", "input_sanitize": {"total":0,"unknown":0,"unknown_rate":0.0} }
```
`status: degraded` khi LLM không phản hồi, hoặc bộ lọc đầu vào fail-open >50% trên mẫu ≥20 request gần nhất.

```
POST /admin/refresh-models   (header X-API-Key bắt buộc)
```

---

## 8. Mã lỗi chung

| Mã | Khi nào | Body |
|---|---|---|
| `400` | Quản lý session (§1.2): thiếu `name`/`title`, `title` > 200 ký tự, `POST /sessions` có `project_id` khác null, hoặc PATCH không có gì để cập nhật. Quản lý project (§1.3): thiếu `name` khi tạo/đổi tên project | `{"error","message"}` |
| `403` | Sửa/xoá session/project không phải của mình (mutate sai chủ — §1.2/§1.3) | `{"error","message"}` |
| `404` | Session/project không tồn tại hoặc đọc sai chủ (không tiết lộ tồn tại); PATCH lên row hội thoại cũ (`chat:`/`exploit:`); **hoặc endpoint đã bị gỡ** (`/ai/conversations/*`, §2) | `{"detail"}` hoặc `{"error","message"}` |
| `422` | Request rỗng — thiếu mọi field text hợp lệ; hoặc `scope` không hợp lệ ở `GET /sessions` | `{"detail": "..."}` |
| `429` | Vượt rate limit | `{"detail": "rate limit exceeded"}` |
| `503` | Model tạm thời không phản hồi | `{"detail": "AI service unavailable: ..."}` |

**Chặn ngôn ngữ** (không phải lỗi HTTP — vẫn `200`, trả lời song ngữ từ chối):
```json
{ "response": "Xin lỗi, IAS.AI hiện chỉ hỗ trợ tiếng Việt và tiếng Anh. ... / Sorry, IAS.AI currently supports Vietnamese and English only. ...",
  "metadata": { "short_circuit": true, "input_classification": {"label": "unsupported_language"} } }
```
Áp dụng cho mọi endpoint chat khi tin nhắn phần lớn ký tự phi Latin.

---

## 9. Ghi chú vận hành & giới hạn đã biết

- **Rate limit** (mỗi IP): `/ai/chat`, `/sessions/{sid}/chat[/stream]` = **90 req/60s**; `/sessions`, `/projects`, và mọi path bắt đầu bằng `/projects/`/`/sessions/` (ngoại trừ chat ở trên) = **120 req/60s** (xem [§1.2](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions)); `/ai/exploit` = 20/60s; `/ai/translate`, `/ai/generate-report`, `/ai/generate-statistical-report` = 10/60s; còn lại = 15/60s mặc định. `/stream` tính chung hạn ngạch với bản không-stream.
- **Air-gap:** hệ thống **không gọi internet** khi trả lời (không `web_fetch`, không tra OWASP/CWE Top-N từ web) — toàn bộ dựa trên KB nội bộ (Qdrant/Neo4j) + kiến thức model.
- **SSE `regenerate`:** khi hook grounding buộc model viết lại (force-continue, thường khi câu hỏi CVE/CWE được trả lời mà chưa tra KB), stream phát 1 frame `regenerate` `{"hook","reason"}` đánh dấu ranh giới, rồi stream lại `token` mới. FE reset buffer khi nhận frame này (xem [§0](#0-sse--quy-ước-dùng-chung)); và bất kể có `regenerate` hay không, luôn ghi đè bằng `final.content` làm nội dung persistent — đừng dùng chuỗi `token` ghép lại.
- **Bộ nhớ hội thoại:**
  - Chat theo `user_id` (`/ai/chat`, mục 1): 1 luồng/`user_id`, TTL 24h.
  - Chat theo session (`/sessions/{sid}/chat`, mục [1.1](#11-chat-theo-session-cụ-thể--sessionssidchat)/[1.2](#12-quản-lý-cuộc-trò-chuyện--sessions-projectspidsessions)): **SQLite bền, không hết hạn 24h** — mỗi cuộc trò chuyện (standalone hoặc trong project) là 1 session riêng, tải lại vẫn còn. Đây là cơ chế thay cho `/ai/conversations/*` (mục 2, không có trong build này).
- **Không cần API key** cho endpoint AI — chỉ `/admin/refresh-models` yêu cầu `X-API-Key` khớp biến môi trường `ADMIN_API_KEY`.
