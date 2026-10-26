# Context chức năng dịch SecAI

**Bộ lớn đã đo production (13–14/10/2026):** [dataset](fixtures/ccu-within-limits/README.md), [kết quả đánh giá](optimization/results/ccu-production-20261013/summary.md), [report HTML](optimization/results/ccu-production-20261013/report.html). 155 mẫu trong giới hạn; tài liệu/scan 10–30–60 trang; 1.538 yêu cầu tải qua 8131. File/ảnh đo tới 30 VU; scan/mixed dừng tăng sau mức 20 có timeout. Chất lượng và thời gian chờ báo riêng, giữ nguyên production.

**Dataset CCU mở rộng (13/10/2026):** [fixtures/ccu-expanded/README.md](fixtures/ccu-expanded/README.md) — text/tệp/ảnh/PDF scan nhiều độ dài, manifest riêng và ca khó. Đây là dữ liệu chuẩn bị bench; chưa có số đo tải mới. Runner CCU cũ chưa tiêu thụ manifest này.

Cập nhật theo checkout ngày **09/10/2026**. Thư mục này cung cấp bản đồ API, hành vi giao diện và quy trình kiểm thử dịch như người dùng thường. Nội dung đối chiếu mã nguồn hiện có; kết quả quan sát môi trường chạy được ghi riêng, không xem danh sách ca kiểm thử là bằng chứng đã chạy.

## Nếu bạn là agent được yêu cầu "đọc file readme của thư mục này" — làm ngay theo thứ tự sau

Đây là lệnh khởi động mặc định cho thư mục này, không cần người dùng diễn giải lại mỗi lần. Trừ khi người dùng nói rõ chỉ muốn đọc/tóm tắt (không muốn chạy gì), mặc định hiểu là: **chạy vòng lặp dev-test-review mô tả ở mục "Vòng lặp dev-test-review" bên dưới**, cụ thể:

1. Đọc đủ 5 file `01`–`05` (đặc biệt [05-trang-thai-cong-viec-dang-do.txt](05-trang-thai-cong-viec-dang-do.txt) — ghi chính xác đang dở ở đâu, đừng dò lại từ đầu `results/`) và mục "Tổ chức agent" + "Ràng buộc kỹ thuật" + "Vòng lặp dev-test-review" bên dưới trước khi làm gì khác.
2. Kiểm tra `results/` xem đã có phiên chạy đầy đủ 68/68 case gần nhất chưa (file `YYYY-MM-DD-NN.txt` có bảng TỔNG KẾT đủ 68 case) và còn case nào đang ở trạng thái "FAIL chưa rõ nguyên nhân" hoặc "NOT_RUN" không. Đối chiếu với mục 2 của file `05` để biết việc nào đã làm dở, việc nào cố ý gác lại (đừng tự ý làm lại hoặc tự ý động vào phần đã gác lại mà không hỏi).
3. Nếu chưa có phiên đầy đủ, hoặc còn case FAIL/NOT_RUN chưa xử lý: tạo **1 agent test** chạy nền (`run_in_background: true`), tuần tự toàn bộ case còn thiếu trên `ias.id.vn` thật theo đúng phương pháp ở [02](02-test-giao-dien-tung-buoc.txt) mục 9 (đăng nhập + upload qua `mcp__playwright__browser_*`), ghi kết quả vào `results/` theo quy ước ở [03](03-tieu-chi-pass.txt) mục 5.
4. Khi agent test báo FAIL có nguyên nhân BE xác định (không phải do thiếu fixture): tạo **agent sửa** chạy song song (không đụng trình duyệt) — đọc đúng file/hàm liên quan trong "Nguồn context cần đọc khi cập nhật" bên dưới, sửa code, commit vào nhánh riêng. **Không tự deploy lên production** trừ khi người dùng xác nhận lại rõ ràng ở lần hỏi đó (quyết định "chỉ sửa local, không deploy" đã chốt ngày 09/10/2026, nhưng vẫn hỏi lại nếu ngữ cảnh khác đi — ví dụ cách xa lần chốt trước quá lâu hoặc người dùng mới).
5. Lặp bước 3-4 cho tới khi mọi case trong 68 case đạt một trong các trạng thái cuối mô tả ở "Điều kiện done của vòng lặp" bên dưới — không còn case "FAIL chưa rõ nguyên nhân" hay "NOT_RUN".
6. Báo cáo tóm tắt cho người dùng: tổng PASS/FAIL/BLOCKED, danh sách FAIL kèm nguyên nhân + trạng thái fix (đã commit nhánh nào), và nhắc rõ các fix local **chưa được xác minh trên production** cho tới khi người dùng tự deploy.

Không hỏi lại người dùng "có nên chạy không" nếu đây đúng là tình huống "đọc readme của thư mục này" — tự chạy theo quy trình trên là hành vi mặc định đã được xác nhận trước (xem lịch sử quyết định trong các mục bên dưới).

## Kho mẫu đa định dạng và lấy mẫu đánh giá (10/10/2026)

**Kho mẫu** nằm ở [fixtures/formats/](fixtures/formats/) — 118 tệp, danh mục + cấu trúc + literal cần giữ ở [fixtures/formats/manifest.json](fixtures/formats/manifest.json). Nội dung kỹ thuật và thông tin đăng nhập là GIẢ (tài khoản `svc_backup_01`, mật khẩu `P@ss!w0rd#2026$%^&*()_+`, URL có tham số, IP:cổng, CVE, email, khóa) — dùng kiểm quy tắc giữ nguyên thông tin ([03](03-tieu-chi-pass.txt) mục 1b).

