# Devspace → Vast: job fine-tune Qwen3-1.7B thật

Ngày 2026-10-06. User chốt nối nút huấn luyện với VPS Vast hiện có và chạy QA trước. Không thuê instance mới. Thay đổi không kích hoạt reward scorer cho chatbot.

## Kết quả

Đã bấm nút trên Devspace 5173 bằng skill Playwright CLI (T3 preview status/open không có automation host), tạo job HTTP 202, quan sát training/evaluating trên GPU và kết quả hoàn tất qua API thật. Tải lại trang, khởi động lại dịch vụ training và truy vấn lại đều giữ kết quả. Bấm lại với dataset không đổi trả cùng job; không chạy thêm GPU.

- Vast instance `53846446`, A100 SXM4 80 GB, SSH `ssh9.vast.ai:10812` tại thời điểm kiểm thử.
- Base `Qwen/Qwen3-1.7B`, revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`.
- LoRA + scalar head, BF16, rank 8, epoch 1, learning rate `1e-4`, max length 384.
- Phiên bản thực: PyTorch `2.14.1`, Transformers `4.57.1`, PEFT `0.17.1`.
- Dataset **1.000 cặp tổng hợp / 50 nhóm chính sách**, tái sử dụng corpus QA đã xây ở [lượt thử 1.000 cặp](reward-qwen3-17b-1000-pairs-20261006.md). Không phải phản hồi chuyên gia thật. Script nhập, phân công, gửi judgment và duyệt qua API thật trong campaign QA mới; nhãn script được đối chiếu với chính sách giả lập trong prompt.
- Chia theo nhóm **600 train / 200 validation / 200 test**, không gộp nhóm giữa partition.
- Test cuối **200/200 đúng, accuracy 100%, ties 0**; Wilson lower bound theo cặp `0.981154` chưa điều chỉnh tương quan. Invariance swap/equal delta `0.0`. Đây là kết quả QA lặp mẫu, không chứng minh chất lượng ATTT hoặc đồng thuận chuyên gia thật.
- Job từ created đến succeeded **207,93 giây**, gồm prepare/load/train/evaluate/cleanup; không gồm cài dependency, tải base hoặc tạo campaign.
- Quan sát GPU lúc training: child **4886 MiB**, VRAM free **3881 MiB**; đây là một sample, không phải peak. Sau job: free **8772 MiB**, như baseline. Giữ nguyên ba GPU resident PID; evidence có `clean_exit_attested=true`, không có poison marker.

Campaign: `007ad695-f675-4b72-801b-813cfc77ea45`. Job: `524fccaf-56a2-42f9-b0f6-496884af59d9`. Artifact SHA-256: `ba7b9881cc84f41490fdaf95ade82bf1a19f54ff3b394496512ef3af9a2434c0`.

Artifact gate `eligible=false`: dữ liệu demo và chưa có release policy. Không đổi gateway scoring hoặc tự đưa model này vào chatbot.

## Flow demo

1. Chọn **Admin** → **Đánh giá A/B**.
2. Chọn đợt **QA DEMO · Vast Qwen3 · 1000 cặp · 20261006**.
3. Xem số cặp đã duyệt và kết quả **Hoàn tất**.
4. Dataset QA mới đủ nhóm: bấm **Huấn luyện model chấm điểm** → chờ GPU → training → evaluating → kết quả.
5. Bấm **Cập nhật trạng thái** hoặc tải lại trang để xem phiên bản đã lưu. Dữ liệu không đổi sử dụng lại job trước.

Người dùng thường vẫn không có tab quản trị. Bộ chọn Demo dùng tài khoản chung cho mỗi vai trò, chưa là đăng nhập hai tài khoản production độc lập. Campaign demo 2 vai trò cũ vẫn giữ hai phiếu đã duyệt và một ví dụ chưa đánh giá; không gộp nó vào corpus 1.000 cặp.

## Kiểm chứng

- Test frontend: **13 file / 74 pass**; build thành công (cảnh báo Vite chunk >500 kB).
- Test backend liên quan: **38 pass** (8 test training mới, pipeline/service/review auth/API/store). Test bộ ba này không tải 1.7B trong CPU suite; training GPU được kiểm thật ở trên.
- Suite backend đầy đủ: **1781 pass, 2 fail, 3 skip**, 64 warnings, 588,80 giây. Hai lỗi tái hiện bằng source HEAD `67e712c` trước thay đổi: `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors` (legacy preflight trả wildcard CORS), `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` (thiếu `/home/ict/AIEngines/secai/claude-bug-bounty`). Không coi suite toàn bộ là pass.
- Bearer thiếu: 401; reviewer gọi API training: 403; private service thiếu token: 401. Không nới auth, lấy mẫu hoặc cooldown.
- Browser: tạo job thật 202, disabled khi active, saved artifact sau reload/service restart, idempotency, ẩn tab cho user thường, viewport 390×844 không tràn ngang.
- Kiểm tra lỗi UI: fault injection 503 cho GET job list trong browser; vẫn giữ kết quả trước và khóa submit; bỏ interception rồi GET 200 backend thật khôi phục trạng thái. Đây là lỗi mô phỏng, không khẳng định dịch vụ thật bị outage.
- Reviewer độc lập phát hiện staging `.prepare-*` sau crash lọt vào queue. Đã sửa chỉ đọc folder UUID published, kiểm identity, fsync snapshot/status; regression test và test worker queue pass. Reviewer kiểm lại không còn Critical/Important.
- Sửa refresh UI xóa kết quả tạm thời; regression test giữ kết quả khi đang refresh và disabled submit pass.
- `git diff --check`: pass.

## Triển khai và bằng chứng

Static server 5173 phục vụ build mới. Container riêng `secai-reward-training-relay` dùng cùng review DB/auth, chỉ listener `127.0.0.1:18241`; không restart gateway chính. Systemd user tunnel `secai-reward-training-tunnel.service` nối `127.0.0.1:18197` tới VPS `127.0.0.1:8197`. Service training trên Vast chạy riêng dưới `/workspace/secai-reward-training`; sau reboot VPS cần operator khởi động lại. Hướng dẫn [deployment](../../deploy/reward-training/README.md).

Link Cloudflare hiện có được GET 200 và nội dung index khớp build mới. Đây là quick tunnel, địa chỉ có thể đổi khi tunnel khởi động lại.

Ảnh runtime ngoài Git: `output/playwright/reward-vast-training-start.png`, `reward-vast-training-running.png`, `reward-vast-training-complete.png`, `reward-vast-training-mobile.png`. Log private/runtime: `/tmp/reward-vast-ui-*.log`, `/tmp/reward-training-{fe-tests,fe-build,targeted-final,full-pytest,baseline-failures}.log`; kết quả live private `data/reward-training/verified-live-result.json`. Job/artifact/exports trên VPS; không commit token, DB, dữ liệu hoặc trọng số. File `/tmp` và quick tunnel không bảo đảm lưu lâu dài.
