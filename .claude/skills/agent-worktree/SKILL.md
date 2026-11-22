---
name: agent-worktree
description: Tạo worktree + nhánh feature riêng, chạy/test trong worktree và merge + dọn dẹp an toàn khi nhiều agent cùng làm trên repo secai-agent-gateway. Dùng trước khi bắt đầu sửa code hoặc khi cần merge vào main.
---

# Quy trình git an toàn nhiều agent

1. Kiểm tra trạng thái: `git rev-parse --show-toplevel`, `git status --short --branch`, `git worktree list`.
2. Phải làm việc trong worktree riêng, không bao giờ trên `main`/checkout chính.
   - Phiên T3 đã ở worktree (`git branch --show-current` không phải `main`): dùng luôn, đừng tạo thêm.
   - Chưa có: `scripts/wt-new.sh <task>` (từ `origin/main`, nhánh `feat/<task>`, copy `.env`, tạo `.env.worktree` cho stack dev riêng).
3. Chạy/test **chỉ qua `scripts/dev.sh`** (`up|health|test|down`…): stack riêng, cổng LAN riêng, `health` xác nhận đúng nhánh. Không gọi `docker compose`/`docker run` trực tiếp. Chi tiết: AGENTS.md mục "Chạy thử theo worktree".
4. Commit theo phạm vi yêu cầu; **push nhánh lên origin ngay khi xong**.
5. Merge: mở PR rồi `gh pr merge`. Kiểm xung đột trước bằng `git merge-tree main <nhánh>` (không checkout `main`). Nếu phải cập nhật `main` đang checkout ở worktree khác: `git commit-tree` + `git update-ref`, rồi `git -C <worktree> reset --mixed && git -C <worktree> checkout HEAD -- .`.
6. Sau merge: `scripts/wt-prune.sh` (dry-run) xem sẽ dọn gì, rồi `scripts/wt-prune.sh --yes`. Script chỉ dọn worktree/nhánh có PR đã merge đúng commit, cây sạch và không còn tiến trình nào trong đó; worktree của chính phiên đang mở được bỏ qua và dọn ở lần sau. Không tự `git worktree remove`/`branch -D` tay.

Cấm: `reset --hard`/`reset` trên nhánh chung, force-push, `docker run/stop/rm` tay vào container production, commit `.env`/`.env.worktree`/token/log/backup, đụng container/volume `secai-docker-*` hoặc của worktree khác.
Rollback container bằng `docker rename ... *-rollback-<ngày>` rồi stop; tag image theo SHA: `secai-agent-gateway:<service>-<sha-ngắn>-<ngày>`.