| Định dạng | Thư mục | Ngắn (`short`) | Dài (`long`) | Ngôn ngữ |
|---|---|---|---|---|
| DOCX | `formats/docx/` | 1 trang: tiêu đề, đoạn, 3 gạch đầu dòng, bảng 3 cột | ~4-6 trang: nhiều mục, danh sách, 2 bảng | 10 |
| XLSX | `formats/xlsx/` | 1 sheet, 8 dòng, ô gộp, công thức SUM | 3 sheet x 150 dòng | 10 |
| PDF có lớp chữ | `formats/pdf/` | 1 trang | 3-4 trang, nhiều mục | 8 (không có `ko`, `ar`: công cụ sinh không nhúng được font Hàn / ghép chữ Ả Rập) |
| PDF scan (chỉ ảnh, không lớp chữ) | `formats/pdf-scan/` | 1 trang | 3 trang | 10 |
| PNG | `formats/png/` | ~1200px, 3 câu + 7 dòng literal | A4 150dpi, dày chữ | 10 |
| JPG | `formats/jpg/` | như PNG (đoạn văn khác) | như PNG (đoạn văn khác) | 10 |
| Rất lớn (`xl`, chỉ `en`) | `xlsx/xl-en.xlsx`, `pdf-scan/xl-en.pdf` | — | XLSX 5.000 dòng; PDF scan 20 trang | kiểm dung lượng/thời gian, không vào mẫu 30 |

Tệp DOCX 20 trang có bảng và TXT dài có sẵn ở `fixtures/files/` (`en-long-20pages.docx`, `docx-tables/L1`, `L2`, `en-long-3000.txt`).

**`.doc` (Word 97-2003):** `formats/doc/short-en.doc`, `formats/doc/long-en.doc`, `formats-extra/pentest-report-vi.doc` — sinh bằng `soffice --headless --convert-to doc` (image `secai-doc-converter:1`) từ docx cùng tên; chưa đăng ký trong manifest. Dịch `.doc` cần worker và gateway gọi được `docker` + image converter (xem optimization/README.md).

Sinh lại kho (xác định, cùng nội dung): `python fixtures/tools/gen_format_fixtures.py` (cần `python-docx openpyxl reportlab pillow pypdf`; ghi đè `fixtures/formats/`).

### Tài liệu mẫu theo ca người dùng (09) — `fixtures/formats-extra/`

[09-case-tai-lieu-nguoi-dung-co-the-thu.txt](09-case-tai-lieu-nguoi-dung-co-the-thu.txt) liệt kê 169 ca tài liệu người dùng có thể thử (nhóm A-I, ưu tiên P0/P1/P2; chưa chạy ca nào). **Đợt 1 = 20 ca P0 đã có tệp mẫu** ở [fixtures/formats-extra/](fixtures/formats-extra/), danh mục + literal cần giữ + kiểm tra riêng từng ca ở [fixtures/formats-extra/manifest.json](fixtures/formats-extra/manifest.json) (22 mục, ~5 MB, dữ liệu GIẢ):

| Ca | Tệp mẫu | Kiểm gì |
|---|---|---|
| UC-C02 / C01 | `sensitive-specialchars-vi.txt` (32 mật khẩu, mỗi ký tự đặc biệt một dòng), `creds-30pairs-vi.txt` | mất/đổi mật khẩu, tài khoản |
| UC-C07 | `connstrings-en.txt` (chuỗi kết nối, JWT, PEM, khóa SSH) | literal dài, khối nhiều dòng |
| UC-C12 / C13 | `config-json-vi.txt`, `config-yaml.txt` | JSON/YAML còn parse được, khóa không đổi |
| UC-C15 | `log-200lines.txt` | số dòng ra = vào |
| UC-C24 | `ioc-table-vi.docx` (101 hàng IOC defang) | cấu trúc bảng + literal |
| UC-A01 | `pentest-report-vi.docx` (bảng phát hiện, khối lệnh Courier) | cấu trúc + lệnh nguyên văn |
| UC-B02 | `nested-tables-vi.docx` (bảng 3x3 chứa 2x2) | bảng lồng |
| UC-A08 / B08 / B10 / B13 | `inventory-300rows-vi.xlsx`, `sheets-special-names.xlsx`, `formulas-vi.xlsx`, `numbers-dates-vi.xlsx` | XLSX: ô gộp, tên sheet, công thức không bị dịch, số/ngày |
| UC-A07 | `invoice-en.pdf` (bảng + IBAN) | PDF có lớp chữ, bảng |
| UC-E18 / E07 | `mixed-scan-text-5p.pdf`, `scan-10p-with-blank.pdf` | PDF trộn scan + chữ; trang trắng/nhiễu |
| UC-E10 | `terminal-nmap-dark.png` | OCR chữ sáng trên nền tối |
| UC-G08 / G06 / G07 | `zipbomb-ratio.docx`, `zip-many-entries.docx`, `docx-external-rel.docx`, `docx-xxe.docx` | từ chối an toàn (413 ARCHIVE_LIMIT_EXCEEDED / UNSAFE_DOCUMENT), không có `/etc/passwd` trong đầu ra |
| UC-H01 / H02 | mục `batch` trong manifest (5 tệp hợp lệ, 6 tệp vượt giới hạn) | giới hạn 5 tệp, rate-limit |

