# API báo cáo — bản cũ và bản cập nhật

Đối chiếu mã nguồn ngày 05/10/2026. Ví dụ response minh họa; nội dung báo cáo phụ thuộc dữ liệu và model.

## 1. Khác biệt chính

| Nội dung | API cũ | API cập nhật v2 |
|---|---|---|
| Tạo báo cáo | Gửi dữ liệu và danh sách mục mỗi lần | Chọn template đã lưu trong DB |
| Cập nhật mẫu | Client tự gửi lại các mục | Thêm/import/cập nhật template qua API |
| Sửa bằng prompt | Chưa có endpoint edit riêng | Sửa báo cáo theo `instruction` |
| Lưu kết quả | Không có report ID và API lịch sử riêng | Lưu báo cáo và từng revision trong SQLite |
| Đầu ra | JSON hoặc SSE | JSON chứa Markdown, không có SSE |
| Xác thực endpoint | Không yêu cầu report API key | Bắt buộc `X-API-Key` |

Template mới có hiệu lực trong DB, không cần huấn luyện lại model để đổi mẫu. Chưa có endpoint xuất Word/PDF.

Ví dụ gọi trực tiếp trên server hiện tại:

```sh
BASE=http://127.0.0.1:8131       # cổng chung cho báo cáo cũ và v2
# REPORT_API_KEY lấy từ cấu hình riêng của deployment, không đưa vào source/frontend.
```

Devspace nội bộ có origin `http://100.74.146.12:5173`; proxy chuyển toàn bộ API tới cổng chung 8131. Ví dụ dưới dùng backend trực tiếp và key của operator. Địa chỉ/port có thể đổi theo deployment.

## 2. API báo cáo cũ

| Method | Endpoint | Response `200` |
|---|---|---|
| POST | `/ai/generate-report` | `{report, metadata}` |
| POST | `/ai/generate-report/stream` | SSE, `final` chứa `content`, `report`, `metadata` |
| POST | `/ai/generate-statistical-report` | `{report_title, report_summary, report_detail, report, statistical_report, metadata}` |
| POST | `/ai/generate-statistical-report/stream` | SSE với các field thống kê trong `final`, thêm `content` |

Tạo báo cáo thường:

```sh
curl -sS "$BASE/ai/generate-report" -H 'Content-Type: application/json' \
  -d '{"logs":"Máy 192.0.2.15 ghi nhận 3 lần đăng nhập thất bại; tài khoản đã khóa.",
       "sections":["Tóm tắt","Chi tiết","Khuyến nghị"]}'
```

```json
{"report":"## Tóm tắt\nGhi nhận 3 lần đăng nhập thất bại...","metadata":{}}
```

`metadata` ở ví dụ được rút gọn. Có thể gửi `text` thay `logs`; thiếu `sections` mặc định `overview`, `details`, `recommendations`.

Báo cáo thống kê:

```sh
curl -sS "$BASE/ai/generate-statistical-report" -H 'Content-Type: application/json' \
  -d '{"from_date":"2026-10-01","to_date":"2026-10-05",
       "summary":"Một máy đã được quét.",
       "detail":[{"name":"web-01","ip":"192.0.2.15","scaned":"2026-10-05",
         "cves":[{"cve":"CVE-2024-1234","severity":"High","description":"Mô tả từ kết quả quét."}]}]}'
```

`detail` nhận chuỗi legacy hoặc mảng máy/CVE. Với mảng, `report_detail` là bảng Markdown dựng bằng code gồm tên máy, IP, ngày quét, lỗ hổng, mức độ, mô tả; các field tổng quan do model tạo. Field `scaned` giữ tên legacy (cũng nhận `scan_date`).

Muốn stream, dùng endpoint `/stream` tương ứng và `curl -N`. Đọc event `token` (`delta`) để hiển thị, `final` làm kết quả; luồng kết thúc bằng `data: [DONE]`.

## 3. API template và báo cáo v2

Prefix: `/ai/v2/reports`. Request trực tiếp tới backend cần `X-API-Key: <REPORT_API_KEY>`. Devspace nội bộ tự kết nối workspace qua proxy, không có ô nhập khóa; proxy đọc khóa private trên server và thêm header khi gọi backend. Server phải bật `REPORT_FEATURE_ENABLED=true`. Key cấp quyền với toàn bộ template/báo cáo của workspace, chưa phân quyền theo từng người dùng.

| Method | Đường dẫn sau prefix | Chức năng |
|---|---|---|
| GET | `/templates` | Danh sách template |
| POST | `/templates` | Tạo template |
| POST | `/templates/import` | Import `.docx`, `.md`, `.txt` thành bản nháp |
| PATCH | `/templates/{template_id}` | Cập nhật mẫu với `expected_version` |
| POST | `/generate` | Tạo và lưu báo cáo, revision 1 |
| GET | `/{report_id}` | Lấy bản hiện tại đã lưu |
| POST | `/{report_id}/edit` | Sửa bằng prompt, lưu revision mới |
| GET | `/{report_id}/revisions` | Lịch sử nội dung và yêu cầu chỉnh sửa |

