# Giao diện quản lý A/B gọn — 2026-10-06

## Thay đổi

- Ba nhóm chính: Chờ duyệt, Đã duyệt/chờ huấn luyện, Đã loại; nhóm đang chọn được tô nổi bật.
- Danh sách có câu hỏi, lựa chọn canonical của user và thời gian nộp; tìm kiếm và phân trang 10 mẫu.
- Chi tiết đặt câu hỏi trên hai câu trả lời A/B, đánh dấu User đã chọn. Nhận xét/nhãn lỗi và phân công được thu gọn.
- Admin chọn đáp án, xác nhận dùng huấn luyện hoặc loại mẫu. Nhận xét tùy chọn; khi trống, lưu lý do xác nhận/loại mặc định đúng hành động admin.
- Nút Fine-tune và kết quả lưu vẫn dùng API hiện có; chi tiết model/job/hash được thu gọn. Chọn/tạo campaign và import/export nằm trong phần nâng cao.
- Làm mới vô hiệu cache danh sách và panel đang chọn; khóa duyệt đến khi phản hồi mới tải xong. Lỗi tải không cho phép duyệt dựa trên detail cũ.

## Kiểm chứng

- `npm test -- --reporter=dot`: 13 file / 77 test đạt. Bao gồm regression lựa chọn thay đổi sau refresh và thêm phiếu thứ hai khi panel đang mở (khóa duyệt trong thời gian tải).
- `npm run build`: thành công; cảnh báo bundle >500 KiB của Vite vẫn còn.
- Skill e2e, runner 0.17.0, dùng harness có sẵn `/tmp/secai-devspace-e2e-20261005`; không đổi dependency sản phẩm. T3 preview báo không có automation host; dùng Chromium headless.
- Luồng admin thực: user thường không có tab A/B; campaign QA có 3 phiếu user đã nộp. Admin duyệt 1 / loại 1 qua HTTP 200, còn 1 chờ duyệt; tìm kiếm và trạng thái giữ đúng sau reload. Không tự gọi training.
- Dataset 1.000 mẫu: trang 10 dòng, sang trang 2/100; kết quả Vast đã lưu còn hiển thị Hoàn tất; các details đóng mặc định.
- Mobile 390×844: không tràn ngang. Chặn PUT adjudication bằng HTTP 503 để kiểm thử lỗi; không xuất hiện trạng thái duyệt giả, mẫu còn chờ và có thể thử lại. Đã gỡ interceptor.
- Chạy lại hai kịch bản đọc/mobile sau sửa cuối: passed, run `01a10f7a-c40c-7f52-920b-8b3f014e7f3c`. Luồng ghi dữ liệu đã đạt ở run `01a10f76-0c2a-735a-a6b0-584417e2dc10`.
- Browser public Cloudflare mở bản build mới, xem lựa chọn user và Làm mới panel thành công. Reviewer độc lập không còn finding Important sau hai sửa cache.
- `git diff --check`: đạt. Không đổi backend nên không chạy lại toàn bộ pytest.

Bằng chứng local (không commit runtime): `output/playwright/ab-compact-pending.png`, `output/playwright/ab-compact-mobile.png`; log `/tmp/ab-compact-*`; report `.e2e/report.json` trong harness.

## Flow demo ngắn

1. Mở Devspace, chọn Demo → Admin → Đánh giá A/B.
2. Mở Nâng cao → chọn đợt `QA DEMO · Giao diện A/B gọn`, rồi thu gọn.
3. Trong Chờ duyệt, bấm câu hỏi; xem User đã chọn ở A/B.
4. Chọn đáp án đúng → Xác nhận dùng huấn luyện, hoặc Loại mẫu.
5. Mở Đã duyệt, chờ huấn luyện để kiểm tra mẫu đã lưu. Admin chủ động nhấn Fine-tune khi đủ dữ liệu hợp lệ.

Vai trò vẫn là demo tài khoản chung; training vẫn chỉ nhận dữ liệu QA/demo có đủ nhóm độc lập. Đây là kiểm chứng giao diện, không chạy thêm lượt GPU hoặc kích hoạt model mới.
