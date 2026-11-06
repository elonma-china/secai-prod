# A/B và dịch — kế hoạch cải tiến đợt 1

**Goal:** đóng các lỗi review có tái hiện và tạo baseline chất lượng dịch trung thực, tái lập được.
**Spec:** `docs/criteria/ab-translation-improvement-20261007.md`, review `docs/reviews/ab-translation-independent-review-20261007.md` và hướng cải tiến được người dùng chấp thuận trong hội thoại.
**Architecture:** giữ API/job/store/React flow hiện có; sửa tại điểm gây lỗi, tái sử dụng evaluation harness. Không thêm hệ thống/abstraction/dependency mới nếu helper hiện có đủ dùng.
**Tech:** Python/FastAPI/SQLite, React/TypeScript, pytest/Vitest.

## Phân công và thứ tự

- [x] T1 Luna: tạo test đỏ Q01–07 trên source trước sửa. Sở hữu tests Python và file test frontend mới; không sửa product code.
- [x] T2 Sol backend: sửa `translation/worker.py`, `translation/jobs.py`, `api/review_training.py` theo Q01/Q02/Q06 sau bằng chứng đỏ. Không sửa test.
- [x] T3 Sol frontend: sửa `LiveReviewWidget.tsx`, `ReviewOverview.tsx`, `AbParts.tsx` và caller `ChatbotScreen.tsx` theo Q03–05 sau bằng chứng đỏ; giữ diff người dùng.
- [x] T4 Astra kiểm kê cho thấy harness hiện có đủ cơ chế offline; không cần Sol viết thêm code trùng lặp. Luna xác minh Q08–10, Astra viết hướng dẫn baseline và giới hạn model/corpus.
- [x] T5 Luna: xác minh các fix, CORS fixture, focused/full backend, frontend build/test và baseline offline. Full backend còn 7 failure đã tái hiện trên source gốc; không coi suite là PASS.
- [x] T6 Astra độc lập: review toàn diff theo Q01–12; Sol sửa finding caller, Luna chạy lại test bị tác động; source APPROVE, không chứng nhận production.
- [x] T7 Tích hợp patch về checkout người dùng sau kiểm tra không có thay đổi xen vào; giữ nguyên file ngoài phạm vi. Không commit/push/deploy.

## Quy tắc thực hiện

Workspace riêng `/home/ict/secai-quality-improvement-20261007-wt/secai/secai-agent-gateway`; copy đúng các file FE dirty liên quan trước sửa. Python chạy bằng `.venv/bin/python` với `PYTHONPATH=$PWD/src` để editable install không trỏ về source cũ. Dùng dependency sẵn có, không copy `.env`; các test mock dịch vụ ngoài. User đã chọn execution đa agent và đồng ý hướng sửa ở lượt trước; không mở vòng xin lại phê duyệt cho từng fix có phạm vi rõ.

T1 và T2/T3 dùng cùng interface hiện tại, nhưng ownership file không trùng. T4 chỉ bắt đầu sau kiểm kê, không thay đổi chất lượng model khi chưa đo. T5 đọc kết quả T2–4. T6 chỉ đọc source/tests. Tiến độ và ruling lưu `/tmp/secai-quality-20261007/progress.md`.