Sinh lại: `python fixtures/tools/gen_case_fixtures.py`. UC-I02 dùng đầu ra của UC-C02. Các tệp "độc hại" chỉ để kiểm hệ thống của chính mình từ chối đúng.

**Kiểm "giữ nguyên thông tin" tự động** ([03](03-tieu-chi-pass.txt) mục 1b — mật khẩu, tài khoản, IP, email, URL, khóa, CVE, ký tự đặc biệt phải còn nguyên từng ký tự): `python3 fixtures/tools/check_output.py --id UC-C02 --output <bản dịch.txt>` in "literal giữ X/Y", liệt kê giá trị thiếu, báo marker lạ (`⟦⟧`, `[L0]`, `[REDACTED]`, `***`), kiểm số dòng, JSON parse, chuỗi cấm. Thiếu dù một ký tự = FAIL. Công cụ không chấm nghĩa dịch; nghĩa chấm theo 03 mục 1.

## Quy tắc triển khai (bắt buộc, thêm 11/10/2026)
Mỗi lần cập nhật mã/cấu hình dịch phải **test lại trước, rồi mới triển khai production**; ghi vào đây và vào [10-nhat-ky-toi-uu.txt](10-nhat-ky-toi-uu.txt):
1. Dựng/áp trên stack thử (cổng 18285, `optimization/scripts/run_stack.sh`), KHÔNG áp thẳng vào production.
2. Test lại tối thiểu: (a) bộ P0 `p0run.py` (16 ca giữ-nguyên-thông-tin PASS + 4 ca tệp độc hại bị từ chối đúng, trừ ca đã chủ động đổi kỳ vọng như UC-G06), (b) OCR: `fixtures/tools/ocr_special_char_check.py` đủ 40/40 nếu đụng OCR, (c) một vòng 30 mẫu (15 dài + 15 ngắn) nếu đụng pipeline, (d) tốc độ giây/trang so với số trước.
3. Ghi kết quả (đạt/không, số đo trước → sau) và việc CHƯA test vào nhật ký TRƯỚC khi triển khai; mục nào chưa test thì ghi rõ "chưa test lại", không coi là đã xong.
3b. Test cả đường LỖI của API (mã ngôn ngữ sai → 400, job không tồn tại → 404, vượt giới hạn tần suất → 429, tệp bị từ chối → job failed có lý do) — một lần đổi tên hàm đã làm mọi phản hồi lỗi thành HTTP 500 (T45).
4. Triển khai production bằng quy trình có khóa + đường lui (`/home/ict/secai-releases/<bản>/rollback.sh`), kiểm không có job đang chạy, rồi test lại qua cổng 8131 và giao diện IAS.
**Đang chờ test lại (đã nằm trong image production `translate-opt-20261011c` nhưng CHƯA kiểm riêng):** patch chồng OCR↔dịch của agent B (cờ `TRANSLATE_V2_SPEC_TRANSLATE_ENABLED` mặc định TẮT — chưa bật, chưa đo lại); adapter OCR song song dải CHƯA ghép vào VM.

**Test tải (gửi đồng thời nhiều tệp):** `python3 fixtures/tools/loadtest.py --base http://127.0.0.1:18285 --concurrency 20 [--kinds text|ocr|all]` — báo nhận/từ chối (429 gọn), thành công/thất bại, xếp hàng, p50/p95, thông lượng, lỗi 5xx; xem [03](03-tieu-chi-pass.txt) mục 1e và kết quả ở optimization/results/load/. Trên production giới hạn 5 upload/phút và 5 job/IP sẽ cho 429 (hợp lệ).

**Đo CCU (người dùng đồng thời):** `flock /tmp/translate-gpu-test.lock python3 fixtures/tools/ccu_translate.py --base http://127.0.0.1:18285 --levels 20,25,30 --runs 3 --duration 240 --ramp 30 --json optimization/results/ccu/ccu-N.json --html optimization/results/ccu/ccu-N.html` (mỗi mức đo 3 lần, nghỉ 40 s giữa các lần, kết luận theo TRUNG BÌNH; `--levels` chỉ nhận 20/25/30, hoặc dùng `--users N` cho 1 mức; ~15 phút/mức) (mỗi VU: tải tệp → poll → nghỉ 5-20 s; 70% chữ/30% OCR, 20% dài; ĐẠT khi 0 lỗi 5xx/timeout, succeeded >= 99%, p95 ngắn <= 30 s, dài <= 120 s). Luôn in kết quả ra terminal (từng lần + trung bình); `--html` xuất thêm báo cáo HTML tự chứa (bảng PASS/FAIL theo mức + biểu đồ p95/thông lượng nhúng ảnh, không cần mạng — ví dụ [ccu-smoke-20261011.html](optimization/results/ccu-smoke-20261011.html)). Chỉ chạy trên stack thử (dựng bằng /home/ict/secai-opt/stack_exp.sh, cần tunnel test 28868/28083 mở), từ chối chạy vào :8131/:18185. Kết quả: [ccu-report-20261011.md](optimization/results/ccu-report-20261011.md).

