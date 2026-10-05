# API dịch — bản cũ và bản cập nhật

Đối chiếu mã nguồn ngày 05/10/2026. Ví dụ response là minh họa contract, không phải số liệu chất lượng model.

## 1. Chọn API

| Nhu cầu | Method và endpoint | Đầu vào / đầu ra |
|---|---|---|
| Dịch text cũ, gồm Anh–Việt | `POST /ai/translate` | JSON → JSON ngay |
| Dịch text cũ dạng stream | `POST /ai/translate/stream` | JSON → SSE |
| Dịch text 18 hướng (16 hướng mới + Anh–Việt) | `POST /ai/v3/translate` | JSON → JSON ngay |
| Dịch v3 dạng stream | `POST /ai/v3/translate/stream` | JSON → SSE từng đoạn đã kiểm tra |
| Xem ngôn ngữ/cặp đang bật | `GET /ai/v3/translate/languages` | JSON capabilities |
| Upload tài liệu | `POST /ai/v2/translate/files` | Multipart → `202` với job ID |
| Upload ảnh và OCR rồi dịch | `POST /ai/v2/translate/images` | Multipart → `202` với job ID |
| Xem tiến độ | `GET /ai/v2/translate/jobs/{job_id}` | JSON trạng thái |
| Lấy kết quả đã lưu | `GET /ai/v2/translate/jobs/{job_id}/result` | JSON khi job thành công |
| Hủy job chưa hoàn tất | `DELETE /ai/v2/translate/jobs/{job_id}` | `202`; job thành công/thất bại trả `409` |

Các API dịch trên không yêu cầu token/API key ở tầng endpoint; có giới hạn dung lượng và tần suất. `job_id` cho phép đọc/hủy job: cần giữ riêng, không đưa vào log công khai.

Ví dụ gọi trực tiếp trên server hiện tại:

```sh
BASE=http://127.0.0.1:8131       # cổng chung cho toàn bộ API dịch
```

Qua Devspace nội bộ, dùng origin `http://100.74.146.12:5173` làm BASE; static proxy chuyển tất cả API về gateway 8131. Địa chỉ/port có thể đổi theo deployment.

## 2. Dịch text cũ

```sh
curl -sS "$BASE/ai/translate" -H 'Content-Type: application/json' \
  -d '{"text":"Check the security log.","source_lang":"en","target_lang":"vi"}'
```

Response `200`:

```json
{"translated_text":"Kiểm tra nhật ký bảo mật.","translation":"Kiểm tra nhật ký bảo mật.","metadata":{}}
```

`metadata` ở ví dụ được rút gọn. Text còn nhận các tên legacy `message`, `question`, `prompt`, `content`; nên gửi thống nhất `text`. `language` là alias của **ngôn ngữ đích** khi thiếu `target_lang`.

Muốn SSE, đổi endpoint thành `/ai/translate/stream`, thêm `curl -N`. Event `token` có `delta`; event `final` có `content`, `translated_text`, `translation`, `metadata`; kết thúc bằng `data: [DONE]`. Dùng `final` làm kết quả hoàn chỉnh vì token giữa luồng có thể còn placeholder kỹ thuật.

## 3. Dịch text v3: 18 hướng

Tiếng Việt hai chiều với 9 ngôn ngữ: **Anh, Trung giản thể, Nhật, Hàn, Pháp, Đức, Tây Ban Nha, Nga, Ả Rập**. Tổng cộng 18 hướng, gồm 16 hướng ban đầu và 2 hướng Anh–Việt bổ sung.

| Ngôn ngữ | Mã v3 |
|---|---|
| Việt | `vi` |
| Anh | `en` |
| Trung giản thể | `zh-Hans` (nhận alias `zh`) |
| Nhật / Hàn | `ja` / `ko` |
| Pháp / Đức / Tây Ban Nha | `fr` / `de` / `es` |
| Nga / Ả Rập | `ru` / `ar` |

