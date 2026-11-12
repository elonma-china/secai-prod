# secai-agent-gateway — project instructions

Đọc [AGENTS.md](AGENTS.md) để biết kiến trúc, quy tắc thay đổi và kiểm chứng; [README.md](README.md) là hướng dẫn API và triển khai.

## Serving Vast: đủ năm model

`secai-serving-vast/` là Git repository riêng. Kiểm tra trạng thái và commit riêng khi sửa serving; không stage cấu hình private vào repository gateway.

Script `serving-up-cyber-f1.sh` hiện mặc định đúng các model:

| Chức năng | Model |
| --- | --- |
| LLM | `DuyTa/Cyber-F1-AWQ` |
| Embedding | `DuyTa/sec-embedding` |
| Rerank | `DuyTa/sec-rerank` |
| OCR | `Qwen/Qwen3-VL-2B-Instruct` |
| Dịch chuyên dụng | `tencent/Hy-MT2-7B` qua GGUF Q4_K_M |

Tên model request phải khớp serving. Không thay embedding/rerank bằng model stock: mismatch gây 404 và RAG có thể rơi về BM25-only dù health vẫn xanh.

Từ thư mục `secai-serving-vast/`, kiểm kế hoạch offline trước khi thuê:

```sh
./serving-up-cyber-f1.sh --dry-run --gpus 1
./serving-up-cyber-f1.sh --dry-run --gpus 2 --specialist-gpu 1
./serving-up-cyber-f1.sh --dry-run --gpus 4 --specialist-gpu 3
```

Phạm vi hỗ trợ là A100 80GB, số GPU 1..4. Mỗi GPU có một replica LLM/embedding/rerank; nginx cân bằng tải. OCR/dịch có API private trên loopback 18083/18868, dùng GPU được chọn, shared admission lock và nạp model lần lượt theo request. Không giả định thêm GPU sẽ tự tăng replica OCR/dịch. Bootstrap yêu cầu ít nhất 8192 MiB free trên GPU phụ; không hạ guard hay dừng resident để vượt kiểm tra.

Chỉ chạy `--yes` khi tác vụ đã cho phép thuê và ngân sách được xác định. Khi nâng cấp instance đang chạy, dùng `serving-specialists-install.sh --reuse-artifacts` với Python và token paths hiện có; drain đúng hai ASGI cũ trước khi thay, giữ ba resident. Không chạy rental để thay cho installer. Supervisor tự restart worker, hook boot đặt sau resident readiness. Giữ shared lock, checksum, cleanup veto và token private.

## Kiểm chứng serving

```sh
# Từ secai-serving-vast/; offline, không thuê GPU.
python3 -m unittest discover -s tests -v
bash -n serving-up-cyber-f1.sh serving-specialists-install.sh vast/on_start_cyber_f1.sh
git diff --check
```

Script thuê probe authenticated inference LLM/embedding/rerank từng replica và qua nginx, rồi smoke OCR/dịch tuần tự. `/health` đơn lẻ không chứng minh model hoạt động. Dùng dữ liệu synthetic cho smoke; không coi smoke là điểm chất lượng nghiệm thu. Matrix 1/2/4 đã kiểm bằng mô phỏng; live instance 1 GPU đã smoke đủ năm model. Chưa kiểm live máy thuê mới 2/4 GPU.

Chi tiết artifact, flags, installer và matrix: `secai-serving-vast/vast/gpu_specialists/README.md` trong repo serving riêng.

## Nhiều agent Claude Code cùng làm việc (git + deploy)

Nhiều agent có thể chạy đồng thời trên cùng máy, cùng repo. Dưới đây là quy tắc bắt buộc, rút từ sự cố thật (main bị `reset --hard` mất 5 commit chưa push; worktree khác bị lệch index khi ai đó đổi `main` dùng chung; container dịch bị `docker run` tay đè tên không qua compose, `release.json` hỏng giữa đường không ai biết):

