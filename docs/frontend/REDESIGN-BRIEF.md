# SecAI FE — Design Brief để thiết kế lại (map 1:1 với gateway hiện tại)

> Mục tiêu: một UI **tối giản, smart** cho `secai-agent-gateway`, lấy **Project → Session**
> làm xương sống, mọi chức năng map đúng endpoint thật. Tài liệu này là spec để bạn dựng lại
> giao diện (Figma/HTML), không phải code.
>
> Backend base: `http://127.0.0.1:8131` (FE gọi qua dev-proxy để tránh CORS; SSE giữ nguyên stream).
> Nhận diện người dùng: mọi request mang `user_id` (owner-scoping). Chưa có auth thật → coi `user_id`
> như danh tính phiên làm việc.

---

## 1. Triết lý thiết kế (tối giản · smart)

1. **Một khung làm việc, không phải 4 app rời.** Hiện tại FE chia 4 panel tách biệt (Chat/PoC/Dịch/Bughunt).
   Thiết kế mới: **một workspace hội thoại duy nhất**; "chế độ" (Chat / PoC / Dịch / Báo cáo) là một
   **switch nhẹ** trong ô nhập, không phải màn hình riêng — vì session giữ ngữ cảnh xuyên chế độ.
2. **Xương sống = Project → Session.** Rail trái điều hướng; nội dung là dòng hội thoại của session đang mở.
3. **Streaming-first.** Token chảy realtime; hoạt động công cụ (tool_call/result) là **timeline gấp gọn**;
   các pha (phase) là **chip tiến trình**. Người dùng thấy "đang làm gì" mà không bị ngợp.
4. **Progressive disclosure.** Tham số nâng cao (exploit-advance, bughunt, tool/exec) nằm sau expander;
   mặc định chỉ hiện 1 hành động chính mỗi màn.
5. **An toàn hiển thị.** Backend đã defang XSS + chặn secret (422) + từ chối ngoài phạm vi; FE vẫn phải
   DOMPurify khi render Markdown và hiển thị các trạng thái an toàn một cách rõ ràng, không "nuốt" lỗi.
6. **Tách 2 tầng người dùng:** *end-user* (Chat/PoC/Dịch/Báo cáo) vs *operator* (Bughunt, Exploit-advance,
   Tool/Exec/Verify, Admin). Tầng operator gated sau một khu "Nâng cao/Vận hành".

---

## 2. Bản đồ Năng lực → Endpoint → Màn hình (map thật)

| # | Năng lực | Endpoint (JSON · SSE) | Body chính | Màn hình / thành phần |
|---|---|---|---|---|
| A | **Chat theo session** (mới, chính) | `POST /sessions/{sid}/chat` · `/sessions/{sid}/chat/stream` | `{message\|question\|prompt, user_id}` | Workspace hội thoại (mặc định) |
| A' | Chat theo user (legacy) | `POST /ai/chat` · `/ai/chat/stream` · `/chat_no_stream` | `{message, user_id}` | Ẩn — chỉ dùng khi không chọn session |
| B | **Quản lý Project** | `POST/GET /projects` · `GET/PATCH/DELETE /projects/{pid}` | `{name, user_id}` | Rail trái (danh sách) + modal tạo/sửa |
| B | **Quản lý Session** | `POST/GET /projects/{pid}/sessions` · `GET/DELETE /sessions/{sid}` · `GET /sessions/{sid}/messages` | `{user_id, title?}` | Rail trái (session trong project) + nạp lịch sử |
| C | **Nghiên cứu PoC / Khai thác** (read-only) | `POST /ai/exploit` · `/ai/exploit/stream` | `{message/prompt hoặc cve/target/ref, user_id}` | Chế độ "PoC" trong workspace + panel Section-1 |
| D | **Khai thác nâng cao target thật** | `GET /ai/exploit-advance/fields` → form; `POST /ai/exploit-advance/stream` · `/ai/exploit-advance` · `/ai/exploit-advance/reply` | xem §6 (form động) | Khu "Nâng cao" — form + timeline ReAct + hỏi-đáp |
| E | **Dịch EN⇄VI bảo mật** | `POST /ai/translate` · `/ai/translate/stream` | `{text/message, language, user_id}` | Chế độ "Dịch" (2 cột nguồn/đích) |
| F | **Báo cáo từ log/dữ liệu** | `POST /ai/generate-report` · `/stream` | `{data, ...}` | Chế độ "Báo cáo" (input lớn → Markdown) |
| F | **Báo cáo thống kê kỳ** | `POST /ai/generate-statistical-report` · `/stream` | `{period...}` | Tab con của Báo cáo → `report_title/summary/detail` |
| G | **Bug hunter (ReAct)** | `POST /ai/bughunt/stream` | `{target, scope_note, enable_exploit, run_cve_sweep, user_id}` | Khu "Nâng cao" — timeline pha recon→hunt→report |
| H | Verify PoC | `POST /ai/verify` | `{target, cve, ref, source, rport, user_id}` | Operator — hành động phụ trong PoC |
| H | Chạy exploit đơn | `POST /ai/exploit/run` | `{target, cve, port, command, use_proxy, user_id}` | Operator — advanced |
| H | Gọi công cụ đơn | `POST /ai/tool` | `{tool, target, ports, query, wordlist, threads, service, username, password}` | Operator — bảng công cụ |
| H | Thực thi lệnh | `POST /ai/exec` | `{cmd, shell, timeout_s, privileged}` | Operator — **nguy hiểm**, gated (exec_enabled + admin) |
| I | **Trạng thái hệ thống** | `GET /health` | — | Chip status ở header |
| I | Refresh models (admin) | `POST /admin/refresh-models` (`X-API-Key`) | — | Settings → Admin |

