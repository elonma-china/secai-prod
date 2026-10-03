# Hướng dẫn làm việc với secai-agent-gateway

## Phạm vi và kiến trúc

- File này áp dụng cho thư mục dự án và các thư mục con. Lệnh bên dưới chạy từ thư mục chứa file này, trừ khi ghi khác.
- Gateway IAS dùng Python >= 3.10, FastAPI, Pydantic v2 và Linch; cung cấp chat, dịch, báo cáo, nghiên cứu PoC và SSE.
- Gateway chạy trên CPU; LLM, embedding và reranker được gọi qua HTTP. Qdrant và Neo4j phục vụ RAG; SQLite/Redis lưu session.
- Git root có thể nằm ở thư mục cha và chứa nhiều dự án. Trước khi sửa, kiểm tra `git rev-parse --show-toplevel`, `git status --short --branch` và `git worktree list`.

## Bản đồ mã nguồn

- `src/secai_gateway/app.py`: khởi tạo ứng dụng, runner và lifespan.
- `src/secai_gateway/config.py`: nguồn chuẩn cho cấu hình, biến môi trường và mặc định.
- `src/secai_gateway/api/`: schema, routes, project/session, dựng prompt, middleware, SSE và showcase API.
- `src/secai_gateway/agent/`: Linch runner, provider, profile và hook điều phối tool/grounding.
- `src/secai_gateway/prompts/`: prompt theo tác vụ và `profiles.yaml`.
- `src/secai_gateway/retrieval/`: Qdrant, BM25, embedding, reranker và Neo4j.
- `src/secai_gateway/poc/`, `bughunt/`: catalog/artifact PoC, prefetch, xác minh và workflow khai thác.
- `src/secai_gateway/security.py`, `sanitize/`: redaction, lọc nội dung đầu vào/đầu ra và classifier.
- `src/secai_gateway/state/`: session store, ledger và rate limiter.
- `secai-fe-devspace/`: frontend React/TypeScript/Vite khi có trong checkout; đọc `package.json` và `src/api.ts` trước khi sửa contract.
- `demo/`: giao diện showcase và KPI Studio; `kpi_bench/`, `evals/`: benchmark và đánh giá.
- `tests/`: pytest; `docs/`: tài liệu API và bằng chứng nghiệm thu; `deploy/`: cấu hình triển khai.

## Cài đặt và chạy local

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8080
```

- Cấu hình qua `.env`; tra tên biến trong `config.py`. Chỉ tạo từ `.env.example` nếu file đó tồn tại; giữ nguyên cấu hình đang có.
- Local API docs: `http://127.0.0.1:8080/docs`; health: `/health`.
- `pyproject.toml` đang pin `linch>=1.2.1,<2` vì thay đổi contract hook ở Linch 2. Không bỏ pin nếu chưa thực hiện migration và kiểm chứng.

## Quy tắc thay đổi

- Sửa nhỏ, đúng nguyên nhân; tái sử dụng helper hiện có trước khi thêm abstraction hoặc dependency.
- Giữ logic I/O bất đồng bộ; không thêm thao tác chặn event loop vào request path.
- Giữ tương thích endpoint IAS, tên field JSON và sự kiện SSE. Khi sửa contract, kiểm tra cả backend, frontend, test và tài liệu API liên quan.
- Với chat, kiểm tra cả `/ai/chat` lẫn `/sessions/{sid}/chat`, bản JSON và stream; giữ quyền sở hữu project/session theo `user_id`.
- Giữ nhất quán câu trả lời stream, `final` và lịch sử lưu; không persist prompt nội bộ, context grounding hay bản nháp retry vào lịch sử hiển thị.
- Báo cáo thống kê có `detail` dạng mảng được dựng bảng bằng code trong `api/messages.py`; giữ tương thích với `detail` dạng chuỗi cũ.
- Không bỏ redaction, anti-XSS, admin API key, allowlist target, air-gap hoặc cổng bật/tắt thực thi để làm test pass. `/ai/exploit` phải phân biệt rõ artifact thật và nội dung tổng hợp, trạng thái chưa chạy và đã xác minh.
- Đọc `CLAUDE.md` khi làm serving: dùng đúng model fine-tuned `DuyTa/sec-embedding` và `DuyTa/sec-rerank`; tên model request phải khớp serving. `/health` đơn lẻ không chứng minh embedding/reranker hoạt động.

