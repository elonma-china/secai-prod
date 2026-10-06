# Thiết kế trang tổng quan A/B và fine-tune thật

Yêu cầu người dùng ngày 2026-10-06: chỉ admin có tab A/B; trang tổng quan dạng bảng; admin duyệt mẫu; mẫu được duyệt hiện ở khu fine-tune; đủ số lượng thì admin chủ động bắt đầu; hiển thị log và tiến trình huấn luyện thật.

Phạm vi: quản lý phản hồi và huấn luyện reward model Qwen3-1.7B. Không tự huấn luyện chatbot chính, không tự kích hoạt model sau huấn luyện. Cách tạo A/B đa dạng được khảo sát riêng trong `docs/reports/ab-generation-research-20261006.md`.

Dùng lại SQLite review hiện có (`REVIEW_DB_PATH`, volume `secai-review-data`), các bảng items/judgments/adjudications/audit_events và pipeline GPU Vast. Không tạo DB mới; không tự di chuyển vào DB IAS ở máy khác khi chưa có schema/kết nối.

Giao diện gồm đúng hai tab: **Phản hồi** và **Fine-tune**. Bảng Phản hồi chỉ có câu hỏi, lựa chọn user, thời gian và thao tác; user ID, campaign/nguồn và metadata nằm trong chi tiết. Khi duyệt, mẫu rời hàng chờ Phản hồi và hiện ở tab Fine-tune. Danh sách phản hồi phải phân trang server, hiển thị câu hỏi, user, lựa chọn và thời gian; không tự tải toàn bộ dataset để phân trang client. Duyệt giữ nguyên phiếu user, admin có quyết định riêng. Phản hồi chưa duyệt, bị loại, tie/both-bad không có winner, QA không được tính nhầm thành cặp production hợp lệ.

Nút fine-tune chỉ hoạt động khi số cặp hợp lệ mới đạt ngưỡng và điều kiện nhóm/split/purpose đạt. Đề xuất ngưỡng mặc định 1.000 cặp mới (cần người dùng chốt); ít nhất ba nhóm độc lập là giới hạn kỹ thuật hiện tại cho QA, không phải tiêu chí đủ chất lượng production. Không tự chạy khi đủ số lượng.

Log/tiến trình phải đến từ worker huấn luyện thật: bước xử lý, epoch, optimizer step, loss, đánh giá, lỗi và artifact. Giai đoạn chưa biết tổng bước hiển thị trạng thái đang xử lý, không tạo phần trăm giả. Model QA hiện tại không tự trở thành model production.

Phân biệt trạng thái phê duyệt và tình trạng sử dụng trong từng job; liên kết mẫu với immutable dataset snapshot. Không cho hai lần bấm hoặc hai admin reserve cùng một batch mới; mất HTTP response phải tra cứu theo idempotency thay vì tạo job mới. Khi thất bại/gián đoạn, không đánh dấu mẫu đã huấn luyện thành công.

Vai trò demo hiện tại dùng tài khoản chung. Để vận hành sản phẩm thật, ánh xạ user_id/admin từ hệ thống xác thực IAS hiện có; không coi dropdown Demo là phân quyền tài khoản thật. Cấu hình tỷ lệ mời A/B toàn hệ thống và cooldown giữ tách biệt với duyệt dữ liệu.

Bổ sung theo phản hồi mới nhất: khi admin nhấn Fine-tune, snapshot các mẫu hợp lệ đang chờ thành **một batch cố định**. Lịch sử fine-tune phải cho xem lại batch, từng sample, quyết định duyệt, các lần chạy, log và kết quả. Batch và training attempt là hai đối tượng khác nhau; retry không tạo lại nhóm mẫu hoặc làm mất lịch sử thất bại.
