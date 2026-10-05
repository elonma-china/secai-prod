# Devspace: grid dịch tệp và kết nối báo cáo — 05/10/2026

## Thay đổi

- Tệp/ảnh hiển thị bằng grid, 4 thẻ mỗi trang; loading khi chờ/chạy, done khi job succeeded. Preview bên phải giữ nguyên khi chuyển trang; màn hình nhỏ xếp dọc.
- Cảnh báo job/result được gộp theo code/message/block_id và chỉ hiện một lần trong chi tiết preview. Giữ polling, khôi phục job, kết quả trên máy chủ và tải TXT/JSON.
- Báo cáo tự tải template, bỏ bước nhập khóa. Frontend gửi marker workspace; proxy server đọc khóa private và thêm X-API-Key. Backend trực tiếp vẫn yêu cầu khóa; shortcut chỉ cho kết nối Tailnet/loopback đã cấu hình, chỉ prefix report, kiểm trước đọc body.

## Kiểm chứng

- Frontend **59 test pass**, TypeScript/Vite build thành công; còn cảnh báo bundle lớn.
- Proxy báo cáo/review, report v2 và unified routing: **51 test pass**.
- Browser trên URL Devspace thực: upload TXT và JPEG nguồn Anh đích Việt. Hai icon loading chuyển thành hai done; TXT dịch giữ IP 192.0.2.15, ảnh dịch “Do you speak English?” thành “Bạn có nói tiếng Anh không?”. Chọn preview chuyển đúng nội dung; cảnh báo OCR xuất hiện một lần, details đóng mặc định.
- Report tự tải 2 template, không còn input password; request browser chỉ có X-Report-Workspace, không có X-API-Key. Qua proxy 200; backend 8131 không khóa vẫn 401. Docker frontend cũng tải templates 200.
- Kiểm browser viewport 900 và 480: không tràn ngang; mobile preview xếp dọc. Test phân trang 5 job và giữ preview khi đổi trang đã pass.

## Giới hạn OCR và triển khai

OCR đang hoạt động; hiệu chỉnh OCR tự động đang tắt trên worker/gateway. Bản dịch sử dụng OCR gốc; giao diện không gọi văn bản đó là “đã sửa”. Cần đối chiếu ảnh mờ hoặc chữ khó đọc; smoke này không đo độ chính xác OCR trên một corpus.

Host Devspace 5173 và Docker frontend 5179 đã cập nhật. Image frontend bất biến: `sha256:8eba2d3a681e161203c5d2ddf709cb8ce33079ccbb6588440db404ed5978e6e4`. Profile/snapshot rollback private: `/home/ict/secai-releases/translation-file-preview-20261005`. Khóa được mount chỉ đọc; không nằm trong image/JS/Git. Không đổi DB hoặc worker.
