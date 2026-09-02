# Chat History API — hướng dẫn tích hợp FE (multi-conversation)

> Bộ API **mới** `/ai/conversations/*` cho phép nhiều cuộc trò chuyện mỗi người dùng: tạo /
> liệt kê / mở lại / xoá, kèm lịch sử bền. **API cũ `/ai/chat`, `/chat_no_stream`,
> `/ai/chat/stream` KHÔNG đổi** (hệ thống Nhật đang dùng) — có thể tiếp tục dùng song song.

## Mô hình

- Mỗi cuộc trò chuyện có một `conversation_id` (uuid, do server cấp). FE giữ id này để gửi
  tiếp / mở lại / xoá.
- **Bộ nhớ sống** (ngữ cảnh agent suy luận, tự nén khi đầy) nằm ở Redis, cửa sổ 24h. **Bản
  bền** (index + transcript đầy đủ để liệt kê/đọc lại) nằm ở SQLite, **không hết hạn**. Đọc
  lại lịch sử luôn lấy từ bản bền → còn nguyên kể cả sau khi cửa sổ sống 24h trôi qua.
- **Quyền sở hữu bằng `user_id`**: `owner_hash = sha256(user_id)` được gấp vào session id nên
  không user nào truy cập được hội thoại của user khác. Vì vậy các endpoint quản lý **bắt buộc
  `user_id`** (thiếu → `400`). Truy cập nhầm chủ → `404` (không xác nhận tồn tại).

## Endpoints

### 1) Gửi tin nhắn (non-stream) — `POST /ai/conversations/chat`

Body: `{ "user_id": "u123", "conversation_id"?: "<uuid>", "message": "..." }`
(alias `message` như cũ: `question`/`prompt`/`content`/`text`/`messages[]`.)
Thiếu `conversation_id` → server tạo cuộc mới và trả id trong kết quả.

Response (**superset** của `/ai/chat` cũ — thêm đúng `conversation_id`):
```json
{
  "response": "…", "answer": "…", "message": "…",
  "metadata": { "run_id": "…", "profile": "chat", "tool_registry": [...], "conversation_id": "<uuid>" },
  "conversation_id": "<uuid>"
}
```

### 2) Gửi tin nhắn (stream SSE) — `POST /ai/conversations/chat/stream`

Body giống trên. Dùng `fetch` + `ReadableStream` (KHÔNG `EventSource`, vì là POST); tách frame
theo `\n\n`, đọc dòng `event:` và `data:`. Chuỗi frame:

| `event:` | `data` | FE xử lý |
|---|---|---|
| `conversation` | `{"conversation_id"}` | **frame đầu tiên** (mới) — lưu id ngay để hiển thị/gửi tiếp |
| `token` | `{"delta"}` | nối vào bong bóng trả lời (typewriter) |
| `tool_call` / `tool_result` | như cũ | chip "đang tra cứu…" |
| `regenerate` | `{"hook","reason"}` | **xoá sạch buffer token** rồi nhận lại từ đầu |
| `compaction` | `{"strategy",...}` | (tuỳ chọn) hội thoại dài vừa được nén — chỉ để hiển thị/log |
| `error` | `{"error"}` | báo lỗi (vẫn theo sau bởi token+final) |
| `final` | `{"content","answer","message","metadata","conversation_id"}` | **nguồn chân lý** — thay toàn bộ text đã render bằng `final.content` |
| *(none)* | `data: [DONE]` | đóng reader |

`final` giữ nguyên khung cũ + thêm `conversation_id` (top-level và trong `metadata`).

### 3) Liệt kê hội thoại — `GET /ai/conversations?user_id=&limit=20&offset=0`

```json
{ "conversations": [ {"conversation_id","title","created_at","updated_at","message_count"} ],
  "total": 12, "limit": 20, "offset": 0 }
```
Sắp xếp mới-cập-nhật-trước; chỉ trả hội thoại của chính `user_id`. Thiếu `user_id` → `400`.

### 4) Đọc transcript — `GET /ai/conversations/{conversation_id}?user_id=&limit=&offset=`

```json
{ "conversation_id":"…","title":"…","created_at":…,"updated_at":…,"message_count":8,
  "total":8,"limit":200,"offset":0,
  "messages": [ {"role":"user|assistant","content":"…","seq":1,"created_at":…} ] }
```
Từ bản **bền SQLite** (đọc lại được lâu dài). Không đúng chủ → `404`.

### 5) Tạo cuộc mới (rỗng) — `POST /ai/conversations`

Body `{ "user_id":"u123", "title"?:"Tên hiển thị" }` → `{conversation_id, title, created_at, updated_at, message_count:0}`.
Dùng khi FE muốn nút "Cuộc trò chuyện mới" trước khi người dùng gõ. (Cũng có thể bỏ qua và để
`/ai/conversations/chat` tự mint khi gửi tin đầu.) Thiếu `user_id` → `400`.

### 6) Xoá — `DELETE /ai/conversations/{conversation_id}?user_id=`

→ `{ "deleted": true, "conversation_id": "…" }`. Soft-delete (ẩn khỏi danh sách) + xoá session
sống. Không đúng chủ → `404`.

## Luồng FE điển hình (chuyển cũ ↔ mới)

1. Mở app → `GET /ai/conversations?user_id=` để dựng danh sách bên trái.
2. Chọn một cuộc → `GET /ai/conversations/{cid}` để nạp transcript.
3. Gửi tin → `POST /ai/conversations/chat/stream` với `conversation_id` đang mở; frame
   `conversation` xác nhận id, `final.content` là câu trả lời chốt.
4. "Cuộc mới" → gửi không kèm `conversation_id` (hoặc gọi `POST /ai/conversations` trước); lưu
   `conversation_id` trả về.
5. Xoá → `DELETE /ai/conversations/{cid}`.

## Ghi chú vận hành

- nginx proxy phải tắt buffering cho SSE (`proxy_buffering off;` + `X-Accel-Buffering: no`),
  giống guide `/ai/exploit/stream`.
- Cửa sổ sống Redis 24h cấu hình qua `SESSION_TTL_SECONDS`; khôi phục ngữ cảnh sau khi hết hạn
  qua `CHAT_HISTORY_REHYDRATE_TURNS` (mặc định 12, 0 = tắt). Transcript hiển thị không phụ thuộc
  các giá trị này (luôn từ SQLite bền).
- Người dùng ẩn danh (không `user_id`) vẫn gọi được `/ai/conversations/chat` (parity với
  `/ai/chat`) nhưng KHÔNG được lập chỉ mục → không liệt kê/đọc lại được.