**Kiểm OCR mức pipeline (ảnh + PDF scan):** `python3 fixtures/tools/ocr_pipeline_check.py --base http://127.0.0.1:18285 --concurrency 3` — gửi 60 tệp (png/jpg/pdf-scan × 10 ngôn ngữ × dài/ngắn) qua API, kiểm giá trị nhạy cảm trong bản dịch, báo ký tự bị rớt/đảo và giây/trang; khác `ocr_special_char_check.py` (gọi thẳng OCR trên ảnh gốc, không thấy lỗi do phóng ảnh/render trang).

**Nhật ký tối ưu:** [10-nhat-ky-toi-uu.txt](10-nhat-ky-toi-uu.txt) — mỗi lần tối ưu ghi 1 ý (ngày, thay đổi, số đo trước → sau, nơi lưu mã). Tối ưu mới thì thêm dòng cuối tệp này.

**Kiểm OCR từng ký tự** ([03](03-tieu-chi-pass.txt) mục 1b.7 — ảnh/PDF scan không được rớt/đổi/đảo ký tự đặc biệt như `&`): `python3 fixtures/tools/ocr_special_char_check.py --ocr-url <URL OCR> --token-file <token>` chạy OCR trực tiếp trên 40 ảnh `fixtures/formats/{png,jpg}`, in literal đủ x/y, ký tự hay bị rớt, mã thoát 0 = PASS (phải 100%). Hiện CHƯA ĐẠT (xem optimization/README.md).

**Chấm chất lượng bản dịch có % ([03](03-tieu-chi-pass.txt) mục 1d, thêm 11/10/2026):** `python3 fixtures/tools/quality_score.py --api-url http://127.0.0.1:18185 --save-dir <thư mục> --json-out results/<tên>.json` (hoặc `--outputs <thư mục *.out.txt> --label <tiền tố>` để chấm đầu ra có sẵn, offline). Metric chính chrF so với gold trong [04-test-data.json](04-test-data.json) + cổng phụ (tỉ lệ độ dài, không sót chữ ngôn ngữ nguồn, không bỏ đoạn, giữ literal/không marker). In bảng từng mẫu và dòng "ĐẠT x/y = z%"; mã thoát 0 nếu z >= 90. Cách 13 giây giữa các lần tải lên (production 5/phút). Token (nếu cần) qua `--token-env`/`--token-file`, không bao giờ in/ghi. Test: `python3 -m unittest fixtures/tools/test_quality_score.py`. **Yêu cầu: MỖI LẦN ĐO (mỗi vòng, mỗi lần sau khi sửa/deploy) phải báo "Chất lượng dịch: ĐẠT x/y = z%, chrF TB n" kèm mẫu trượt và lý do, ghi vào đầu file kết quả `results/`.**

### Lấy mẫu đánh giá — mỗi vòng 30 tài liệu ngẫu nhiên, dài : ngắn = 15 : 15

Mỗi **vòng** (test -> kiểm -> cải tiến) lấy **30 tài liệu ngẫu nhiên có phân tầng** (tệp + ảnh) từ kho [fixtures/formats/](fixtures/formats/) (118 tệp), không chọn tay:

- **Tỉ lệ bằng nhau: 15 dài + 15 ngắn.** 6 định dạng x 5 mẫu (DOCX, XLSX, PDF có lớp chữ, PDF scan, PNG, JPG). 3 định dạng (chọn ngẫu nhiên theo seed) lấy 3 dài + 2 ngắn, 3 định dạng còn lại 2 dài + 3 ngắn. Ưu tiên ngôn ngữ khác nhau trong cùng định dạng. Mẫu `xl` kiểm riêng, không vào 30.
- Lệnh: `python3 fixtures/tools/pick_eval_sample.py [--seed N] [--json]`. Mỗi vòng dùng **seed mới**; ghi seed vào đầu file kết quả (`results/YYYY-MM-DD-NN.txt`) để chạy lại đúng bộ mẫu. Công cụ từ chối ra bộ không đúng 15:15.
- Mẫu 30 này là bộ NGẪU NHIÊN để tránh học vẹt; 20 ca P0 ở trên là bộ HỒI QUY CỐ ĐỊNH, chạy ở vòng đầu và sau mỗi lần sửa liên quan.

**Vòng lặp test -> kiểm -> cải tiến (mỗi vòng):**

1. **Test:** lấy 30 mẫu bằng `pick_eval_sample.py --seed <mới>`; gửi từng mẫu qua API v2 hoặc UI theo [02](02-test-giao-dien-tung-buoc.txt) mục 9 (ghi lại ngôn ngữ đích; hướng dịch xoay vòng để phủ 9 ngôn ngữ). Agent chạy test chỉ ĐO và ghi kết quả (không tự sửa), tuần tự vì chỉ có 1 GPU.
2. **Kiểm:** (0) chạy `quality_score.py` và báo "Chất lượng dịch: ĐẠT x/y = z%" (03 mục 1d) trên các hướng/mẫu có gold của vòng; với mỗi mẫu áp [03](03-tieu-chi-pass.txt): (a) mục 1 nghĩa/ngôn ngữ đích; (b) mục 1b giữ nguyên thông tin, dùng `check_output.py` hoặc so chuỗi con với `preserve_literals` trong manifest; (c) mục 1c: giữ cấu trúc, hỗ trợ đúng loại tệp/OCR, **tốc độ ~1-3 giây/trang** (tài liệu 20 trang <= 60 giây). Ghi "Đạt X/30", chia theo dài/ngắn và theo định dạng, kèm thời gian thật mỗi mẫu.
3. **Cải tiến:** gom lỗi theo nguyên nhân (OCR / dịch / cấu trúc / tốc độ / loại tệp), sửa trong nhánh riêng, **không deploy lên production khi chưa được xác nhận**; kiểm bằng stack thử nghiệm cô lập. Mỗi sửa phải kèm test tái hiện lỗi.
4. **Vòng sau:** chạy lại các mẫu FAIL của vòng trước + **30 mẫu mới** (seed mới) + các ca P0 liên quan. Dừng khi 2 vòng liên tiếp đều không có FAIL mới ở phần nghĩa/giữ thông tin/tốc độ.
- Điểm ghi riêng cho phần này: "Đạt X/30 (tệp/ảnh)"; không cộng vào điểm 71 ca của phiên đầy đủ.