```sh
curl -sS "$BASE/ai/v3/translate/languages"
curl -sS "$BASE/ai/v3/translate" -H 'Content-Type: application/json' \
  -d '{"text":"Kiểm tra nhật ký bảo mật.","source_lang":"vi","target_lang":"ja"}'
```

Response `200`:

```json
{
  "translated_text":"セキュリティログを確認してください。",
  "source_lang":"vi","target_lang":"ja","warnings":[],
  "metadata":{"request_id":"example-1","chunks":1,"attempts":1}
}
```

Ba field request đều bắt buộc; không nhận field thừa. Một đầu phải là `vi`, hai đầu phải khác nhau. V3 nhận Anh–Việt khi server bật `MULTILANG_ENABLE_EN=true`; không nhận `auto` hay `zh-Hant`. API Anh–Việt cũ tiếp tục hoạt động. Chỉ gọi cặp có `enabled=true` từ capabilities. Model chuyên dụng hiện tại: `tencent/Hy-MT2-7B`. Giới hạn text mặc định 24.000 ký tự; v3 có SSE qua `/stream`, chưa có API lưu/lấy lại bản dịch.

`warnings` có thể chứa `SOURCE_LANGUAGE_UNCERTAIN` hoặc `TARGET_LANGUAGE_UNCERTAIN`: cần kiểm tra bản dịch, nhất là câu rất ngắn.

### Stream v3 (thử nghiệm)

```sh
curl -N "$BASE/ai/v3/translate/stream" -H 'Content-Type: application/json' \
  -d '{"text":"Kiểm tra nhật ký bảo mật.","source_lang":"vi","target_lang":"en"}'
```

Tất cả API dịch cũ, v2 và v3 được gọi qua cổng chung **8131**. Gateway chuyển tới service nội bộ; client không cần biết các cổng upstream.

- `event: chunk`: `{delta,index,total,warnings}`; index từ 0, delta đã khôi phục và kiểm tra IP/CVE/literal, giữ separator gốc. Ghép các delta theo thứ tự để hiển thị bản tạm.
- `event: final`: response đầy đủ cùng schema API JSON; chỉ event này xác nhận toàn bộ request thành công.
- `event: error`: `{error:{code,message,request_id}}`; nếu một đoạn sau lỗi, bỏ bản tạm hoặc đánh dấu chưa hoàn tất. Không có final thành công.
- Kết thúc: `data: [DONE]`. Lỗi trước đoạn đầu trả HTTP/JSON thông thường; sau khi stream mở, HTTP đã là 200 nên phải đọc event error.

Đây là **stream theo đoạn đã kiểm tra**, không phải từng token model. Văn bản ngắn chỉ có một đoạn nên gần như không có lợi về thời gian thấy kết quả. Client ngắt kết nối sẽ hủy xử lý tiếp ở gateway. Devspace chỉ có hai chế độ “Dịch trực tiếp” và “Dịch tệp và ảnh”; dịch text dùng v3 stream.

## 4. Dịch file và ảnh mới

- File: `.txt`, `.docx`, `.doc`; `.doc` cần converter đang hoạt động. Không nhận PDF.
- Ảnh: `.png`, `.jpg`, `.jpeg`; thực hiện OCR, hiệu chỉnh và dịch.
- Mã v2: `vi`, `en`, `zh`, `ja`, `ko`, `fr`, `de`, `es`, `ru`, `ar`. Khác v3, mã Trung ở v2 là `zh`.
- Dịch hai chiều giữa `vi` và 9 ngôn ngữ còn lại; cùng ngôn ngữ được giữ nguyên. Không hỗ trợ dịch giữa hai ngôn ngữ khác nhau nếu cả hai đều khác `vi`.
- Multipart: `file` và `target_lang` bắt buộc; `source_lang` mặc định `auto`. Nên chỉ định nguồn khi biết rõ để tránh lỗi nhận diện.
- Devspace giới hạn 5 tệp mỗi phiên, nguồn tiếng Việt chọn 9 đích; nguồn khác chỉ chọn đích tiếng Việt. Mỗi API upload nhận một tệp.
- Mặc định: file tối đa 10 MiB; ảnh tối đa 20 triệu pixel, mỗi cạnh tối đa 10.000 pixel.

