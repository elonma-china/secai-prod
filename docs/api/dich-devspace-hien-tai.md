# API dịch đang dùng trên Devspace

Ngày 05/10/2026. Giao diện gồm **Dịch trực tiếp** và **Dịch tệp và ảnh**.

```sh
BASE=http://127.0.0.1:8131
```

Tất cả endpoint dưới đây dùng chung cổng **8131**, không cần token ở tầng API dịch. Trình duyệt gọi cùng đường dẫn qua origin Devspace.

## Ngôn ngữ

Hỗ trợ **18 hướng**: tiếng Việt ↔ Anh, Trung, Nhật, Hàn, Pháp, Đức, Tây Ban Nha, Nga, Ả Rập. Nguồn là tiếng Việt thì chọn một trong 9 ngôn ngữ đích; nguồn khác thì đích là tiếng Việt.

Mã: `vi`, `en`, `ja`, `ko`, `fr`, `de`, `es`, `ru`, `ar`. Tiếng Trung dùng **`zh-Hans` ở v3**, **`zh` ở v2**.

## Dịch trực tiếp — v3 stream

| Method | Endpoint | Công dụng |
|---|---|---|
| GET | `/ai/v3/translate/languages` | Danh sách ngôn ngữ, cặp đang bật và giới hạn |
| POST | `/ai/v3/translate/stream` | Dịch văn bản, nhận SSE |

```sh
curl -N "$BASE/ai/v3/translate/stream" -H 'Content-Type: application/json' \
  -d '{"text":"Kiểm tra nhật ký bảo mật.","source_lang":"vi","target_lang":"en"}'
```

Các field JSON `text`, `source_lang`, `target_lang` đều bắt buộc. Ví dụ SSE, nội dung dịch minh họa:

```text
event: chunk
data: {"delta":"Check the security log.","index":0,"total":1,"warnings":["TARGET_LANGUAGE_UNCERTAIN"]}

event: final
data: {"translated_text":"Check the security log.","source_lang":"vi","target_lang":"en","warnings":["SOURCE_LANGUAGE_UNCERTAIN","TARGET_LANGUAGE_UNCERTAIN"],"metadata":{"request_id":"...","chunks":1,"attempts":1}}

data: [DONE]
```

Ghép `chunk.delta` để hiển thị; **chỉ `final` xác nhận thành công**. Stream theo đoạn đã kiểm tra, không theo từng token. Nếu nhận `event: error` hoặc mất kết nối trước `final`, bản dịch chưa hoàn tất. Cảnh báo `*_LANGUAGE_UNCERTAIN` phản ánh hạn chế kiểm tra ngôn ngữ tự động, không phải lỗi dịch. Dịch text chưa được lưu thành lịch sử trên máy chủ.

## Dịch tệp và ảnh — v2 job

| Method | Endpoint | Công dụng |
|---|---|---|
| POST | `/ai/v2/translate/files` | Upload `.txt`, `.docx`, `.doc` |
| POST | `/ai/v2/translate/images` | Upload `.png`, `.jpg`, `.jpeg`; OCR rồi dịch |
| GET | `/ai/v2/translate/jobs/{job_id}` | Xem trạng thái và tiến độ |
| GET | `/ai/v2/translate/jobs/{job_id}/result` | Lấy kết quả đã lưu |
| DELETE | `/ai/v2/translate/jobs/{job_id}` | Yêu cầu hủy job chưa hoàn tất |

Devspace hiển thị các tệp bằng grid có phân trang (4 tệp/trang). Biểu tượng loading chuyển thành hoàn tất khi job thành công; bấm **Xem trước** để xem bản dịch, tải kết quả và đối chiếu OCR trong khung bên cạnh.

Mỗi POST nhận **một tệp**. Devspace giới hạn **5 tệp mỗi phiên**; cấu hình Docker sau rollout 09/10/2026 nhận tối đa 5 job đang chờ/chạy/hủy mỗi IP, queue toàn hệ thống tối đa 20 job và worker xử lý 1 job. Upload tối đa 10 MiB; không nhận PDF. Khi đạt giới hạn, giữ tệp ở phía gọi và retry theo `Retry-After`.

```sh
curl -sS "$BASE/ai/v2/translate/files" \
  -H 'Idempotency-Key: 0123456789abcdef0123456789abcdef' \
  -F 'file=@./mau.txt' -F 'source_lang=vi' -F 'target_lang=en'
# Với ảnh: đổi /files thành /images và chọn file ảnh.
```

Multipart gồm `file`, `source_lang`, `target_lang`; giao diện luôn chỉ định nguồn. `Idempotency-Key` là 32 ký tự hex thường, giữ nguyên khi retry cùng upload để tránh job trùng.

POST trả **202**, có `job_id`, `status`, `stage`, `progress`; chưa phải kết quả dịch. Dùng ID thực từ response:

```sh
JOB=<job_id>
curl -sS "$BASE/ai/v2/translate/jobs/$JOB"
# Khi status = succeeded:
curl -sS "$BASE/ai/v2/translate/jobs/$JOB/result"
# Hủy:
curl -sS -X DELETE "$BASE/ai/v2/translate/jobs/$JOB"
```

Trạng thái: `queued`, `running`, `cancelling`, `cancelled`, `succeeded`, `failed`. Khi `failed`, đọc `error` trong status.

Result trả `translated_text`, `blocks`, `warnings`; ảnh có thêm `ocr` gồm `raw_text`, `corrected_text`, `edits`, `needs_review`. Kết quả được lưu trên máy chủ **24 giờ sau khi job kết thúc**; tải về để giữ lâu hơn. Chưa xuất lại Word/PDF.

## Lỗi cần xử lý

Lỗi v2/v3 dùng `{"error":{"code":"...","message":"...","request_id":"..."}}`. Trước khi stream mở, lỗi trả HTTP/JSON; sau khi mở, đọc SSE `event: error` dù HTTP là 200.

- `400/422`: input hoặc cặp ngôn ngữ không hợp lệ.
- `413/415`: quá dung lượng hoặc loại tệp không hỗ trợ.
- `429`: quá tần suất/hàng đợi đầy; chờ theo `Retry-After`.
- `409`: kết quả chưa sẵn sàng hoặc thao tác xung đột; `404/410`: job không tồn tại/hết hạn.
- `502/503/504`: lỗi dịch vụ, kiểm tra đầu ra hoặc timeout.
