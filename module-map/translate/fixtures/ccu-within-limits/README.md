# Dataset lớn trong giới hạn production — 13/10/2026

Đây là bộ dùng cho phép đo mới qua gateway `127.0.0.1:8131`. Bộ `../ccu-expanded/` nhỏ hơn được giữ nguyên để đối chiếu. Nội dung tổng hợp, thông tin truy cập giả; gold là chữ nguồn để kiểm OCR/literal, chưa phải bản dịch chuẩn.

| Nhóm | Ngắn | Vừa | Dài | Mẫu mỗi mức/định dạng |
|---|---|---|---|---|
| Text trực tiếp v3 | 200 từ | 350 từ | 450 từ | 5 |
| TXT upload | 2.000 từ | 5.000 từ | 10.000 từ | 5 |
| DOCX, DOC, PDF có chữ | 10 trang | 30 trang | 60 trang | 5 |
| XLSX | 1.000 dòng | 3.000 dòng | 4.900 dòng | 5 |
| PNG, JPG | 500 từ | 1.000 từ | 1.500 từ | 5 |
| PDF scan | 10 trang | 30 trang | 60 trang | 5 |

Tổng 135 mẫu chính và 20 ca khó: 15 ảnh mờ/nghiêng/nền tối, 5 PDF 30 trang trộn 15 trang chữ và 15 trang scan. Tổng **155 sample**, ~201 MB không tính gold/render.

## Vì sao text trực tiếp nhỏ hơn TXT

Capability v3 công bố 24.000 ký tự và 8.192 source token, nhưng cấu hình specialist thực tế chỉ cho 16 chunk ×128 token/chunk (gồm prompt). Một mẫu dưới giới hạn ký tự vẫn có thể bị `TEXT_TOO_LONG` vì quá nhiều chunk. Đã kiểm cả 15 text bằng tokenizer và cách chia chunk của service đang chạy: tối đa 1.122 source token, 15 chunk trong snapshot `text-token-validation.json`. Các hướng text dùng vi→fr, fr→vi, vi→ja, vi→de; không dùng tiếng Anh ở v3 vì hướng đó chưa bật.

## Giới hạn và kiểm tra

Snapshot production: upload 50 MiB, ảnh ≤20 triệu pixel/cạnh ≤10.000px, nội dung ≤3 triệu ký tự, XLSX ≤20.000 ô, PDF ≤600 trang/OCR ≤200 trang. Trong bộ này: tệp lớn nhất 16.890.352 byte, ảnh lớn nhất 9.142.560 pixel, XLSX dài có 19.620 ô, nội dung lớn nhất 776.212 ký tự. DOC/DOCX đã render bằng LibreOffice để xác minh đúng 10/30/60 trang.

`validation.json` kiểm mọi đường dẫn, SHA-256, chữ nguồn/literal, số trang/lớp chữ, ô XLSX, pixel, giới hạn ZIP. Đây là kiểm đầu vào, không chứng minh dịch vụ dịch thành công. Runner kiểm từng sample thật trước khi đưa vào tải; sample lỗi được ghi đầy đủ và coverage của kịch bản sẽ là PARTIAL nếu không phủ đủ.

Ảnh dài được tăng chiều cao và giữ font ≥24px, thay vì nhét 1.500 từ vào ảnh thấp và giảm chữ. Mỗi sample có 7 literal riêng. Đoạn/ô tài liệu có mã khác nhau để giảm memo; vẫn dùng mẫu câu chung, nên cần nêu hạn chế tính đại diện/cache khi báo kết quả.

## Công cụ

Công cụ sinh mẫu cần Pillow, python-docx, reportlab, pypdf và openpyxl. Trong môi trường chuẩn bị bộ này đã bổ sung `openpyxl==3.1.5` vào `.venv`; không thay dependency của gateway.

```sh
# Bổ sung thư viện bảng tính nếu môi trường chưa có.
.venv/bin/python -m pip install openpyxl==3.1.5

# Sinh vào thư mục MỚI; cần LibreOffice local hoặc converter tạm riêng có --init.
.venv/bin/python -u module-map/translate/fixtures/tools/gen_ccu_fixtures.py \
  --profile within-limits --out /tmp/secai-ccu-new-dataset

.venv/bin/python -m unittest discover \
  -s module-map/translate/fixtures/tools -p 'test_ccu*.py'
```

Nếu dùng Docker converter, tạo tác vụ tạm với `--init`, giữ giới hạn PID/memory và chỉ trỏ `--converter-container` vào container đó; không dùng converter production để sinh sample. Không có LibreOffice/converter thì DOC ghi BLOCKED và DOCX chưa xác minh số trang. Generator từ chối ghi đè output.

Runner mới: `tools/bench_ccu_manifest.py`, dùng `--manifest` và kiểm tra production bằng cờ chủ động `--allow-production`. Kết quả phép đo ở `../../optimization/results/ccu-production-20261013/`: log terminal, `results.json`, `report.html`, outputs và nhật ký job do runner tạo. Chỉ hủy job benchmark còn dở, không sửa giới hạn hoặc giả IP. Report cập nhật trong lúc chạy; xem trường phase để biết đã hoàn tất hay chưa.

Chạy một phép đo mới từ root dự án, dùng output chưa tồn tại:

```sh
# Bước 1: kiểm từng mẫu thật qua gateway; có thể mất khoảng một giờ.
flock /tmp/translate-gpu-test.lock .venv/bin/python -u \
  module-map/translate/fixtures/tools/bench_ccu_manifest.py \
  --base http://127.0.0.1:8131 --allow-production \
  --manifest module-map/translate/fixtures/ccu-within-limits/manifest.json \
  --out /tmp/ccu-translation-new --preflight

# Bước 2: đo 5/10/20/25/30 VU, 3 lượt/mức, dùng preflight đã lưu.
flock /tmp/translate-gpu-test.lock .venv/bin/python -u \
  module-map/translate/fixtures/tools/bench_ccu_manifest.py \
  --base http://127.0.0.1:8131 --allow-production \
  --manifest module-map/translate/fixtures/ccu-within-limits/manifest.json \
  --out /tmp/ccu-translation-new --resume
```

Lệnh in `MEASURE`, `PROGRESS`, `DONE`, `SUMMARY`, đường dẫn `REPORT` ra terminal. JSON/HTML được cập nhật sau mỗi lượt; số liệu kết thúc có `phase=complete`. `--resume` chỉ chấp nhận đúng gateway, hash manifest, mã runner/helper và cấu hình của lần chạy trước.

## Kết quả phép đo đã hoàn tất

Preflight 155/155 mẫu: 154 xử lý thành công, 146 giữ đủ literal. Tải chính thức qua 8131 có **1.538 lần gửi, 1.528 hoàn tất**, 16 mức × 3 lượt. File/ảnh tới VU 30; scan/mixed dừng sau VU 20 có timeout. Xem [đánh giá](../../optimization/results/ccu-production-20261013/summary.md) và [HTML](../../optimization/results/ccu-production-20261013/report.html); PASS xử lý chưa chứng minh đạt SLA hay nghĩa dịch.

`validation.json` là snapshot kiểm đầu vào khi sinh mẫu; số liệu live nằm riêng trong results/assessment. Mã thực sự dùng khi đo được giữ ở `runner-snapshot/`. Sau khi đo đã sửa guard deadline và thêm fingerprint helper; để đối chiếu phiên cũ dùng snapshot, để chạy bản hiện tại dùng output mới. Không trộn phiên có mã khác bằng --resume.
