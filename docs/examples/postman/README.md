# Postman — API dịch Docker 8131

Import `secai-translation-8131.postman_collection.json` bằng **Postman → Import → Files**.
Collection có 14 request, dùng `base_url = http://123.16.179.18:8131` (IP public xác định ngày 09/10/2026). Có thể đổi địa chỉ trong tab **Variables** của collection.

1. Gửi **Health Docker gateway**, rồi **Các cặp ngôn ngữ v3 đang bật** để kiểm tra kết nối.
2. Dịch text: chọn request JSON hoặc SSE trong folder 02. Sửa `text`, `source_lang`, `target_lang` ở Variables; mặc định Việt → Anh. Trung dùng `zh-Hans` ở v3, `zh` ở v2. Một đầu phải là tiếng Việt khi dịch giữa hai ngôn ngữ khác nhau.
3. Upload: mở folder 03, vào **Body → form-data → file** và chọn tệp trên máy. Có thể dùng `aaa.txt` đi kèm; đặt nguồn `en`, đích `vi` cho tệp này. File hỗ trợ TXT/DOCX/DOC; ảnh hỗ trợ PNG/JPG/JPEG. Không tự đặt header Content-Type: Postman cần tự tạo multipart boundary.
4. Upload thành công trả 202 và tự lưu `job_id` trong Variables. Gửi **Xem tiến độ** nhiều lần đến khi `succeeded`, sau đó **Lấy kết quả**. 202 chỉ xác nhận nhận job. Nếu `failed`, xem `error.code`; lấy kết quả quá sớm trả 409.
5. **Hủy job** chỉ gửi khi muốn hủy job hiện tại. Job đã thành công hoặc thất bại trả 409.

Folder 05 có hai request TXT tự chứa nội dung, không cần chọn tệp: tên `aaa.txt` đúng phải trả 202; tên chứa thêm dấu ngoặc kép phải trả 415 `UNSUPPORTED_FILE_TYPE`. Request đúng tạo job thật và lưu `job_id`, có thể hủy qua folder 04 sau khi thử.

Các script kiểm tra response xuất hiện trong **Test Results**. Không chạy toàn bộ collection một lượt: nó chứa cả request hủy và request gây lỗi có chủ đích. Timeout Postman nên đặt 180000 ms cho dịch. Stream phải có `event: final`, không có `event: error`; HTTP 200 riêng lẻ chưa xác nhận dịch thành công.

`file_idempotency_key` và `image_idempotency_key` tự sinh lần đầu, gồm 32 ký tự hex thường và gửi qua header `Idempotency-Key`. Giữ nguyên key khi retry cùng upload. Khi chọn tệp mới hoặc đổi ngôn ngữ, xóa giá trị biến key tương ứng trước khi gửi để script sinh key mới; dùng lại key với payload khác sẽ trả 409.

`job_id` là quyền truy cập job: giữ riêng, không chia sẻ collection đã lưu job thật. Bộ file ban đầu không chứa job ID, token hay secret. API dịch hiện không yêu cầu token ở tầng endpoint.

Đã kiểm tra kết nối health/capabilities và hai request chẩn đoán qua IP public từ máy server; chưa xác nhận kết nối từ mạng bên ngoài. Các job thử đã hủy. Việc kiểm tra này không đánh giá chất lượng bản dịch text/OCR.