> Ghi chú map: Chat/PoC/Translate/Report/Statistical/Bughunt/Exploit-advance đều có cặp **JSON + `/stream`**.
> Mặc định dùng bản `/stream` (SSE) cho trải nghiệm realtime; bản JSON để copy/tải hoặc chế độ chậm.

---

## 3. Kiến trúc thông tin (IA) & layout tối giản

```
┌──────────────────────────────────────────────────────────────────────┐
│ Header:  ◱ SecAI   [user_id ▾]        ● LLM ● RAG ● Store   ⌘K  ⚙︎     │  ← status từ /health
├───────────────┬──────────────────────────────────────────────────────┤
│ RAIL TRÁI     │  WORKSPACE (session đang mở)                          │
│               │                                                       │
│ + Dự án       │  ┌─ Thread hội thoại ────────────────────────────┐   │
│ ▾ Dự án A     │  │  · tin nhắn user / trợ lý (Markdown, defang)   │   │
│    • Phiên 1  │  │  · [Tool timeline gấp gọn]  · [phase chips]    │   │
│    • Phiên 2  │  │  · [Section-1 PoC panel] khi ở chế độ PoC      │   │
│ ▸ Dự án B     │  └───────────────────────────────────────────────┘   │
│               │  ┌─ Composer ────────────────────────────────────┐   │
│ ───────────   │  │ [Chat|PoC|Dịch|Báo cáo ▾]  ô nhập…      ⏎ Gửi │   │
│ ⚙ Nâng cao    │  └───────────────────────────────────────────────┘   │
│   (operator)  │                                                       │
└───────────────┴──────────────────────────────────────────────────────┘
```

- **Rail trái**: cây Project → Session. Tạo project (`POST /projects`), tạo session (`POST /projects/{pid}/sessions`),
  đổi tên (`PATCH`), xoá (`DELETE`, cascade). Chọn session → `GET /sessions/{sid}/messages` nạp lịch sử.
- **Workspace**: một thread duy nhất của session đang chọn. Composer có **mode switch** (Chat/PoC/Dịch/Báo cáo).
- **Khu "Nâng cao/Vận hành"** (thu gọn cuối rail): Bughunt, Exploit-advance, Tool/Exec/Verify — tách khỏi luồng chính.
- **Header**: chip trạng thái từ `/health` (llm/qwen, redis/session_backend, searchsploit); switch `user_id`; ⌘K command palette; ⚙︎ settings.

---

## 4. Mô hình dữ liệu phía FE

```ts
Project  { project_id, name, owner, created_at, updated_at }        // ISO-8601
Session  { session_id, title, project_id, created_at, updated_at }  // meta.kind="session"
Message  { seq, appended_at, role, content }                        // từ /sessions/{sid}/messages
```
- `user_id` là owner; FE chỉ liệt kê project/session của owner đang chọn.
- Session id do backend cấp dạng `proj:{pid}:sess:{uuid}` — FE coi là opaque, luôn truyền nguyên văn.
- Khi mở lại session cũ: **nạp toàn bộ lịch sử** qua `GET /sessions/{sid}/messages` (backend đã bền, không TTL 24h;
  ngữ cảnh dài được backend tự nén — FE không cần lo cắt).