## Đọc theo thứ tự

| File | Nội dung |
|---|---|
| [01-api-va-luong-xu-ly.txt](01-api-va-luong-xu-ly.txt) | Toàn bộ endpoint dịch, request/response, SSE, job, lỗi và cấu hình |
| [02-test-giao-dien-tung-buoc.txt](02-test-giao-dien-tung-buoc.txt) | Chuẩn bị và các bước bấm trên IAS (`ias.id.vn`): text, tệp, ảnh, hủy, tải lại, lỗi |
| [03-tieu-chi-pass.txt](03-tieu-chi-pass.txt) | Tiêu chí PASS/FAIL/BLOCKED, checklist và mẫu báo cáo |

> **Quy tắc bắt buộc (10/10/2026):** mọi ca dịch ảnh/file/text phải GIỮ NGUYÊN toàn bộ thông tin — không che, không xóa, không đổi giá trị (mật khẩu, tài khoản, khóa, token, IP, URL, email, CVE, chữ CJK…). Xem [03](03-tieu-chi-pass.txt) mục 1b; 3 ca bắt buộc ở khóa `integrity_cases` trong 04 (phiên đầy đủ = 68 + 3 = 71 ca). Bị từ chối vì thông tin nhạy cảm = FAIL.
> **Phản hồi nghiệm thu (10/10/2026):** còn mất dữ liệu tài khoản/mật khẩu/ký tự đặc biệt; dịch tệp cần scan (kể cả ảnh) quá chậm; cần giữ nguyên cấu trúc; phải hỗ trợ PDF, XLSX, DOCX, DOC, PNG…; cho phép OCR PDF scan và ảnh; tốc độ ~1-3 giây/trang (tài liệu 20 trang <= 60 giây). Xem [03](03-tieu-chi-pass.txt) mục 1c, [05](05-trang-thai-cong-viec-dang-do.txt) mục 0b, [06](06-checklist-kiem-thu-theo-loai-dau-vao.txt) mục A1/E/F.
| [04-test-data.json](04-test-data.json) | Dữ liệu text và đường dẫn file/ảnh theo từng ID ca |
| [05-trang-thai-cong-viec-dang-do.txt](05-trang-thai-cong-viec-dang-do.txt) | **Đọc file này trước nếu tiếp tục công việc dở** — đã xong gì, deploy gì, còn gì chưa xử lý |
| [06-checklist-kiem-thu-theo-loai-dau-vao.txt](06-checklist-kiem-thu-theo-loai-dau-vao.txt) | **Checklist theo loại đầu vào (tệp / ảnh / văn bản)** — duyệt từng mục một lần thay vì thử case rời rạc; có trạng thái OK/LỖI/CHƯA |
| [fixtures/](fixtures/) | Nội dung mẫu thật: `text/` 500 từ (dán trực tiếp), `files/` 1000 từ `.txt` (upload), `images/` ảnh OCR theo từng ngôn ngữ |
| [results/](results/) | Kết quả mỗi lần chạy, mỗi phiên một file `.txt`, quy ước đặt tên ở [03](03-tieu-chi-pass.txt) mục 5 |

Luồng chính và duy nhất để test là **https://ias.id.vn/login**, tài khoản **dev201**, menu **Nền tảng AI → Dịch văn bản**. Mật khẩu được cấp riêng, không lưu vào Git. MCP đã được dùng để quan sát và chạy thử trực tiếp trên IAS; phạm vi quan sát thực tế nằm ở cuối file 02 và trong `results/`.

Bộ dữ liệu hiện có đủ `input`/`output_gold` sẵn sàng chạy (`preparation_status: ready`) cho 18 hướng text trực tiếp, 18 cặp file và 18 cặp ảnh (Việt ↔ 9 ngoại ngữ: en, zh-Hans, ja, ko, fr, de, es, ru, ar) trong [04-test-data.json](04-test-data.json). Ảnh OCR trong `fixtures/images/` là **ảnh tự render bằng Pillow + font Noto**, không phải ảnh tải thật từ internet — lý do và quyết định ghi ở [03](03-tieu-chi-pass.txt) mục 2. Còn `null`/`needs_file`: `FILE-04-UNSUPPORTED` (PDF/XLS/XLSX/PPT/PPTX), `IMG-03-NOTEXT` (ảnh không chữ), và các case `BATCH-*`/`CANCEL-01`/`RESTORE-01`/`NEG-*` — cần bổ sung file/ảnh thật trước khi chạy. DOCX/DOC thật (nhị phân) chưa nằm trong đợt cập nhật này, chỉ có `.txt`.

## Tổ chức agent khi chạy một phiên test