Các endpoint trên trả `200` khi thành công, kể cả create/generate. Hiện chưa có endpoint liệt kê/xóa báo cáo; client cần giữ `report_id` để mở lại.

### Tạo hoặc cập nhật template

```sh
curl -sS "$BASE/ai/v2/reports/templates" \
  -H "X-API-Key: $REPORT_API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Báo cáo sự cố","description":"Tổng hợp sự cố bảo mật",
       "sections":["Tóm tắt","Chi tiết","Khuyến nghị"],
       "instructions":"Viết ngắn gọn, chỉ dùng dữ kiện được cung cấp.","example":""}'
```

Template trả về `{id,name,description,sections,instructions,example,version,created_at,updated_at}`. Giữ `id` để generate, `version` để cập nhật.

`name` và `sections` bắt buộc; các chuỗi còn lại mặc định rỗng. Giới hạn: tên 120 ký tự, mô tả 1.000, 1–20 mục (mỗi mục 1–120 ký tự), instructions 4.000, example 40.000.

PATCH gửi đầy đủ field template như POST và thêm `expected_version:1`; đây không phải patch từng field. Version cũ trả `409`: tải template mới rồi áp dụng lại thay đổi.

Import mẫu:

```sh
curl -sS "$BASE/ai/v2/reports/templates/import" \
  -H "X-API-Key: $REPORT_API_KEY" -F 'file=@./mau-bao-cao.docx'
```

Giới hạn 2 MiB. Response là draft `{name,description,sections,instructions,example}`: kiểm tra/sửa rồi POST `/templates` để lưu. Import chưa tự tạo template trong DB.

### Tạo và lưu báo cáo

```sh
TEMPLATE_ID='<id trả về khi tạo template>'
curl -sS "$BASE/ai/v2/reports/generate" \
  -H "X-API-Key: $REPORT_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"template_id\":\"$TEMPLATE_ID\",\"data\":\"Máy 192.0.2.15 ghi nhận 3 lần đăng nhập thất bại; tài khoản đã khóa.\"}"
```

Response minh họa:

```json
{
  "id":"report-id","template_id":"template-id","template_version":1,"revision":1,
  "content":"## Tóm tắt\nMáy 192.0.2.15 ghi nhận 3 lần đăng nhập thất bại.\n\n## Chi tiết\nTài khoản đã khóa.\n\n## Khuyến nghị\nCần bổ sung dữ liệu để đưa ra khuyến nghị cụ thể.",
  "created_at":"2026-10-05T00:00:00Z"
}
```

`data` là chuỗi, tối đa 20.000 ký tự. `content` là Markdown tiếng Việt. Report ghi nhận version template lúc tạo; sửa template sau đó không tự sửa báo cáo cũ.

### Chỉnh sửa bằng prompt và mở lại

```sh
REPORT_ID='<id trả về khi generate>'
curl -sS "$BASE/ai/v2/reports/$REPORT_ID/edit" \
  -H "X-API-Key: $REPORT_API_KEY" -H 'Content-Type: application/json' \
  -d '{"instruction":"Rút ngắn toàn bộ báo cáo, giữ nguyên IP và số liệu.","expected_revision":1}'

curl -sS "$BASE/ai/v2/reports/$REPORT_ID" -H "X-API-Key: $REPORT_API_KEY"
curl -sS "$BASE/ai/v2/reports/$REPORT_ID/revisions" -H "X-API-Key: $REPORT_API_KEY"
```

Edit trả cấu trúc report như trên với `revision:2`. Prompt tối đa 4.000 ký tự. Lịch sử là mảng `{revision,content,created_at,instruction}`; revision 1 có instruction rỗng. `expected_revision` chống ghi đè khi sửa đồng thời.

**Báo cáo, nguồn dữ liệu và lịch sử sửa được lưu trong SQLite**, không bỏ đi sau khi generate. Deployment cần giữ persistent volume. Nếu sinh/sửa không qua validation thì không lưu bản lỗi và không thay bản đang có.

## 4. Lỗi và giới hạn cần biết

V2 thường trả `{"detail":"..."}`; lỗi schema có `detail` dạng mảng.

| HTTP | Ý nghĩa / cách xử lý |
|---|---|
| `401` | Thiếu/sai report API key |
| `404` | Feature chưa bật/chưa cấu hình key, hoặc template/report không tồn tại |
| `409` | Template version/report revision đã đổi; tải bản mới trước khi sửa lại |
| `413` | Import quá dung lượng |
| `422` | Sai input/loại file, dữ liệu bị guard từ chối, hoặc bản nháp vẫn không đạt validation sau retry |
| `502` / `503` | Model lỗi, đầu ra rỗng/bị cắt, hoặc external LLM bị chặn trong air-gap |

Validator kiểm cấu trúc mục và một số dữ kiện như IP/CVE/URL/số liệu; chưa bảo đảm mọi ý nghĩa đúng. Cần chuyên gia đọc lại trước khi sử dụng chính thức. Khi edit trả `422`, báo cáo đã lưu vẫn giữ nguyên.

Chi tiết: [contract report v2](report-mvp.md). API cũ và v2 cùng tồn tại; tích hợp template/chỉnh sửa/lịch sử nên dùng v2.