---

## 5. Giao thức streaming (SSE) → cách render

FE parse frame `event: <name>\ndata: <json>`, kết thúc bằng `data: [DONE]`. Map sự kiện (theo `api/sse.py`):

| event | payload | Render UI |
|---|---|---|
| `token` | `{delta}` | Nối vào bong bóng trợ lý (typing realtime) |
| `tool_call` | `{tool, args…}` | Thêm dòng vào **Tool timeline** (chip "đang tra qdrant/neo4j/poc…") |
| `tool_result` | `{…}` | Cập nhật dòng timeline (gấp gọn, click để xem chi tiết) |
| `phase` | `{phase, …}` | **Chip pha** (recon/hunt/exploit/verify…) cho bughunt & exploit-advance |
| `regenerate` | `{hook, reason}` | Badge nhỏ "đang bổ sung dữ liệu…" (hook bắt tra thêm) |
| `error` | `{error}` | Banner lỗi trong thread (không nuốt) |
| `final` | `{content/answer, message, metadata}` | Chốt câu trả lời; hiện `metadata.run_id`, tool_registry |
| `[DONE]` | — | Đóng stream, mở lại composer |
| *(exploit)* `section1` | PoC đã fetch (đầy đủ) | **Panel Section-1**: cây repo + mã nguồn (đã defang) |
| *(exploit-advance)* `question` | `{run_id, …}` | **Hỏi-đáp inline** (human-in-the-loop) → gọi `/ai/exploit-advance/reply` |

Nguyên tắc: **một bong bóng trợ lý = token stream**; **tool/phase = dải phụ gấp gọn** bên dưới, không lẫn vào câu trả lời.

---

## 6. Màn hình chi tiết theo chức năng

### 6.1 Chat (mặc định)
- Composer mode "Chat". Gửi `{message, user_id}` tới `/sessions/{sid}/chat/stream`.
- Nếu chưa chọn session: hiện empty-state "Chọn hoặc tạo phiên" (hoặc tạo nhanh 1 session mặc định).
- Câu hỏi về CVE/CWE/PoC → backend tự gọi công cụ (thấy qua tool timeline). Câu về hệ thống → trả lời thẳng.

### 6.2 PoC / Nghiên cứu khai thác
- Composer mode "PoC" → `/ai/exploit/stream`. Có thể nhập tự do hoặc form nhẹ `{cve, target?, ref?}`.
- Panel **Section-1** hiển thị PoC được fetch (đầy đủ, cây file + code). Nhấn mạnh: **read-only, không thực thi**
  (backend chỉ trả `draft_not_executed`). UI phải nói rõ điều này.

### 6.3 Khai thác nâng cao (operator, target thật)
- **Form động**: gọi `GET /ai/exploit-advance/fields` → dựng form theo `name/type/required/default/description`.
  Trường: `cve, target, port, command(=id), auth{headers/cookies/token}, scope_note, max_steps(1-20),
  step_timeout_s, total_timeout_s, use_proxy, extra, run_id`.
- Chạy `/ai/exploit-advance/stream`: hiển thị **timeline ReAct** (phase + tool_call/result), vòng exploit→verify→sửa.
- **Human-in-the-loop**: khi có event `question` → hiện ô trả lời inline → `POST /ai/exploit-advance/reply {run_id, answer}`.
- Bắt buộc **scope_note / xác nhận uỷ quyền** trước khi chạy (đây là khai thác target thật).

### 6.4 Dịch
- Mode "Dịch" → `/ai/translate/stream`. Layout 2 cột nguồn/đích; chọn `language` (EN⇄VI). Giữ verbatim tài khoản/mật khẩu (backend đã masking).

### 6.5 Báo cáo & Báo cáo thống kê
- Mode "Báo cáo" → ô nhập lớn `{data}` → `/ai/generate-report/stream` → render Markdown.
- Tab con "Thống kê kỳ" → `/ai/generate-statistical-report/stream` → hiển thị `report_title / summary / detail`.

### 6.6 Bug hunter (operator)
- Form `{target, scope_note, enable_exploit(false), run_cve_sweep(true)}` → `/ai/bughunt/stream`.
- Timeline pha: recon → hunt → triage → map-CVE → report → (exploit opt-in). Rất nhiều `phase` → chip tiến trình là chính.

