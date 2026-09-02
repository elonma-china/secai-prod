# Chat History API — tài liệu tích hợp

Bộ API **mới** `/ai/conversations/*` cho phép mỗi người dùng có **nhiều cuộc trò chuyện**
riêng biệt (tạo / liệt kê / mở lại / xoá), với lịch sử lưu bền lâu dài.

> **API cũ `/ai/chat`, `/chat_no_stream`, `/ai/chat/stream` không đổi** — hệ thống Nhật đang
> dùng có thể tiếp tục chạy song song, không cần sửa gì.

Base URL: `http://<gateway>:8131` (production hiện tại).

---

## 1. Khái niệm

| | |
|---|---|
| **`conversation_id`** | uuid do server cấp khi tạo/gửi tin đầu tiên. Client giữ và gửi lại ở các lượt sau. |
| **Bộ nhớ sống** | Redis, 24h, agent tự nén khi hội thoại dài. Đây là nơi model *suy luận*. |
| **Bộ nhớ bền** | SQLite, **không hết hạn**. Đây là nơi `GET` đọc lại lịch sử — luôn đầy đủ dù bộ nhớ sống đã hết hạn. |
| **Sở hữu** | Tính từ `user_id` (băm), không đoán được id của người khác. Mọi endpoint quản lý **bắt buộc `user_id`**. |

---

## 2. Endpoints

| # | Method | Path | Việc gì | `user_id` |
|---|---|---|---|---|
| 1 | POST | `/ai/conversations/chat` | Gửi tin nhắn (không stream) | tuỳ chọn* |
| 2 | POST | `/ai/conversations/chat/stream` | Gửi tin nhắn (SSE) | tuỳ chọn* |
| 3 | GET | `/ai/conversations` | Liệt kê hội thoại | **bắt buộc** |
| 4 | GET | `/ai/conversations/{id}` | Đọc transcript | **bắt buộc** |
| 5 | POST | `/ai/conversations` | Tạo hội thoại rỗng | **bắt buộc** |
| 6 | DELETE | `/ai/conversations/{id}` | Xoá hội thoại | **bắt buộc** |

\* Không có `user_id` vẫn chat được (giống `/ai/chat` cũ) nhưng hội thoại **không lưu lại** — không liệt kê/mở lại được. Luôn gửi `user_id` nếu muốn dùng lịch sử.

**Rate limit:** endpoint 1–2 dùng chung hạn ngạch với `/ai/chat` (90 req/60s/IP). Endpoint 3–6: 15 req/60s/IP.

---

## 3. Gửi tin nhắn — không stream

```
POST /ai/conversations/chat
```

**Request**
```json
{ "user_id": "user-42", "conversation_id": "<uuid, để trống nếu tạo mới>", "message": "SQL injection là gì?" }
```

**Response 200** — khung giống hệt `/ai/chat` cũ, chỉ thêm `conversation_id`:
```json
{
  "response": "SQL injection là...",
  "answer": "SQL injection là...",
  "message": "SQL injection là...",
  "metadata": { "run_id": "...", "profile": "chat", "tool_registry": [...], "conversation_id": "a397e7...b690" },
  "conversation_id": "a397e7...b690"
}
```

**Ví dụ chạy được:**
```bash
curl -X POST http://<gateway>:8131/ai/conversations/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "user-42", "message": "SQL injection là gì?"}'
```
Lượt sau, gửi kèm `conversation_id` nhận được ở trên để tiếp tục **cùng** hội thoại:
```bash
curl -X POST http://<gateway>:8131/ai/conversations/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "user-42", "conversation_id": "a397e7...b690", "message": "Vậy XSS thì sao?"}'
```

---

## 4. Gửi tin nhắn — streaming (SSE)

```
POST /ai/conversations/chat/stream
```
Request giống mục 3. Response là `text/event-stream` — dùng `fetch` + `ReadableStream` (**không** dùng `EventSource`, vì đây là POST). Mỗi frame cách nhau `\n\n`, gồm dòng `event:` và `data:`.

| `event:` | `data` | Ý nghĩa |
|---|---|---|
| `conversation` | `{"conversation_id"}` | Frame **đầu tiên** — lưu id ngay |
| `token` | `{"delta"}` | Nối vào bong bóng trả lời (typewriter) |
| `tool_call` / `tool_result` | — | Đang tra cứu (hiện chip trạng thái nếu muốn) |
| `regenerate` | `{"hook","reason"}` | **Xoá sạch buffer token đã nhận**, chờ token mới |
| `compaction` | `{"strategy",...}` | (tuỳ chọn) hội thoại vừa được nén — chỉ để log |
| `error` | `{"error"}` | Có lỗi (vẫn có `token`+`final` theo sau) |
| `final` | `{"content","answer","message","metadata","conversation_id"}` | **Nguồn chân lý** — thay toàn bộ text đã render bằng `final.content` |
| — | `data: [DONE]` | Đóng stream |