- **Mỗi agent 1 worktree riêng, 1 nhánh feature riêng.** `git worktree add ../wt-<task> -b fix/<task> main`. Không bao giờ `git checkout`/sửa trực tiếp trên checkout chính nếu không chắc có agent khác đang dùng — kiểm `git worktree list` trước.
- **Không bao giờ code trực tiếp trên `main`.** Merge vào `main` chỉ qua `git merge-tree main <nhánh>` (kiểm xung đột, không cần checkout `main`, không đụng worktree ai khác). Nếu phải cập nhật `main` mà nó đang được checkout ở worktree khác, dùng plumbing (`git commit-tree` + `git update-ref`) rồi `git -C <worktree-đó> reset --mixed && git -C <worktree-đó> checkout HEAD -- .` để đồng bộ lại index của họ, không để lệch.
- **Push nhánh lên origin ngay khi xong việc.** Commit chỉ nằm local là thứ dễ mất nhất khi nhiều agent cùng đụng nhánh chung.
- **Tuyệt đối không `git reset`/`reset --hard` một nhánh chung** (main, hay bất kỳ nhánh ai khác có thể đang checkout) khi chưa chắc ai đang dùng. Muốn "dọn" lịch sử, tạo nhánh backup trước.
- **Có 1 worktree "main" cố định, lâu dài** (không nằm trong thư mục scratchpad của session nào — dễ bị dọn mất) để mount cho production và để merge vào. Không ai code trực tiếp trong worktree này.
- **Container production không bao giờ mount sống từ checkout đang có agent code nhánh feature.** Nguồn mount luôn là worktree "main" cố định ở trên. Agent đổi nhánh ở checkout chính không được phép làm đổi code đang chạy production.
- **Không `docker run`/`docker stop`/`docker rm` tay vào container production**, dù chỉ để test nhanh. Dùng đúng script release có sẵn (`deploy/translation-release/release.py` — có lock, kiểm ownership, rollback, readiness-check; dùng làm khuôn mẫu cho mọi service khác). Container test tạm phải đặt tên khác hẳn (`*-smoke-<ngày>`), không bao giờ trùng/đè tên production.
- **Rollback bằng đổi tên, không xoá.** Trước khi thay container/image mới, `docker rename` container cũ thành `*-rollback-<ngày>` rồi `stop` (đúng quy ước đã có trong repo), giữ nguyên image cũ — không `rm`/`rmi` cho tới khi chắc bản mới ổn.
- **Tag image theo commit SHA** (`secai-agent-gateway:<service>-<sha-ngắn>-<ngày>`) thay vì tên mô tả mơ hồ, để nhìn tag biết ngay build từ commit nào, đỡ phải điều tra bằng `docker history`/`grep` trong container.

Đọc [deploy/all-features/README.md](deploy/all-features/README.md) trước cập nhật full-feature image. Gateway/worker/report chạy CPU; giữ entrypoint, private env và volume theo từng service. Nhiều agent có thể triển khai chung: dùng lock hiện có, kiểm ownership và immutable image ngay trước switch/rollback; chỉ cập nhật service thuộc phạm vi.

Devspace staticserver tách report `/ai/v2/reports` sang 18183, file/image và text v3 sang 18185, còn legacychat/report và `/review-api` sang 8131. Dùng `REPORT_GATEWAY_PROXY`/`TRANSLATION_GATEWAY_PROXY` hoặc `.report-upstream`/`.translation-upstream`; không chứa key trong URL. Build bật ba cờ report/file/image đã ghi trong README. Report phải persist nội dung và revision trong `REPORT_DB_PATH`; giữ DB khi rollback.

Bằng chứng rollout và lệnh rollback hiện tại: [all-features-deployment-20261004.md](docs/test-results/all-features-deployment-20261004.md). Profile/env/backup nằm private ngoài Git. Chỉ dọn `.env.bak*` khi không còn tham chiếu; giữ `.env` đang dùng, `.env.pentest` và `.env.example`.
