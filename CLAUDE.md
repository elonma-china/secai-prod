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

## Docker và Devspace

Đọc [deploy/all-features/README.md](deploy/all-features/README.md) trước cập nhật full-feature image. Gateway/worker/report chạy CPU; giữ entrypoint, private env và volume theo từng service. Nhiều agent có thể triển khai chung: dùng lock hiện có, kiểm ownership và immutable image ngay trước switch/rollback; chỉ cập nhật service thuộc phạm vi.

Devspace staticserver tách report `/ai/v2/reports` sang 18183, file/image và text v3 sang 18185, còn legacychat/report và `/review-api` sang 8131. Dùng `REPORT_GATEWAY_PROXY`/`TRANSLATION_GATEWAY_PROXY` hoặc `.report-upstream`/`.translation-upstream`; không chứa key trong URL. Build bật ba cờ report/file/image đã ghi trong README. Report phải persist nội dung và revision trong `REPORT_DB_PATH`; giữ DB khi rollback.

Bằng chứng rollout và lệnh rollback hiện tại: [all-features-deployment-20261004.md](docs/test-results/all-features-deployment-20261004.md). Profile/env/backup nằm private ngoài Git. Chỉ dọn `.env.bak*` khi không còn tham chiếu; giữ `.env` đang dùng, `.env.pentest` và `.env.example`.
