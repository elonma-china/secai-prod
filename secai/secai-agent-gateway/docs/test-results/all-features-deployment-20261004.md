# Kiểm chứng triển khai 2026-10-04

## Code và Docker

- Gateway main/remote đã tích hợp báo cáo, dịch file/ảnh, OCR, đa ngôn ngữ và expert review tại `c4c3c8022165085a97d28ca0aa310de4109a7f90`.
- Image `secai-agent-gateway:all-features-c4c3c80`, SHA256 `72167a647ca2ce3bab0ccf11137475b0024881e2256a907e127cf4e6c4a97bec`.
- Sáu container của ba project hiện có được đổi image, giữ command, cấu hình private và volume dữ liệu. Mỗi project sử dụng lock triển khai đang có; kiểm ownership trước thay đổi/rollback.
- Kiểm sau deploy: health200; reports/templates có auth200; expert /review-api/me không auth401; /ai/v3/translate/languages200 và đủ cặp bật; file routes còn trong OpenAPI. Devspace5173 và5179 cùng bundle `index-aNM_IA-f.js`, có report/file/image feature flags.
- Browser Devspace chính xác nhận mục Báo cáo thông minh, Theo mẫu, Template bằng prompt, Toàn màn hình; Dịch tệp và ảnh và Mở dịch đa ngôn ngữ hiện trên UI.
- Hai report đã lưu trước triển khai (`aa9eb0ce-ba22-4f73-ba24-80f0bf790824`, `25cfd96f-0bd6-467a-93d1-68097bfbf89b`) vẫn đọc lịch sử revision200 sau triển khai.
- Snapshot SQLite hiện tại của review/jobs/report lưu private ngoài Git sau rollout. Không coi đây là snapshot trước rollout.

## Vast hiện có

- Dùng lại instance A10080GB hiện có; không thuê/destroy mới.
- Ba model resident được probe inference thật trên endpoint local: Cyber-F1 chat, DuyTa/sec-embedding embeddings và DuyTa/sec-rerank rerank đều200 đúng model ID.
- Hai API private OCR18083/MT18868 dưới supervisor riêng, shared GPU admission lock, model on-demand. Token/weights/venv đang có được reuse và kiểm checksum.
- Smoke tuần tự thật: dịch câu tiếng Anh sang tiếng Việt; OCR ảnh synthetic chữ “SECAI2026”; cả hai trả200 có nội dung. Đây là kiểm khả năng chạy, không phải điểm chất lượng trên dataset nghiệm thu.
- Smoke OCR ban đầu phát hiện thiếu PYTHONPATH trong disposable child; đã sửa worker để truyền đường dẫn helper và thêm test spawned interpreter. Smoke cả hai pass sau sửa.
- Sau inference GPU trở lại baseline72278MiB used/8772MiB free, chỉ ba resident.
- Kiểm phục hồi: dừng worker MT lúc GPU idle; supervisor tự tạo worker mới, PID OCR/supervisor giữ nguyên và ba resident được giữ.
- Hook boot được thêm sau startup resident; bản startup trước thay đổi giữ private. Không reboot instance để kiểm hook vì đang phục vụ chung.

## Kiểm thử và giới hạn

Gateway:1719passed,1skipped,7deselected ở lượt functional cuối; bảy case phụ thuộc fixture Bughunt/PoC bên ngoài đã được ghi ở all-features-main-integration.md. Không khẳng định toàn bộ suite pass. Frontend47tests và build pass. Serving test evidence/ma trậnGPU ghi trong repo serving riêng.

## Rollback trên host

Private release `/home/ict/secai-releases/all-features-20261004/` có profile cũ/mới pin immutableimage, inspect snapshots và helper. Không đưa profile/env/token vào Git.

```sh
python3 /home/ict/secai-releases/all-features-20261004/release.py verify
python3 /home/ict/secai-releases/all-features-20261004/release.py rollback
python3 /home/ict/secai-releases/all-features-20261004/frontend-rollback.py
```

Helper từ chối nếu ownership/container hoặc frontend đã bị deployment khác thay đổi. DB/volume giữ nguyên. Oldimage đã chạy health200 trong canary riêng rồi canary được xóa; chưa đảo live để diễn tập.

Model rollback trên instance: chạy `/workspace/secai-specialist-upgrade-before/rollback.py` bằng Python; dừng supervisor thuộc release, khôi phục startup và đúng hai command/env private trước thay đổi. Không đụng ba resident hoặc xóa model.