## Kiểm chứng

```sh
# Test liên quan trước, rồi toàn bộ suite khi thay đổi hành vi backend.
.venv/bin/python -m pytest -q tests/test_projects_sessions.py
.venv/bin/python -m pytest -q
git diff --check

# Khi sửa frontend, chạy trong thư mục frontend tương ứng.
cd secai-fe-devspace
npm ci
npm run build
```

- `tests/conftest.py` dùng session memory, tắt Redis và tránh classifier gọi embedding thật; AIRGAP được đặt false cho test các tính năng mạng. Mock mọi dịch vụ ngoài mà test sử dụng, không giả định fixture chặn tất cả kết nối.
- Test Bughunt có thể cần cây `claude-bug-bounty` bên ngoài checkout. Báo rõ dependency thiếu và tên test fail/skip; không coi suite là pass nếu chưa chạy xong.
- Live eval/KPI cần dịch vụ, dữ liệu và có thể tốn chi phí. Chỉ chạy khi nằm trong phạm vi tác vụ; không dùng thay cho unit test.
- Sửa prompt/hook cần test hồi quy phù hợp; số liệu chất lượng phải ghi rõ dataset, cấu hình/model và phương pháp đo.
- Thay đổi chỉ tài liệu: kiểm tra nội dung, đường dẫn/lệnh và diff; không cần chạy lại suite backend.

## Git, dữ liệu và triển khai

- Không revert thay đổi của người dùng/agent khác. Khi thấy file biến mất hoặc thay đổi ngoài phạm vi, kiểm tra và báo lại trước khi ghi đè.
- Stage đường dẫn cụ thể; tránh `git add .` hoặc `git add -A` ở Git root có nhiều dự án.
- Không commit `.env`, khóa/token, dữ liệu session, log, backup, cache, build output hay kết quả eval phát sinh, trừ artifact đã được yêu cầu rõ và kiểm tra nội dung.
- Giữ phạm vi commit/merge theo yêu cầu hiện tại. Khi được yêu cầu merge: cập nhật remote, kiểm tra worktree đích, kiểm tra conflict và test kết quả merge; không force-push hoặc reset lịch sử để xử lý lỗi.
- Đọc `deploy/README.md` trước thao tác Docker. Stack ở `deploy/` có thể dùng chung tên container/volume với stack đang chạy; `up`, `down`, `restart` có thể tác động production. Chỉ thực hiện khi tác vụ đã bao gồm triển khai.
- Không đổi dataset/bằng chứng nghiệm thu hoặc số liệu benchmark để hợp thức hóa kết quả.

## Khi có phiên nodeterm

- Nếu có `NODETERM_NODE_ID`, có thể đọc context liên kết bằng `sh "/home/ict/.nodeterm-server/context-links/context.sh" list`, rồi `summary --node <id>` khi cần.
- Chỉ mở/quản lý node hoặc phân công agent trên canvas khi tác vụ yêu cầu; làm theo hướng dẫn nodeterm của phiên hiện tại.
- Nếu CLI báo không phải phiên nodeterm hoặc không có node liên kết, không thử lại.

## Báo cáo kết quả

- Mặc định trao đổi bằng tiếng Việt, ngắn và cụ thể: đã đổi gì, kiểm chứng nào đã chạy, lỗi hoặc phần chưa hoàn tất.
- Chỉ khẳng định commit, push, merge, build hoặc test thành công khi có kết quả lệnh xác nhận.
