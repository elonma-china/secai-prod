# Dataset mở rộng cho benchmark CCU dịch

Bộ dữ liệu riêng theo yêu cầu khách hàng: text, tệp chữ, bảng tính, ảnh và file scan ở nhiều độ dài. Nội dung kỹ thuật và thông tin truy cập đều là dữ liệu tổng hợp, không phải tài liệu thật của khách hàng.

## Ma trận chính

| Nhóm | Định dạng | Ngắn | Vừa | Dài | Mẫu mỗi mức |
|---|---|---|---|---|---|
| Text trực tiếp | `text/*.txt` | 100 từ | 500 từ | 2.000 từ | 5 |
| Tệp text | TXT | 100 từ | 500 từ | 2.000 từ | 5 |
| Tài liệu | DOCX, DOC, PDF có chữ | 1 trang | 5 trang | 20 trang | 5/định dạng |
| Bảng tính | XLSX | 50 dòng | 300 dòng | 1.000 dòng | 5 |
| Ảnh | PNG, JPG | 100 từ | 300 từ | 800 từ | 5/định dạng |
| File scan | PDF scan | 1 trang | 5 trang | 20 trang | 5 |

Ma trận chính: **135 sample** (15 text trực tiếp + 120 tệp/ảnh). Ca khó bổ sung: **15 ảnh** (mờ Gaussian, nghiêng 2°, nền tối) và **5 PDF trộn** (2 trang có chữ + 3 trang scan), báo riêng với nhóm chính. Tổng: **155 sample**.

Ngôn ngữ nguồn gồm Anh, Việt, Pháp; hướng mẫu gồm en→vi, vi→fr, fr→vi, vi→ja. Các ngôn ngữ khác tiếp tục dùng fixture cũ; bộ mới không phải ma trận đầy đủ 18 hướng. Phải kiểm capability/endpoint trước khi chạy tải: v3 tại lần đọc 13/10 không công bố hướng tiếng Anh. `route` trong manifest là endpoint dự kiến, chưa phải bằng chứng sample được production nhận.

## Dùng manifest

- `manifest.json`: `items`, `blocked`, đường dẫn tính từ thư mục chứa manifest (`path_base=manifest_directory`).
- Mỗi sample có ID, format, `group`, `size_class`, nguồn/đích, byte, ký tự/từ, SHA-256, đường dẫn chữ nguồn và 7 `preserve_literals` giả khác nhau giữa các sample.
- `text/*.txt` là payload text trực tiếp, `txt/*.txt` là tệp upload. Runner phải phân biệt hai nhóm.
- `gold/*.txt` là **chữ nguồn**, dùng đối chiếu OCR/literal/độ đầy đủ; chưa phải bản dịch chuẩn để chấm chrF.
- `rendered/*.pdf` dùng kiểm số trang DOCX/DOC bằng LibreOffice. `pages` của TXT/text/XLSX để null; không quy đổi số từ/dòng thành trang. Ảnh tính là một ảnh upload, không phải đo số trang in.
- PDF scan không có lớp chữ; PDF trộn có lớp chữ ở một phần số trang.
- `group=difficult` là nhóm ca khó; `base_group` cho biết images/scan, không trộn vào baseline sạch nếu chưa công bố tỉ trọng.

Mọi đoạn/ô có mã hồ sơ khác nhau để tránh trùng khối hoàn toàn. Nội dung vẫn dùng một số mẫu câu nghiệp vụ chung: cần báo giới hạn này khi diễn giải cache và tính đại diện. Ảnh đều render/suy giảm tổng hợp, chưa đại diện ảnh chụp điện thoại thật hoặc scan do khách hàng cung cấp.

**Script CCU cũ chưa đọc manifest mới.** Bước mở rộng runner theo kế hoạch phải tiêu thụ manifest này; không chạy lại script cũ rồi coi kết quả là đã phủ dataset mới.

## Sinh và kiểm tra

```sh
# Chạy từ root dự án; dùng output MỚI nếu cần sinh lại.
.venv/bin/python -u module-map/translate/fixtures/tools/gen_ccu_fixtures.py \
  --out /tmp/secai-ccu-new-dataset \
  --converter-container secai-opt-converter

.venv/bin/python -m unittest discover \
  -s module-map/translate/fixtures/tools -p 'test_ccu_fixtures.py'
```

Thư viện: Pillow, python-docx, openpyxl, reportlab, pypdf. Converter tùy chọn dùng LibreOffice có sẵn; chỉ xử lý tài liệu tổng hợp trong thư mục/profile tạm riêng, không restart hoặc thay cấu hình container. Nếu thiếu converter, DOC được ghi BLOCKED và số trang DOCX chưa xác minh sẽ để null. Generator từ chối ghi đè thư mục đã có nội dung.

Chưa chạy dịch hoặc tải production bằng bộ dữ liệu này. Chưa có số đo CCU hay chất lượng dịch từ dataset mới.