### 6.7 Vận hành/Công cụ (operator, ẩn sâu)
- `POST /ai/verify`, `/ai/exploit/run`, `/ai/tool`, `/ai/exec` — bảng công cụ có form riêng.
- `/ai/exec` **nguy hiểm**: chỉ hiện khi backend bật (`exec_enabled`) + có `X-API-Key`; luôn confirm.

### 6.8 Trạng thái & Cài đặt
- Chip `/health`: `llm/qwen` (model), `redis/session_backend` (bộ nhớ), `searchsploit`, `input_sanitize`.
- Settings: đổi `user_id`, `GATEWAY_URL`/`VITE_API_BASE`, `X-API-Key` (admin) → `POST /admin/refresh-models`.

---

## 7. Trạng thái & UX an toàn (bắt buộc thể hiện)

- **Empty**: chưa có project/session/tin nhắn → gợi ý hành động (tạo project, hỏi mẫu).
- **Streaming**: disable gửi, hiện token + tool timeline; nút "Dừng" hủy stream.
- **Lỗi 422 (secret/PII)**: banner "Đầu vào chứa bí mật/PII — đã chặn để bảo vệ bạn." (không gửi tới model).
- **Từ chối ngoài phạm vi**: render câu từ chối như nội dung bình thường, mời quay lại chủ đề ATTT.
- **Defang XSS**: backend đã vô hiệu hoá active content; FE **vẫn** DOMPurify + chặn link `javascript:` khi render Markdown/code.
- **Owner mismatch**: 403/404 → thông báo "Không có quyền / không tồn tại" (không lộ tồn tại).

---

## 8. Hệ component tối giản (inventory)

`ProjectTree` (rail) · `SessionItem` · `Thread` · `MessageBubble(role)` · `ModeSwitch` · `Composer`
· `ToolTimeline` + `ToolRow` · `PhaseChips` · `Section1PocPanel(FileTree+CodeBlock)` · `QuestionInline`
(human-in-the-loop) · `DynamicForm` (từ `/ai/exploit-advance/fields`) · `StatusChips` (health) · `Markdown`
(DOMPurify + highlight) · `Toast/Banner` (422/error/refusal) · `CommandPalette (⌘K)` · `SettingsDrawer`.

---

## 9. Nguyên tắc "smart" cụ thể

- **⌘K command palette**: "Tạo project", "Chuyển sang PoC", "Dịch đoạn này", "Bật Bughunt"… — giảm nút bấm.
- **Mode nhớ theo session**: mở lại session giữ nguyên chế độ + ngữ cảnh (backend đã nhớ lịch sử).
- **Tool timeline mặc định gấp**: chỉ bung khi cần soi — giữ câu trả lời sạch (khớp `output_hygiene` của backend).
- **1 hành động chính / màn**: nút Gửi là chính; nâng cao ẩn sau expander/khu operator.
- **Tôn trọng phân tầng**: end-user không bao giờ vô tình thấy `/ai/exec`.
- **Không chặn khi backend chậm**: exploit-advance/bughunt chạy phút → timeline + cho phép rời đi, quay lại xem tiếp.

---

## 10. Gợi ý hệ hình ảnh (design-tool-agnostic)

- Tối giản, tương phản cao, dark-mode-first (bối cảnh bảo mật). Màu ngữ nghĩa: xanh=OK, vàng=cảnh báo/thiếu dữ liệu,
  đỏ=nguy hiểm/exec, tím=operator. Mã định danh (CVE/CVSS) in đậm; code dùng monospace + highlight.
- Mật độ vừa phải, nhiều khoảng trắng; timeline/chip nhỏ, không "hò hét". Icon 1 nét.
- Responsive: rail collapse ở màn hẹp; composer luôn cố định đáy.

---

### Phụ lục — Việc FE cần thêm để chạy được thiết kế này
1. Thread `session_id`/`project_id` vào `api.ts` (hiện chỉ gửi `user_id`).
2. `ProjectTree` + gọi 6 endpoint CRUD project/session.
3. `DynamicForm` đọc `/ai/exploit-advance/fields`; `QuestionInline` cho `/reply`.
4. Xử lý thêm event `phase`/`regenerate`/`question`/`section1` trong bộ dispatch SSE.
5. Rebuild `dist/` (bản đang phục vụ cũ 13/08) hoặc chạy `npm run dev`.
