# A/B canary harness (cô lập)

Dựng gateway + static server Devspace riêng để chạy E2E "Đánh giá A/B" mà không đụng prod. Mọi thứ ở `/tmp/ab-canary/` (ngoài Git).

| Thành phần | Địa chỉ | Ghi chú |
|---|---|---|
| Gateway (`secai_gateway.unified_app:app`, `.venv`) | `127.0.0.1:18261` | `env -i`, cwd rỗng (không đọc `.env` repo), AIRGAP=true, `REVIEW_ENABLED=true` |
| Static + proxy (`serve_static.py` bản copy) | `127.0.0.1:18262` | `/tmp/ab-canary/fe/`, trỏ gateway canary; `DEVSPACE_REVIEW_ALLOW_LOOPBACK=true` |
| Review DB | `/tmp/ab-canary/review.db` | xoá mới mỗi lần start đầu / `--reset` |
| Token | `/tmp/ab-canary/tokens.json` (0600) | khoá `admin`, `user1`, `user2`, `user3`, `training`; không in ra màn hình |
| Hash credentials | `/tmp/ab-canary/credentials.json` (0600) | gateway chỉ nhận SHA-256 |
| FE identity map | `/tmp/ab-canary/fe/.review-devspace-tokens.local` | Admin → `canary-admin`; Người dùng thường → `canary-user-1` |
| PID / log | `/tmp/ab-canary/pids`, `/tmp/ab-canary/logs/` | `down.sh` chỉ dừng PID trong file này |

Danh tính: `canary-admin` (admin), `canary-user-1..3` (reviewer). Đọc token trong script bằng `json.load(open('/tmp/ab-canary/tokens.json'))['admin']`; không đưa vào log/commit.

## Dùng

```sh
cd /home/ict/AIEngines/secai/secai-agent-gateway
scripts/ab_canary/build-fe.sh           # build vào /tmp/ab-canary/fe/dist (không đụng secai-fe-devspace/dist)
scripts/ab_canary/up.sh                 # idempotent; đang chạy thì không làm gì. --reset = DB và token mới
.venv/bin/python -I scripts/ab_canary/seed.py                         # ~58 mục tổ hợp + 6 mục demo
.venv/bin/python -I scripts/ab_canary/seed.py --n 1000 --pending 20   # thêm 1000 cặp QA hợp lệ, 20 cặp chờ duyệt
scripts/ab_canary/down.sh               # dừng; --purge xoá luôn /tmp/ab-canary
```

Mở `http://127.0.0.1:18262`, đổi **Demo → Admin → Đánh giá A/B**. `seed.py` chạy lại được (bỏ qua phần đã có). Trạng thái sau `--n 1000 --pending 20`: 980/1000 cặp sẵn sàng (nguồn QA); duyệt hàng loạt 20 cặp còn lại (nguồn QA) để chạm ngưỡng. Muốn đủ 1000 ngay: `--pending 0`.

Dữ liệu seed (đều gắn `[synthetic ab-canary]`):
- Campaign "Đánh giá từ chat" (nguồn `live`): thắng A/B rõ, 2-3 phiếu đồng thuận, tie, cả hai kém, bỏ qua, thiếu ngữ cảnh, thiếu phiếu (2 giao, 1 nộp), chưa có phiếu, mâu thuẫn (A vs B), trùng prompt/đáp án (kể cả đảo thứ tự và cùng prompt khác đáp án), câu hỏi dài, markdown, code, hội thoại nhiều lượt, đã duyệt (winner), đã loại.
- Campaign "Demo 2 vai trò — canary synthetic" (nguồn `qa`): 6 phân công chờ cho `canary-user-1` (nút "Thử đánh giá A/B").
- Campaign "QA canary synthetic bulk" (nguồn `qa`): `--n` cặp hợp lệ, mỗi cặp một nhóm nguồn riêng, đã có phiếu và admin duyệt.

## Hạn chế

- Training: không có service huấn luyện fake. Gateway trỏ `REWARD_TRAINING_URL` vào cổng chết `18269`, nên eligibility/hiển thị đúng nhưng "Fine-tune → Xác nhận" chốt batch rồi báo lỗi kết nối (batch ở `dispatching`, chặn lượt kế tiếp). Không có tiến trình/loss. Repo chỉ có `httpx.MockTransport` trong test (không dùng được qua HTTP). Muốn lặp lại sau khi chốt: `up.sh --reset` rồi seed lại.
- Mẫu `live` được tạo qua `ReviewStore.create_live_assignment` (subprocess cô lập), vì `/live-pairs` cần LLM thật; các bước còn lại qua API. `submitted_at` của phiếu seed bị lùi bằng SQL trên DB canary (`--spread-hours`, mặc định 72, `0` = tắt) để các thời điểm khác nhau; thay đổi này không đi qua API.
- Không có LLM/Qdrant/Redis: chat, `/live-pairs` trả `offered:false`, classifier cảnh báo lệch embedding (fail-open). `/health` xanh không chứng minh RAG.
- Danh tính demo là mô phỏng (vai trò dùng chung), giống Devspace thật; không phải đăng nhập.
- Build FE không chạy `tsc -b` (chỉ `vite build`); `secai-fe-devspace/.env.local` vẫn được Vite đọc nhưng ba cờ report/file/image bị ghi đè tường minh.
- Cổng cấm: 5173, 8131, 8133, 8134, 18183, 18185, 18241 (và tunnel 18197/18083/18868); script từ chối dùng.
