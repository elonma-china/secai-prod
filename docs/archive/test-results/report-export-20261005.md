# Xuất báo cáo TXT / DOCX / PDF — 05/10/2026

## Chức năng

Tab **Báo cáo thông minh → Theo mẫu** có Tải TXT, Tải Word và Tải PDF cho báo cáo đã lưu, lấy đúng revision đang xem. Endpoint `GET /ai/v2/reports/{id}/export?format=txt|docx|pdf&revision=N` dùng chung gateway 8131. Bỏ revision để xuất bản hiện tại tại thời điểm đọc. Xuất không gọi model, không tạo revision hay đổi DB.

TXT là UTF-8 plain text; DOCX/PDF giữ tiêu đề, đoạn/danh sách, code và bảng cơ bản. PDF nhúng DejaVu Sans hỗ trợ tiếng Việt, lặp tiêu đề bảng nhiều trang. Không tải ảnh/tài nguyên ngoài hoặc thực thi HTML. Định dạng inline đậm/nghiêng/hyperlink được làm phẳng; bảng quá nhiều cột có thể chật. Tab tạo báo cáo legacy vẫn dùng hộp thoại in cũ; chức năng tải file mới nằm trong tab Theo mẫu.

## Kiểm chứng

- **1745 test backend passed, 7 deselected**, 551.46 giây. Bảy test cần cây Bughunt/PoC ngoài checkout được loại; không coi là toàn bộ suite pass. 64 warning hiện có (operation IDs/SyntaxWarning).
- **63 test frontend passed**, TypeScript/Vite build thành công; còn cảnh báo kích thước bundle.
- **55 test liên quan export/report/proxy/unified routing passed** sau khi chốt code; export mới có 7 test gồm lịch sử/auth/dữ liệu/bảng và PDF dài.
- Review độc lập không thấy lỗi chặn bàn giao. Stress PDF kiểm bảng có cell dài, bảng rộng, code dài, HTML literal và không tải ảnh từ URL.
- Browser tại URL Devspace thực đã tải TXT/DOCX/PDF revision 2, sau đó PDF revision 1; cả bốn download thành công, tên file đúng revision.
- TXT khớp plaintext từ nội dung đã lưu. DOCX mở được bằng python-docx; PDF trích xuất giữ các từ trong cả hai revision. PDF thực tải từ giao diện và mẫu bảng nhiều trang đã render PNG và kiểm trực quan: tiếng Việt đúng, không cắt/chồng chữ, tiêu đề bảng lặp.
- Export không làm thay đổi lịch sử đã lưu; backend trực tiếp thiếu key trả 401 ngay cả khi query sai. 422 cho format/revision không hợp lệ; 404 cho báo cáo/bản sửa không có.

## Triển khai

Report backend và Docker frontend dùng image bất biến `sha256:ac510772daebc9b37afe7d49005dfa023b6bea3aa66d07b156cdf7abc816b0ae`; host frontend 5173 build mới. Giữ nguyên gateway 8131, private env/key, volume/DB và worker. Font/license đi cùng image; Dockerfile chuẩn cài fonts-dejavu-core. Private profile/snapshot rollback và SQLite backup trước đổi image nằm tại `/home/ict/secai-releases/report-export-20261005`. Canary độc lập đã kiểm tra cả ba file trước rollout và được dọn sau kiểm chứng.
