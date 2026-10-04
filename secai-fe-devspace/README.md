# secai-fe-devspace

Dev UI cho **SecAI Agent Gateway** — tập trung 3 API chính:

| Panel | Endpoint (stream / non-stream) |
|---|---|
| 💬 Chatbot | `POST /ai/chat/stream` · `/ai/chat` |
| 🛡️ PoC / Khai thác | `POST /ai/exploit/stream` · `/ai/exploit` |
| 🌐 Dịch | `POST /ai/translate/stream` · `/ai/translate` |

Stack: Vite + React + TypeScript. Render markdown + highlight code (react-markdown / rehype-highlight).

## Chạy

```bash
cp .env.example .env      # sửa GATEWAY_URL nếu cần (mặc định http://127.0.0.1:8131)
npm install
npm run dev               # http://localhost:5173
```

Gateway chạy trước đó, ví dụ:
```bash
# trong secai-agent-gateway
uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8131
```

## Kết nối & CORS

Gateway mặc định `allowed_origins` rỗng → FE **không gọi trực tiếp** mà đi qua **Vite dev proxy**
(`/ai`, `/health`, `/chat_no_stream` → `GATEWAY_URL`). Nhờ vậy không vướng CORS và SSE giữ nguyên
kết nối stream. Xem `vite.config.ts`.

Muốn gọi thẳng một base URL khác (bỏ proxy): đặt `VITE_API_BASE` trong `.env` (khi đó gateway phải
mở CORS cho origin của FE).

## Xử lý SSE

Client parse frame `event: <name>\ndata: <json>` (xem `src/api.ts`), map các sự kiện:

- `token` → nối vào câu trả lời
- `tool_call` / `tool_result` → hiển thị hoạt động tool (poc_search / poc_fetch / qdrant…)
- `regenerate` → **xoá buffer** đang stream (hook ép model trả lời lại; nếu không reset sẽ dính bản nháp + bản sửa)
- `error` → hiển thị lỗi
- `final` → câu trả lời chính thức + `metadata`

## Cấu trúc

```
src/
  api.ts              # client: post + stream(SSE), health
  types.ts            # contract khớp routes/sse/schemas.py
  App.tsx             # layout: sidebar + topbar (health, user_id, streaming) + panel
  components/
    ChatPanel.tsx     # hội thoại nhiều lượt, session theo user_id
    PocPanel.tsx      # biểu mẫu (vulnerability_type/target_system/context) hoặc free text + metadata
    TranslatePanel.tsx# chọn ngôn ngữ nguồn/đích, đảo chiều
    Markdown.tsx      # render markdown + highlight
    ToolActivity.tsx  # hiển thị tool call/result
```

## Build

```bash
npm run build         # tsc -b && vite build → dist/
npm run preview
```