**Ví dụ chạy được (xem thô qua curl):**
```bash
curl -N -X POST http://<gateway>:8131/ai/conversations/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "user-42", "message": "SSRF là gì?"}'
```
```
event: conversation
data: {"conversation_id": "faedc8d9...41f7f"}

event: token
data: {"delta": "SSRF"}

event: token
data: {"delta": " (Server-Side..."}

...

event: final
data: {"content": "SSRF (Server-Side Request Forgery)...", "answer": "...", "message": "...", "metadata": {...}, "conversation_id": "faedc8d9...41f7f"}

data: [DONE]
```

**Ví dụ FE (JavaScript):**
```javascript
const res = await fetch(`${BASE}/ai/conversations/chat/stream`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ user_id: userId, conversation_id: currentCid, message }),
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
    if (event === "conversation") currentCid = payload.conversation_id;
    else if (event === "token") answer += payload.delta;         // render dần
    else if (event === "regenerate") answer = "";                 // xoá, chờ lại
    else if (event === "final") answer = payload.content;         // chốt bằng bản final
  }
}
```

---

## 5. Liệt kê hội thoại

```
GET /ai/conversations?user_id=user-42&limit=20&offset=0
```
```json
{
  "conversations": [
    { "conversation_id": "a397e7...", "title": "SQL injection là gì?", "created_at": 1798..., "updated_at": 1798..., "message_count": 4 }
  ],
  "total": 1, "limit": 20, "offset": 0
}
```
Mới cập nhật gần nhất xếp trước. `title` tự lấy từ tin nhắn đầu tiên.

---

## 6. Đọc transcript một hội thoại

```
GET /ai/conversations/{conversation_id}?user_id=user-42
```
```json
{
  "conversation_id": "a397e7...", "title": "SQL injection là gì?",
  "created_at": 1798..., "updated_at": 1798..., "message_count": 4,
  "messages": [
    { "role": "user", "content": "SQL injection là gì?", "seq": 1, "created_at": 1798... },
    { "role": "assistant", "content": "SQL injection là...", "seq": 2, "created_at": 1798... }
  ],
  "total": 4, "limit": 200, "offset": 0
}
```
Luôn đọc từ **bộ nhớ bền** — đầy đủ kể cả sau khi cửa sổ sống Redis (24h) đã hết hạn.

---

## 7. Tạo hội thoại rỗng (tuỳ chọn)

```
POST /ai/conversations
{ "user_id": "user-42", "title": "Câu hỏi về CVE-2026-xxxx" }
```
```json
{ "conversation_id": "b21f...", "title": "Câu hỏi về CVE-2026-xxxx", "created_at": 1798..., "updated_at": 1798..., "message_count": 0 }
```
Dùng khi cần nút "**+ Cuộc trò chuyện mới**" hiện ngay trước khi người dùng gõ gì. Có thể bỏ qua bước này — cứ gửi tin ở mục 3/4 mà không kèm `conversation_id`, server tự tạo.

---

## 8. Xoá hội thoại

```
DELETE /ai/conversations/{conversation_id}?user_id=user-42
```
```json
{ "deleted": true, "conversation_id": "a397e7..." }
```
Soft-delete — ẩn khỏi danh sách, không hiện lại.

---

## 9. Lỗi

| Mã | Khi nào | Body |
|---|---|---|
| `400` | Thiếu `user_id` ở endpoint quản lý (3, 5, 6, 8) | `{"detail": "user_id là bắt buộc để ..."}` |
| `404` | `conversation_id` không tồn tại **hoặc** không thuộc `user_id` này | `{"detail": "Không tìm thấy hội thoại."}` |
| `429` | Vượt rate limit | `{"detail": "rate limit exceeded"}` |
| `503` | Model tạm thời không phản hồi (endpoint 3/4 only) | `{"detail": "AI service unavailable: ..."}` |

`404` dùng cho cả "không tồn tại" lẫn "sai chủ" — để không lộ thông tin có hội thoại đó tồn tại hay không.

---

## 10. Luồng FE điển hình

```
Mở app  ──▶  GET /ai/conversations?user_id=       (danh sách bên trái)
              │
              ├─ chọn 1 cuộc  ──▶  GET /ai/conversations/{id}   (nạp transcript)
              │                     │
              │                     └─▶  POST /ai/conversations/chat/stream (kèm id)  (tiếp tục)
              │
              └─ "+ Cuộc mới"  ──▶  POST /ai/conversations/chat/stream (không kèm id)
                                     → lưu conversation_id từ frame đầu tiên
```

---

## Ghi chú vận hành

- Proxy SSE (nginx) phải tắt buffering: `proxy_buffering off;` + header `X-Accel-Buffering: no`.
- Ẩn danh (không `user_id`) vẫn chat được để tương thích ngược, nhưng **không lưu lịch sử**.
- Cấu hình: `SESSION_TTL_SECONDS` (cửa sổ sống Redis, mặc định 24h), `CHAT_HISTORY_REHYDRATE_TURNS` (số lượt nạp lại từ bộ nhớ bền khi cửa sổ sống đã hết hạn, mặc định 12, 0 = tắt).