Một phiên chạy đầy đủ (68 case, xem [03](03-tieu-chi-pass.txt) mục 5) không chạy bằng một agent đơn lẻ tuần tự từ đầu đến cuối, mà chia việc giữa nhiều agent:

- **Nhiều agent thực thi** (model Haiku 5.5, effort `high`): mỗi agent nhận một phần case (ví dụ chia theo nhóm `text_direct`/`file`/`image` hoặc theo ngôn ngữ), tự đăng nhập/mở trình duyệt, chạy đúng các bước ở [02](02-test-giao-dien-tung-buoc.txt), và ghi lại input đã dùng + output thực tế quan sát được trên UI cho từng case — **không tự kết luận PASS/FAIL**, chỉ ghi nguyên văn quan sát.
- **Một agent đánh giá (solo)**: nhận toàn bộ output thực tế do các agent thực thi ghi lại, đối chiếu từng case với `output_gold` trong [04-test-data.json](04-test-data.json) theo đúng quy tắc PASS ở [03](03-tieu-chi-pass.txt) mục 1, rồi gộp thành **một** file kết quả trong [results/](results/) theo đúng quy ước đặt tên và mẫu báo cáo ở đó. Agent đánh giá là nơi duy nhất ghi kết luận PASS/FAIL/BLOCKED/NOT_RUN/NOT_APPLICABLE cuối cùng, và là nơi duy nhất chốt **điểm tổng kết "Đạt X/68 UC"** cùng danh sách UC lỗi kèm nguyên nhân (FE: trích log thật qua MCP devtool; BE: chỉ ra endpoint + vị trí code) — chi tiết cách làm ở [03](03-tieu-chi-pass.txt) mục 7.

Tách vai trò như trên để tránh một agent vừa tự chạy vừa tự chấm cùng lúc (dễ bỏ sót lỗi hiển thị hoặc tự nới lỏng tiêu chí so khớp `output_gold`).

### Ràng buộc kỹ thuật: trình duyệt dùng chung

Thao tác UI thật đi qua `mcp__playwright__browser_*`/`mcp__t3-code__preview_*` — các tool này điều khiển **một trình duyệt dùng chung trong phiên**, không phải một trình duyệt riêng cho mỗi agent. Nhiều agent bấm/điều hướng UI **song song thật** sẽ giẫm lên nhau (agent A đang dịch thì agent B đổi ngôn ngữ/tab). Vì vậy:

- "Nhiều agent thực thi" ở trên chỉ an toàn khi chạy **nối tiếp từng agent một** (agent sau chỉ bắt đầu khi agent trước đã nhường trình duyệt), hoặc khi chia case theo cách không cần thao tác UI đồng thời.
- Việc đăng nhập/upload file/ảnh dùng `mcp__playwright__browser_*` (browser riêng của playwright, tách khỏi `preview_*`) — cách làm đã xác nhận dùng được ngày 09/10/2026, chi tiết ở [02](02-test-giao-dien-tung-buoc.txt) mục 9. Đã thử cách set thẳng `.files` qua JS/base64 trong `preview_evaluate` nhưng bị hệ thống an toàn chặn — **không dùng lại**.
- Công việc không chạm trình duyệt (đọc code tìm nguyên nhân lỗi, sửa code, viết lại tài liệu) mới thực sự chạy song song được giữa nhiều agent.

## Vòng lặp dev-test-review (test → tìm lỗi → sửa → test lại)

Áp dụng khi mục tiêu là đưa toàn bộ 68 case về PASS, không chỉ ghi nhận hiện trạng. Quyết định ngày 09/10/2026 (do người yêu cầu xác nhận): **bước sửa chỉ dừng ở sửa code local + commit vào nhánh riêng, không tự động deploy lên `ias.id.vn` production.**

1. **Agent test (1 agent, chạy tuần tự toàn bộ 68 case)**: đăng nhập bằng `mcp__playwright__browser_*` theo đúng cách ở mục trên, chạy lần lượt từng case trong [04-test-data.json](04-test-data.json) theo các bước ở [02](02-test-giao-dien-tung-buoc.txt), đối chiếu `output_gold` theo quy tắc ở [03](03-tieu-chi-pass.txt), ghi kết quả thật vào `results/` theo đúng mẫu — **kể cả khi gặp FAIL vẫn tiếp tục chạy hết case, không dừng giữa chừng**.
2. **Khi một case FAIL với nguyên nhân BE xác định được** (có `error.code`, endpoint, hoặc log console/network cụ thể — không phải do thiếu fixture): giao cho **agent sửa** (chạy song song với agent test, vì không đụng trình duyệt) — đọc đúng file/hàm liên quan trong "Nguồn context cần đọc khi cập nhật" bên dưới, xác định nguyên nhân gốc, sửa code, chạy test/build local nếu có, rồi `git commit` vào một nhánh riêng (không phải `main`, không deploy). Agent sửa ghi lại trong message/commit: file đã sửa, vì sao, và **rõ ràng là CHƯA deploy nên chưa thể xác minh trên production**.
3. **Test lại (retest)**: vì không deploy, chạy lại đúng case đó trên `ias.id.vn` **sẽ cho kết quả y hệt lần trước** nếu lỗi là lỗi BE xác định (code chưa đổi trên server) — đây là hành vi đúng, không phải agent test chạy sai. Retest chỉ có ý nghĩa xác nhận lại cho các case nghi ngờ **không ổn định** (ví dụ lỗi 400 khi poll nhiều job cùng lúc, hoặc chất lượng dịch có thể khác nhau giữa các lần gọi model) — chạy lại 1-2 lần để phân biệt "lỗi luôn xảy ra" với "thỉnh thoảng mới xảy ra".

