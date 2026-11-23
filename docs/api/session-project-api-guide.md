# API quản lý Project & Session

API cho FE dựng UI kiểu ChatGPT: **Project** (thư mục) chứa nhiều **Session** (cuộc trò chuyện); Session có thể đứng độc lập (standalone) hoặc thuộc 1 project.

`session_id`/`project_id` là chuỗi **mờ (opaque)** — không tự parse hay giả định định dạng.

---

## 1. Project

| Method | Path | Việc gì |
|---|---|---|
| POST | `/projects` | Tạo project |
| GET | `/projects?user_id=` | Liệt kê project của user |
| GET | `/projects/{pid}?user_id=` | Xem chi tiết 1 project |
| PATCH | `/projects/{pid}` | Đổi tên project |
| DELETE | `/projects/{pid}?user_id=` | Xoá project (cascade toàn bộ session con) |

**Request — tạo/đổi tên:**
```json
{ "user_id": "user-42", "name": "Đợt pentest Q3" }
```
`name` bắt buộc, không rỗng. `user_id` bỏ trống → mặc định `"anonymous"`.

**Response — `ProjectOut`:**
```json
{
  "project_id": "proj:8c2e...",
  "name": "Đợt pentest Q3",
  "owner": "user-42",
  "created_at": "2026-09-18T09:00:00+00:00",
  "updated_at": "2026-09-18T09:00:00+00:00"
}
```

`GET /projects` trả `ProjectOut[]`, sort theo `updated_at` giảm dần, không phân trang.

`DELETE` trả `{"deleted": <số session con đã xoá theo>}`. Session standalone không bị ảnh hưởng.

---

## 2. Session

| Method | Path | Việc gì |
|---|---|---|
| POST | `/sessions` | Tạo session standalone (không thuộc project) |
| GET | `/sessions?user_id=&scope=standalone\|all` | Liệt kê session của user (mặc định `scope=standalone`, thêm `all` để gồm cả session trong project) |
| PATCH | `/sessions/{sid}` | Đổi tên và/hoặc chuyển session vào/ra project |
| GET | `/sessions/{sid}` | Xem chi tiết 1 session |
| DELETE | `/sessions/{sid}` | Xoá session |
| GET | `/sessions/{sid}/messages` | Lấy lịch sử tin nhắn |
| POST | `/projects/{pid}/sessions` | Tạo session **thuộc project `pid`** |
| GET | `/projects/{pid}/sessions` | Liệt kê session thuộc project `pid` |

**Request — tạo:**
```json
{ "user_id": "user-42", "title": "Câu hỏi nhanh" }
```
Tạo qua `POST /sessions`: `title` tối đa 200 ký tự; nếu truyền `project_id` khác `null` → `400` (dùng `POST /projects/{pid}/sessions` thay thế).

**Request — đổi tên / di chuyển (`PATCH /sessions/{sid}`):**
```json
{ "user_id": "user-42", "title": "Tên mới", "project_id": "proj-xyz" }
```
- Không truyền `project_id` → giữ nguyên vị trí hiện tại.
- `project_id: null` → chuyển ra standalone.
- `project_id: "<pid>"` → chuyển vào project đó (phải tồn tại và đúng chủ, sai → `404`).

**Response — `SessionOut`:**
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

⚠️ **Danh sách sắp theo `last_activity` (mốc chat gần nhất), KHÔNG phải `updated_at`.** Đổi tên cập nhật `updated_at` nhưng không đẩy session lên đầu danh sách.

`GET /sessions/{sid}` trả shape đầy đủ hơn: `{"session_id", "meta", "created_at", "updated_at"}`.

`GET /sessions/{sid}/messages` trả:
```json
{ "messages": [{ "seq": 0, "appended_at": "...", "message": {...} }] }
```

Không phân trang trên mọi endpoint liệt kê.

---

## 3. Chat theo session

| Method | Path |
|---|---|
| POST | `/sessions/{sid}/chat` |
| POST | `/sessions/{sid}/chat/stream` (SSE) |

Chat trong session đã tạo ở trên. Mỗi lượt chat tự cập nhật `last_activity`, và tự đặt `title` từ ~60 ký tự đầu tin nhắn **nếu chưa có title**.

---

## 4. Quyền sở hữu & mã lỗi

Một `user_id` chỉ thấy/sửa được project/session của chính mình.

| Code | Khi nào |
|---|---|
| `400` | Thiếu/rỗng `name`/`title`, `title` > 200 ký tự, body rỗng khi PATCH, tạo standalone session kèm `project_id` khác null |
| `403` | `user_id` không phải chủ sở hữu (PATCH/DELETE/tạo con) |
| `404` | `pid`/`sid` không tồn tại; GET sai chủ cũng trả `404` (không tiết lộ sự tồn tại) |
| `422` | `scope` ở `GET /sessions` khác `standalone`/`all` |

---

## 5. Rate limit

- `/projects*`, `/sessions*` (CRUD): **120 request / 60s**
- `/sessions/{sid}/chat[/stream]`: **90 request / 60s**
