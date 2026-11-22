---
name: gateway-verify
description: Chạy kiểm chứng backend gateway (pytest, kiểm contract chat/report/translate) sau khi sửa code trong src/ hoặc tests/.
---

# Kiểm chứng gateway

Đọc `AGENTS.md` mục "Kiểm chứng" để chọn phạm vi.

```sh
scripts/dev.sh test tests/<file_liên_quan>.py   # test hẹp trước
scripts/dev.sh test                              # toàn bộ khi đổi chung
git diff --check
```

Dùng `scripts/dev.sh test`, không chạy `.venv/bin/python -m pytest` trần trong worktree: venv dùng chung cài editable bản **main**, pytest trần sẽ test nhầm code của `main`. `dev.sh test` đặt `PYTHONPATH` về `src/` của worktree và tự copy `.env` nếu thiếu.

Lưu ý:
- Đổi contract: kiểm backend, frontend, test, tài liệu API; giữ tên field JSON và sự kiện SSE.
- Chat: kiểm cả `/ai/chat` và `/sessions/{sid}/chat`, JSON và stream, quyền sở hữu theo `user_id`.
- Cần xem chạy thật: `scripts/dev.sh up` rồi `scripts/dev.sh health` (phải in đúng `git_branch`); xong `scripts/dev.sh down`.
- `tests/test_gateway.py::test_streaming_endpoints` đang fail sẵn trên `main` (không do thay đổi của bạn); ghi rõ khi báo cáo, đừng coi suite là pass.
- Chỉ sửa tài liệu: kiểm nội dung, đường dẫn/lệnh và diff, không cần chạy suite.
- Báo ngắn bằng tiếng Việt: đã đổi gì, kiểm chứng nào đã chạy, phần chưa xong.
