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

## Báo cáo theo mẫu (MVP)

Tab **Nền tảng AI → Báo cáo thông minh → Theo mẫu** được ẩn mặc định. Khi backend đã bật
`REPORT_FEATURE_ENABLED` và cấu hình `REPORT_API_KEY`, chạy FE với:

```bash
VITE_REPORT_FEATURE_ENABLED=true npm run dev
```

Tab mới gọi riêng `/ai/v2/reports`; hai tab báo cáo cũ vẫn dùng API cũ. Người dùng nhập
khóa báo cáo vào ô mật khẩu trên tab. Khóa chỉ giữ trong React state cho đến khi tải lại
trang, không đặt trong `VITE_*`, URL hoặc localStorage. Đây là khóa chung của workspace,
không phải tài khoản hay quyền sở hữu riêng của từng người.

Tạo mẫu bằng form hoặc nhập `.docx`, `.md`, `.txt` tối đa 2 MiB. File nhập chỉ điền bản
nháp để người dùng soát/sửa trước khi lưu. Có thể chọn mẫu đã lưu, sửa thành phiên bản
mới, sinh báo cáo từ dữ liệu nguồn, mở báo cáo bằng ID và tạo bản sửa bằng prompt.
UI hiển thị lịch sử bản sửa; backend kiểm tra phiên bản khi lưu/chỉnh để tránh ghi đè
thay đổi đồng thời.

## Dịch nhiều tệp/ảnh (translation v2)

Tab dịch tệp dùng API job v2 riêng, không phụ thuộc khả năng dịch văn bản v3. Đặt
`VITE_TRANSLATE_V2_FILES_ENABLED=true` để hiện tab; thêm
`VITE_TRANSLATE_V2_IMAGES_ENABLED=true` để chọn PNG/JPG/JPEG. Hai cờ FE mặc định tắt;
gateway cũng cần `TRANSLATE_V2_ENABLED=true`, và ảnh cần OCR v2 hoạt động.
Đây là cờ build Vite, nên đổi giá trị cần build lại frontend.

Mỗi lần chọn tối đa 20 tệp TXT/DOCX/DOC hoặc ảnh, từng tệp tối đa 10 MiB.
Giao diện giữ tối đa hai job chưa kết thúc, gửi cách nhau ít nhất 12,5 giây,
poll trạng thái cách nhau ít nhất 4 giây/job, và tuân theo `Retry-After`.
ID job và khóa idempotency là bí mật truy cập; chỉ metadata/hàm băm tệp được giữ
trong `sessionStorage` của tab, không lưu nội dung tệp hay bản dịch. Sau khi tải lại
trang, job đã nhận được tiếp tục kiểm tra; tệp chưa gửi hoặc POST chưa rõ kết quả
cần chọn lại đúng tệp để phát lại cùng khóa. Giao diện không thể hủy một POST
chưa rõ máy chủ đã nhận cho tới khi khôi phục được ID job. Kết quả có thể tải
dưới dạng TXT hoặc JSON; JSON giữ các trường OCR và cảnh báo từ API.

## Demo hai vai trò và phản hồi A/B

Bản demo dùng bộ chuyển **Demo → Người dùng thường / Admin** trong header. User không có tab A/B quản trị; chọn **Thử đánh giá A/B** để gửi phản hồi từ ví dụ QA được phân công. Admin mở tab A/B, xem phản hồi và phê duyệt hoặc loại mẫu. Đây là mô phỏng bằng credential chung cho từng vai trò, không phải đăng nhập production. Nút huấn luyện bị khóa vì chưa có API job fine-tune.

Chức năng đăng nhập demo cần **static proxy `serve_static.py`**, không chạy qua `npm run dev` hoặc `npm run preview` trực tiếp. Vite chỉ chuyển tiếp `X-Review-Role`; gateway yêu cầu Bearer và không tự coi header này là quyền truy cập. Static proxy ánh xạ role sang credential riêng theo chính sách kết nối cho phép.

Sau khi quản trị đã cấu hình credential reviewer/admin trong `.review-devspace-tokens.local`, cấu hình quyền kết nối local/tailnet của proxy và gateway review đang chạy:

```bash
npm run build
FE_HOST=127.0.0.1 FE_PORT=5173 python3 serve_static.py
```

Không chạy thêm server nếu cổng đang được sử dụng. Không công khai file credential hoặc tắt Bearer/kiểm tra quyền để demo hoạt động. Luồng kiểm thử và ảnh: [báo cáo demo hai vai trò](../docs/test-results/devspace-ab-two-roles-20261006.md).