**Bắt buộc sau MỖI lần deploy (quyết định 10/10/2026, áp dụng mọi lần sau này, không chỉ lần này):** retest phải đi qua **giao diện thật** (thao tác UI qua `mcp__playwright__browser_*`: chọn ngôn ngữ, dán text/upload file, bấm nút, đọc kết quả hiển thị trên màn hình), không được chỉ gọi thẳng API/`curl`/đọc JSON job status rồi suy ra đã fix. Lý do: đã có trường hợp `curl` trần bị WAF chặn cho ra 400 giả trong khi app không lỗi (xem phần "upload file" ở trên) — ngược lại cũng có thể API trả đúng nhưng UI hiển thị sai/không hiển thị, nên chỉ kiểm API là không đủ chứng minh người dùng thật sẽ thấy gì. Mỗi case retest sau deploy phải có `preview_snapshot`/`browser_take_screenshot` lưu lại (dùng `save=true` hoặc `filename`) làm bằng chứng, đường dẫn ảnh ghi kèm trong block kết quả ở `results/`.
4. **Điều kiện "done" của vòng lặp**: không phải "toàn bộ 68 case PASS trên production" (vì không deploy, các FAIL do BE đã biết nguyên nhân sẽ không tự hết). "Done" nghĩa là: mỗi case trong 68 case đạt một trong các trạng thái cuối — **PASS thật** (đã test và khớp `output_gold`), **FAIL đã chẩn đoán + có fix local chờ deploy** (có commit, có mô tả nguyên nhân rõ ràng), hoặc **BLOCKED/NOT_APPLICABLE có lý do** (thiếu fixture, ngoài phạm vi). Không còn case nào ở trạng thái "FAIL chưa rõ nguyên nhân" hoặc "NOT_RUN".
5. Khi người phụ trách tự deploy các fix đã commit, chạy lại đúng các case từng FAIL (không cần chạy lại cả 68 case) để xác nhận PASS thật trên production, rồi cập nhật `results/` bằng một phiên mới.

Tài liệu/ghi chép của vòng lặp: mỗi lượt test ghi một file `results/YYYY-MM-DD-NN.txt` như thường lệ; mỗi commit sửa lỗi ghi rõ case id liên quan trong message commit để truy ngược dễ dàng.

### Deploy đã thực hiện — `translate-bugfix-20261010` (09/10/2026 ~17:21 UTC)

Người dùng xác nhận rõ ràng cho deploy lần này (khác quyết định mặc định "chỉ local" ở trên — ghi đè cho đúng một lần này, không phải thay đổi quyết định mặc định cho các lần sau). Đã deploy 3 commit từ nhánh `fix/translate-bugs-2026-10-09` (`0656088`, `b77a464`, `5493394`) lên **đúng 2 service đang chạy thật dịch cho `ias.id.vn`**: `secai-translation-gateway` (process `multilang_app`, xử lý cả admission v2 lẫn v3 text) và `secai-translation-worker` (process `translation.worker`, xử lý job file/ảnh v2) — không đụng `secai-docker-gateway` (gateway chat chính) hay các service khác.

Cách làm: patch theo kiểu layer mỏng `FROM <image đang chạy> + COPY <đúng file đã sửa>` (không build lại toàn bộ từ nguồn), giữ lại mọi hotfix khác đã có sẵn trên image đang chạy (ví dụ bản vá unicode từ release `translation-unicode-batch-20261009-194904` trước đó). Có giữ lock (`gateway-deploy.lock`, `translation-deploy.lock`), kiểm ownership (container Id khớp đúng bản đã ghi) ngay trước khi đổi, và container cũ được **đổi tên giữ lại** (`secai-translation-gateway-before-20261010`, `secai-translation-worker-before-20261010`) thay vì xóa — rollback tức thì bằng `/home/ict/secai-releases/translate-bugfix-20261010/rollback.sh`.

**Retest thật sau deploy này cho thấy chỉ 1/3 fix có tác dụng:** fix zh-Hans alias xác nhận khắc phục triệt để (FILE-vi-zh-Hans PASS); fix retry-budget và fix near-empty-guard KHÔNG khắc phục được, 6/6 case liên quan vẫn FAIL y hệt — xem `results/2026-10-09-03.txt` mục RETEST, và phần điều tra sâu bên dưới.

### Deploy round 2 — `translate-bugfix-round2-20261010` (09/10/2026 ~18:05 UTC)

Agent điều tra sâu (model Opus) tìm ra 2 fix lần đầu nhắm sai nguyên nhân:

