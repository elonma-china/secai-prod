# Reward training QA trên Vast

Dịch vụ này huấn luyện thật Qwen3-1.7B bằng LoRA và scalar head, nhưng chỉ nhận campaign có nhãn QA/demo. Artifact luôn `purpose=demo`; không có API kích hoạt model hoặc sửa cấu hình chatbot. Luồng production với danh tính chuyên gia và release policy vẫn dùng [operator CLI](../../docs/reward-qwen-operator.md).

## Chuẩn bị riêng trên VPS

Tạo môi trường Python riêng, cài gateway với extra `reward`; không thay môi trường resident/OCR/dịch. Bundle phải có `src/`, `deploy/gpu_stage/` và `deploy/mt_cpu_service/child_supervisor.py`. Đặt `PYTHONPATH` tới cả `src` và root bundle. Cài dependency và tải trọng số là thao tác operator; job không tải model.

Cache phải có `Qwen/Qwen3-1.7B`, revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`. Đặt `HF_HOME` tới cache. Tạo token private ở gateway và SHA-256 token trong file `0600` trên VPS; không truyền token bằng tham số CLI. Thư mục job phải `0700`.

```sh
HF_HOME=/workspace/hf-cache \
PYTHONPATH=/workspace/secai-reward-training/bundle/src:/workspace/secai-reward-training/bundle \
/workspace/secai-reward-training/venv/bin/python -m secai_gateway.reward.training_service \
  --root /workspace/secai-reward-training/jobs \
  --token-hash-file /workspace/secai-reward-training/service-token.sha256 \
  --specialist-config /workspace/secai-specialist-services/config.json \
  --port 8197
```

Một process/worker cho một thư mục job. API bind `127.0.0.1`; chạy dưới supervisor của operator. Lần triển khai Devspace dùng process riêng trên Vast; sau khi container VPS khởi động lại, operator cần khởi động lại dịch vụ này. Không sửa startup của resident hoặc OCR/dịch trong bản triển khai này.

Config specialist là nguồn chuẩn cho GPU UUID, PID resident và shared admission lock. Runner sử dụng `GpuStageOwner` hiện có: tối đa 6144 MiB của child, dự trữ 2048 MiB, kiểm trước/đang chạy và chứng minh cleanup trước khi trả khóa. Child có parent-death supervision; không kill resident. GPU không còn đúng baseline hoặc cleanup chưa được chứng minh sẽ bị chặn. Không xóa evidence/poison marker để ép job chạy.

Job chờ khóa tối đa 600 giây, train/evaluate tối đa 3600 giây. Cấu hình hiện cố định: LoRA rank 8, 1 epoch, learning rate `1e-4`, max length 384. Câu quá giới hạn sẽ làm job thất bại, không tự cắt nội dung. Tối đa 4 job active. Snapshot và trạng thái được lưu trên disk; queued được tiếp tục sau restart, job đang chạy bị đánh dấu interrupted. File staging sau crash không được đưa vào hàng đợi. Dataset không đổi sẽ dùng lại job đã hoàn tất.

## Gateway và Devspace

Dùng SSH tunnel private, ví dụ local `127.0.0.1:18197` tới VPS `127.0.0.1:8197`. Thông tin kết nối Vast được đọc từ file private `data/reward-training/.env` (mode `0600`): `VAST_INSTANCE_ID`, `VAST_SSH_TARGET`, `VAST_SSH_PORT`, `VAST_SSH_KEY`, `REWARD_TRAINING_SSH_FORWARD`. Unit tunnel dùng `EnvironmentFile` và các biến này; không ghi trực tiếp host/port trong unit. Không commit file env. Cấu hình gateway:

```text
REWARD_TRAINING_ENABLED=true
REWARD_TRAINING_URL=http://127.0.0.1:18197
REWARD_TRAINING_TOKEN=<private token>
```

Các endpoint dưới `/review-api/reward/training` chỉ nhận Bearer admin, kiểm auth trước body:

- `GET /campaigns/{cid}`: capability và lịch sử job.
- `POST /campaigns/{cid}`, body `{}`: snapshot raw/preference từ DB; gửi sang VPS; trả 202 và job đã lưu. Không nhận đường dẫn, lệnh SSH hoặc hyperparameter từ browser.
- `GET /jobs/{jid}`: trạng thái và kết quả.

VPS xác minh export SHA-256, approval, campaign và chia theo prompt/source group. Cần ít nhất ba nhóm độc lập; chỉ winner được dùng. Khóa idempotency dựa trên nội dung dataset và cấu hình; bấm lại không tạo lượt GPU khi dữ liệu không đổi.

Có thể bật routes trong gateway hiện có, hoặc dùng `secai_gateway.reward.training_gateway:app` trên listener loopback riêng với cùng review DB/credentials. Lần Devspace dùng container relay riêng và giữ gateway chính. Static proxy đọc `.reward-training-upstream` hoặc `REWARD_TRAINING_GATEWAY_PROXY` để định tuyến riêng các path training; giữ chính sách kết nối và ánh xạ Bearer đang có. File env/token/upstream private không đưa vào Git.

Đọc [bằng chứng chạy thật](../../docs/test-results/reward-vast-devspace-training-20261006.md) và kiểm unit/test auth trước rollout. Không dùng dữ liệu QA hoặc accuracy QA làm tiêu chí phát hành model.
