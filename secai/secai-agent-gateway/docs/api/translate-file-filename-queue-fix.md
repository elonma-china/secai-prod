# Dịch tệp: tên Unicode và gửi nhiều tệp

Kiểm tra ngày 09/10/2026 trên Docker gateway 8131 và mã trong checkout.

## Tên tệp có dấu

Đã tái hiện `UNSUPPORTED_FILE_TYPE` trên API đang chạy với tên `xin chào.txt` được mã hóa trong multipart theo dạng MIME encoded-word của .NET, ví dụ `filename="=?utf-8?B?eGluIGNow6BvLnR4dA==?="`. Trường hợp có thêm `filename*=utf-8''xin%20ch%C3%A0o.txt` cũng bị từ chối: parser hiện chọn giá trị encoded-word chưa giải mã.

Bản sửa giải mã encoded-word bằng thư viện chuẩn Python trước khi kiểm tra tên và đuôi. Tên UTF-8 thông thường tiếp tục được nhận; giữ giới hạn tên, chặn đường dẫn/NUL, allowlist đuôi và kiểm tra chữ ký nội dung. Charset không hợp lệ trả `400 INVALID_REQUEST`. Tệp PDF tiếp tục bị từ chối kể cả tên được mã hóa.

Đã rollout ngày 09/10/2026 vào `secai-translation-gateway`, dùng image hiện hành làm nền và chỉ thay module route. Profile, image cũ và script rollback có kiểm tra ownership được lưu riêng tại `/home/ict/secai-releases/translation-unicode-batch-20261009-194904`. Không đổi worker, model, schema DB hoặc volume.

## QUEUE_FULL khi gửi nhiều tệp

`JobStore.admit()` trả cùng lỗi `429 QUEUE_FULL` khi một trong hai điều kiện xảy ra:

- Số job `queued` toàn hệ thống đạt `TRANSLATE_V2_MAX_QUEUED_JOBS`.
- Số job `queued/running/cancelling` của IP gọi đạt `TRANSLATE_V2_MAX_ACTIVE_PER_IP`.

Cấu hình trước rollout: queue 20, active mỗi IP 2, running toàn hệ thống 1, upload rate 5/phút. Sau rollout, active mỗi IP là 5; các giới hạn khác giữ nguyên. Response 429 có `Retry-After: 60`. Backend C# dùng chung IP gửi ra có thể làm nhiều người dùng cùng chia sẻ giới hạn IP. Proxy có thể tạo tình trạng tương tự; không tin tùy ý header IP do client gửi.

Đề xuất sửa theo thứ tự:

1. Phía gọi giữ danh sách chờ, chỉ đưa tối đa 1–2 tệp vào trạng thái đã được server nhận nhưng chưa kết thúc. Poll job; chỉ gửi tệp kế tiếp khi job trước `succeeded`, `failed` hoặc `cancelled`. Gửi POST tuần tự nhưng không chờ job kết thúc vẫn có thể chạm giới hạn.
2. Với 429, giữ tệp trong danh sách chờ, retry theo `Retry-After` và thêm jitter; giới hạn số lần/thời gian retry, cho phép người dùng hủy. Giữ cùng `Idempotency-Key` cho cùng upload. Dừng retry tự động cho lỗi không tạm thời như 400/415/422. Đây là thay đổi client được đề xuất, chưa thực hiện trong bản sửa tên tệp.
3. Nếu sản phẩm cần nhận ngay một lô 5 tệp, cân nhắc nâng `TRANSLATE_V2_MAX_ACTIVE_PER_IP` từ 2 lên 5 sau khi đánh giá việc chia sẻ IP, quota và timeout. Giữ running=1 trước để không tăng áp lực GPU. Queue 20 là giới hạn khác, không cần tăng chỉ để xử lý lô 5 tệp. Upload rate 5/phút cũng cần tính đến các request retry.
4. Với nhiều người dùng đi qua C# hoặc proxy, cần giới hạn theo danh tính đã xác thực hoặc cấu hình trusted proxy rõ ràng; không thay IP bằng `user_id`/header tùy ý của caller. Có thể bổ sung thông báo để phân biệt queue toàn hệ thống đầy và giới hạn mỗi IP, giữ nguyên mã lỗi hiện có cho tương thích.

Không tăng đồng thời worker concurrency, queue và rate limit để che lỗi. Không tắt GPU lock, quota hoặc timeout. Đã thực hiện nâng active mỗi IP lên 5; danh sách chờ/retry phía C# và phân biệt thông báo lỗi vẫn là đề xuất, chưa thực hiện vì ứng dụng C# không có trong checkout.
