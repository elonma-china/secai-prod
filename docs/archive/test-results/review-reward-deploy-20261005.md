# Triển khai bản sửa review/Reward feedback — 2026-10-05

Đã commit mã nguồn `fe6c5dc332c85821b685e23612bff2cc8298e6d6` và triển khai lên gateway 8131, Devspace host 5173 và Docker Devspace 5179. Không push remote trong lượt này.

## Phạm vi

- Gateway dùng đúng image đang chạy làm base, chỉ overlay `api/middleware.py` và `api/review_schemas.py`; giữ entrypoint `secai_gateway.unified_app:app`, environment, network và toàn bộ mount cũ.
- Docker Devspace dùng image frontend đang chạy làm base, cập nhật build và static proxy. Bổ sung mount read-only credential review riêng vì bản trước trả 503 do thiếu file. Không đưa token vào image, frontend bundle hoặc Git.
- Devspace host nhận cùng build; publish assets trước rồi thay index atomically, giữ assets cũ phục vụ lượt tải đang chạy.
- Không thay report backend, dịch gateway/worker, OCR worker hoặc model serving. Giữ upstream báo cáo/dịch và volume dữ liệu. Sampling/cooldown production vẫn 0.1/600 giây.

Immutable images:

| Service | Image ID |
|---|---|
| Gateway | `sha256:232c5e90739233ce55a33074a5bd6fc17ac20ac71027cab01a946fd2da2a05f8` |
| Docker Devspace | `sha256:b14ec7a6c8810ffc51594576f7fcc4e06ccc3b4996da0aeacd02c50f8a8a6739` |

## Kiểm chứng

- Backend liên quan chạy lại trước commit: **38 passed**. Kết quả frontend/build/QA trước đó: [báo cáo sửa và kiểm thử](review-reward-fix-20261005.md).
- Docker canary có DB/credential riêng: health OK, `pip check` không có dependency lỗi; CRUD limit 120, model-backed routes 15, schema nhận skip. Đủ bốn ca E2E được kiểm chứng qua hai lượt: chat A/B + cooldown, recovery khi read trả 429, admin/import/chấm sáu loại/duyệt/export/đóng, template bằng model thật. Luồng admin chạy lại sau sửa đường dẫn artifact trong script test; lỗi đường dẫn đó không phải lỗi ứng dụng. Canary sampling=1 để kiểm tra chắc chắn UI, cooldown=600; production không đổi sampling.
- Sau chuyển dịch vụ, E2E production trên 5179: **2 passed**, 21.29 giây: toàn luồng admin và template dùng model thật. Preference trước duyệt rỗng; sau duyệt có hai winner, raw có sáu mẫu, SHA-256 khớp. Chiến dịch tổng hợp đã đóng; phiếu QA không dùng training.
- Cả 5173/5179: health, identity admin, report template list và v3 language capabilities HTTP 200; thiếu auth review trả 401. Đã đối chiếu command/entrypoint và mọi mount cũ vẫn được giữ.
- Các container canary của lượt này đã được xóa; không xóa volume hoặc database. Không thực hiện rollback production.

Toàn suite backend vẫn chưa pass; xem bảy failure và lượt chạy chưa hoàn tất trong báo cáo QA. Việc triển khai không thay bằng chứng hoặc dữ liệu để hợp thức hóa suite.

## Snapshot và rollback

Private release directory: `/home/ict/secai-releases/review-reward-fix-20261005`, mode 0700. Profile/inspect JSON mode 0600. Có snapshot SQLite gateway/session/review và bản frontend trước đổi. Activation giữ `gateway-deploy.lock`, kiểm identity/image trước thao tác; chỉ `up -d --no-deps --no-build` hai service được sở hữu.

Rollback chuẩn bị riêng cho lượt này:

```sh
python3 /home/ict/secai-releases/review-reward-fix-20261005/rollback.py
```

Script giữ deployment lock và từ chối nếu container identity đã đổi bởi lượt triển khai khác. Giữ database hiện tại; không tự phục hồi backup làm mất phiếu sau triển khai. Script chưa được chạy trên production.

Bằng chứng ngoài Git:

- Release `active.json`, `verification.json`, `baseline.json`, `images.json` trong private release directory.
- E2E production: `/tmp/secai-devspace-fix-20261005/.e2e-production/report.json`, `summary.md`, screenshot/video/trace trong `artifacts/`.
- E2E Docker canary: `/tmp/secai-devspace-fix-20261005/.e2e-docker/`; các ca pass nằm ở hai lượt chạy đã mô tả.
