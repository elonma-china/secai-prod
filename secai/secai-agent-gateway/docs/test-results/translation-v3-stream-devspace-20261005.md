# Kiểm chứng dịch và routing Devspace — 05/10/2026

- V3 thêm Anh ↔ Việt: 18 hướng, 10 ngôn ngữ; Devspace mặc định dùng `/ai/v3/translate/stream`.
- Chỉ còn Dịch trực tiếp / Dịch tệp và ảnh. Batch mới tối đa 5 tệp; nguồn Việt chọn 9 đích, nguồn khác chỉ đích Việt. Job cũ được giữ khi tải lại.
- Cảnh báo kiểm tra ngôn ngữ tự động và mã yêu cầu nằm trong chi tiết đóng mặc định; không thay đổi cảnh báo hoặc kiểm tra an toàn API.
- Gateway client chung 8131, giữ dịch vụ nội bộ và DB hiện có. Report không có key trả 401; review backend không có Bearer trả 401; proxy Devspace nội bộ dùng tài khoản admin chung trả 200.

## Kiểm thử

- Backend functional suite: **1734 passed, 7 deselected**, 548.77 giây. Bảy test phụ thuộc cây bug-bounty/arsenal ngoài checkout được loại; không gọi đây là toàn bộ suite pass.
- Sau review sửa cleanup khi disconnect: **95 passed** cho multilang, security, unified routing, review proxy. Regression AnyIO cancellation trước sửa thất bại, sau sửa pass.
- Frontend: **55 passed**; TypeScript/Vite build thành công. Vite còn cảnh báo bundle lớn.
- Browser trên `https://intake-sofa-width-zshops.trycloudflare.com/`: xác nhận hai chế độ, asset mới, POST v3 stream thành công, IP 192.0.2.15 giữ nguyên và details đóng. File panel nguồn Nhật chỉ có đích Việt, nguồn Việt có 9 đích. Review proxy admin 200.
- Report đã lưu đọc qua 8131 giữ revision 2 và hai revisions. Upload TXT qua 8131 nhận 202, polling succeeded và result được lưu/đọc lại 200.

## Đo stream

Một mẫu tổng hợp khoảng 200 từ, cùng model Hy-MT và chunk budget 128: JSON 21.64 giây; SSE đoạn đầu 11.42 giây, hoàn tất 21.52 giây, hai chunk khớp final và giữ IP/CVE. Đây là smoke đơn mẫu, không phải benchmark chất lượng. Văn bản một đoạn gần như không có lợi về thời gian thấy kết quả; stream chưa theo token.

## Triển khai và rollback

Gateway chính, translation gateway và translation frontend đã cập nhật image bất biến `sha256:3e1d38125557f4b8894b231bdab4f80e184b1fdeb81576e7132a9eafd9744970`; frontend host 5173 build mới. Upstream frontend đều 8131; gateway giữ report và translation nội bộ. Snapshot/profile rollback riêng nằm ngoài Git tại `/home/ict/secai-releases/translation-v3-en-stream-20261005`. Không đổi volume hoặc xóa DB. Profile rollback gateway/report trước rollout và snapshot frontend được giữ.

## Sửa ghi chú sau dịch

Ghi chú màu vàng về kiểm tra ngôn ngữ cũng được chuyển vào `Chi tiết bản dịch`, đóng mặc định. Bên ngoài chỉ còn trạng thái `Dịch hoàn tất.` sau event final; lỗi thật vẫn hiển thị riêng. Regression test mới thất bại trước sửa, pass sau sửa; 55 test frontend pass và build thành công. Docker frontend dùng image `sha256:a8e3cbe5f2068670699b1953976eb969a48bacf550d5dd3c6aaa1d19e5897716`, có profile rollback riêng trong thư mục release private.