```sh
curl -sS "$BASE/ai/v2/translate/files" \
  -F 'file=@./mau.txt' -F 'source_lang=vi' -F 'target_lang=ja'

curl -sS "$BASE/ai/v2/translate/images" \
  -F 'file=@./anh.png' -F 'source_lang=en' -F 'target_lang=vi'
```

Response `202` có `job_id`, `status`, `stage`, `progress`, `source_lang`, `target_lang`, `warnings`, `error`, các timestamp và `status_url`/`result_url`. Lấy ID từ response rồi polling:

```sh
JOB=0123456789abcdef0123456789abcdef  # thay bằng job_id thực
curl -sS "$BASE/ai/v2/translate/jobs/$JOB"
curl -sS "$BASE/ai/v2/translate/jobs/$JOB/result"
```

`status`: `queued`, `running`, `cancelling`, `cancelled`, `succeeded`, `failed`. Chỉ lấy result khi `succeeded`; khi `failed` đọc `error` trong status. Không tự gửi lại upload mỗi lần polling.

Response result `200`, ví dụ file:

```json
{
  "job_id":"0123456789abcdef0123456789abcdef","kind":"file","status":"succeeded",
  "source_lang":"en","target_lang":"vi","translated_text":"Nội dung",
  "blocks":[{"id":"b000001","kind":"paragraph","order":0,
    "source_text":"Content","translated_text":"Nội dung","metadata":{}}],
  "warnings":[],"ocr":null
}
```

Ảnh trả `kind:"image"` và `ocr:{raw_text,corrected_text,edits,needs_review}`; `needs_review=true` cần kiểm tra OCR. Kết quả là JSON/text theo block, chưa xuất lại Word/PDF.

Job/kết quả được lưu trong SQLite và artifact storage, mặc định giữ kết quả **24 giờ sau khi kết thúc**, sau đó hết hạn và được dọn. Client cần tải về nếu muốn giữ lâu hơn. Có thể gửi `Idempotency-Key` gồm đúng 32 ký tự hex thường để tránh tạo job trùng khi retry cùng payload; khác payload với cùng key trả `409`.

## 5. Xử lý lỗi chính

V2/v3 dùng envelope `{"error":{"code":"...","message":"...","request_id":"..."}}`; client xử lý theo `code`.

| HTTP | Ý nghĩa thường gặp |
|---|---|
| `400` | Sai input/ngôn ngữ/cặp (`INVALID_LANGUAGE` ở v2; `UNSUPPORTED_LANGUAGE`/`UNSUPPORTED_PAIR` ở v3) |
| `403` | V3: cặp chưa bật (`LANGUAGE_DISABLED`) |
| `404` / `410` | V2: job không tồn tại / hết hạn |
| `409` | V2: result chưa sẵn sàng, job lỗi/đã hủy hoặc idempotency conflict |
| `413` / `415` | Quá giới hạn / sai loại file |
| `422` | Dữ liệu không xử lý được hoặc sai ngôn ngữ nguồn |
| `429` | Quá tần suất/hàng đợi đầy; chờ theo `Retry-After` |
| `502` / `503` / `504` | Model/đầu ra không hợp lệ, dịch vụ chưa sẵn sàng hoặc timeout |

V2 đã nhận `202` vẫn có thể thất bại khi xử lý; GET status trả `200` với `status:"failed"`. Không coi `202` là dịch thành công. API legacy có thể trả lỗi dạng `detail`, không áp dụng envelope v2/v3 cho mọi bản cũ.

Chi tiết: [contract file/ảnh v2](translate-v2-contract.md), [contract text v3](multilang-contract.md). Schema v3: `GET /ai/v3/translate/openapi.json`.