- **`TOKEN_INTEGRITY_FAILED` (luôn dừng đúng 2/18 chunk)**: không phải model không ổn định. Nguyên nhân thật: sau khi khôi phục literal, code chạy lại `literal_spans()` trên bản dịch rồi đòi khớp y hệt bản nguồn — nhưng mẫu `_VERSION` nuốt luôn từ ASCII đứng ngay trước số có dấu chấm ("from 198.51.100.23", "bản 3.4.1"), nên **một bản dịch hoàn toàn đúng vẫn luôn fail**, lặp lại y hệt ở mọi lần thử (giải thích vì sao retry không cứu được). Fix thật: so phần lõi literal (bỏ từ bị nuốt) thay vì so nguyên span — commit `79628a3` trên nhánh `fix/translate-bugs-round2-2026-10-10`, có test tái hiện lỗi trước khi sửa. **Đã deploy** — chỉ `secai-translation-worker`, patch layer mỏng lên trên image round 1, cùng quy trình lock/ownership/rollback như trên. Rollback: `/home/ict/secai-releases/translate-bugfix-round2-20261010/rollback.sh`.
- **Mất chữ Hán khi dịch trực tiếp vi→ja/zh-Hans**: hóa ra **IAS không gọi v3** cho ô dịch trực tiếp — gọi endpoint legacy `/ai/translate` trên `secai-docker-gateway` (gateway chat chính, KHÁC hẳn 2 container dịch), nơi có bộ lọc an toàn của chat (`remove_chinese_content()`) xóa mọi ký tự Hán trong mọi output — kể cả khi người dùng chủ động chọn dịch sang tiếng Nhật/Trung. Fix thật (`fd5444f`, cùng nhánh) thêm cờ `keep_cjk` bỏ qua bước xóa khi đích là zh/ja. **CHỦ ĐỘNG CHƯA DEPLOY** theo yêu cầu người dùng (gác nhóm lỗi "chất lượng" lại đợt sau) — cũng vì cần build lại `secai-docker-gateway`, đang có code chưa commit của một phiên/agent khác trong `src/secai_gateway/api/messages.py` (liên quan đúng chủ đề alias `zh-Hans` cho endpoint legacy) — không động vào để tránh giẫm lên việc người khác đang làm dở.

Retest 5 case `TOKEN_INTEGRITY_FAILED` sau deploy round 2: đang chạy, xem block "RETEST ROUND 2" mới nhất trong `results/2026-10-09-03.txt`.

Hồ sơ đầy đủ (ownership record, Dockerfile, state.json, rollback.sh) ở `/home/ict/secai-releases/translate-bugfix-20261010/` (private, mode 0700, không đưa vào Git). Cả 2 container mới health=healthy ngay sau swap. Xác minh thật qua UI do agent test đang chạy đảm nhận (không tự mở trình duyệt song song để tránh giẫm — xem mục ràng buộc kỹ thuật ở trên); kết quả retest nằm trong `results/2026-10-09-03.txt`, đánh dấu rõ "RETEST sau deploy".

## Chức năng đang dùng

- **Dịch trực tiếp:** text đa ngôn ngữ v3, chọn nguồn/đích rõ ràng, nhận kết quả qua SSE.
- **Dịch tệp và ảnh:** job v2; TXT/DOCX/DOC được trích xuất rồi dịch, PNG/JPG/JPEG đi qua OCR rồi dịch. Có xem trước, tải TXT/JSON và hủy job.
- **Legacy:** `/ai/translate` và `/ai/translate/stream` vẫn tồn tại cho client cũ; màn hình `TranslateScreen` hiện không gọi hai endpoint này.

V3 có 10 ngôn ngữ và tối đa 18 hướng Việt ↔ Anh/Trung/Nhật/Hàn/Pháp/Đức/Tây Ban Nha/Nga/Ả Rập. Hướng thực sự được bật lấy từ `GET /ai/v3/translate/languages`. V2 dùng `zh`, v3 trả mã chuẩn `zh-Hans` cho tiếng Trung.

## Nguồn context cần đọc khi cập nhật

Các đường dẫn dưới đây tính từ thư mục này:

- [Màn hình dịch](../../secai-fe-devspace/src/screens/TranslateScreen.tsx), [panel text](../../secai-fe-devspace/src/screens/MultilangTranslatePanel.tsx), [panel tệp/ảnh](../../secai-fe-devspace/src/screens/TranslateFileBatchPanel.tsx).
- [Client v3](../../secai-fe-devspace/src/api/multilang.ts), [client v2](../../secai-fe-devspace/src/api/translateV2.ts), [client legacy](../../secai-fe-devspace/src/api.ts).
- [Routes v3](../../src/secai_gateway/api/multilang/routes.py), [registry ngôn ngữ](../../src/secai_gateway/multilang/registry.py), [dispatcher](../../src/secai_gateway/multilang_app.py).
- [Routes v2](../../src/secai_gateway/api/translate_v2/routes.py), [job store](../../src/secai_gateway/translation/jobs.py), [worker](../../src/secai_gateway/translation/worker.py).
- [Cấu hình v3](../../src/secai_gateway/multilang/config.py), [cấu hình v2](../../src/secai_gateway/translation/settings.py), [cấu hình gateway](../../src/secai_gateway/config.py).
- [Tài liệu API dịch (bản nội bộ)](../../docs/api/dich-devspace-hien-tai.md), [contract v3](../../docs/api/multilang-contract.md), [contract v2](../../docs/api/translate-v2-contract.md).

Nếu tài liệu cũ khác mã hiện tại: kiểm tra cả mã nguồn và bản frontend đang phục vụ. Ví dụ README frontend còn ghi 20 tệp; `TranslateFileBatchPanel.tsx` hiện giới hạn 5 tệp mới mỗi phiên và chỉ giữ khả năng đọc phiên cũ tối đa 20 dòng. Giới hạn job của backend phụ thuộc cấu hình deployment, không suy ra từ câu mô tả trên giao diện.
