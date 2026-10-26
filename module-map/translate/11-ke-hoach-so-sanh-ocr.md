# Kế hoạch so sánh OCR: Qwen3-VL-2B (đang dùng) vs Baidu Unlimited-OCR — 13/10/2026

Mục tiêu: quyết định có nên thay model OCR hay không, dựa trên số đo trên dữ liệu của dự án, không dựa trên benchmark công bố.
Phạm vi: chỉ OCR. Model dịch Hy-MT2-7B giữ nguyên. Không đụng production (:8131, tunnel 18868/18083).

| | Qwen3-VL-2B-Instruct (A) | Unlimited-OCR (B) |
|---|---|---|
| Tham số | ~2B | 3B (BF16) |
| Loại | VLM đa dụng, OCR theo prompt | Chuyên OCR/parse tài liệu, nền DeepSeek-OCR + R-SWA (KV cache cố định, nhiều trang/lượt, tối đa 32K) |
| Serving | vLLM, luôn nạp, API /v1/ocr | Transformers + `trust_remote_code`; chưa rõ chạy được vLLM |
| Đầu ra | Văn bản thuần | Văn bản kèm marker loại khối + toạ độ, cần hậu xử lý |
| Giấy phép | Apache 2.0 | MIT |

## 1. Nguyên tắc công bằng

1. **Cùng phần cứng:** cùng 1 GPU A100 80GB, cùng driver/CUDA, đo lần lượt (không chạy song song 2 model khi đo tốc độ).
2. **Cùng đầu vào:** cùng byte ảnh. PDF scan được render 1 lần ở 200 DPI (giá trị đã chốt ở T-DPI) rồi đưa cùng ảnh trang cho cả hai. Tắt upscale của pipeline khi đo mức model (giống CCU: `TRANSLATE_V2_OCR_UPSCALE_ENABLED=false`).
3. **Cùng độ chính xác số:** cả hai BF16, không quantize. Giải mã tham lam (temperature 0), cùng `max_tokens`.
4. **Prompt/chế độ:** mỗi model dùng prompt/chế độ OCR **khuyến nghị của chính nó** (A: prompt hiện tại trong pipeline; B: prompt mẫu trong README). Không tối ưu prompt riêng cho một bên trong vòng chính.
5. **Hậu xử lý:** B chạy hậu xử lý chính thức (bỏ marker, bỏ vùng ảnh) để ra văn bản thuần, rồi cả hai qua cùng bước chuẩn hoá (Unicode NFC, gộp khoảng trắng) trước khi chấm. Không sửa lỗi ký tự cho bên nào.
6. **Tốc độ đo 2 kiểu:** (a) **cùng engine** — cả hai chạy Transformers, để so model; (b) **engine tốt nhất mỗi bên** (A: vLLM; B: vLLM nếu hỗ trợ, không thì Transformers), để so khả năng triển khai thật. Loại lượt khởi động lạnh, mỗi phép đo chạy 3 lần lấy trung bình.
7. **Chấm tự động, mù tên model:** script chấm chỉ nhận `output_A/output_B` so với gold; phần chấm tay (bảng, thứ tự đọc) xáo trộn nhãn A/B.
8. **Cùng một lần chạy cho mọi chỉ số:** không chọn lọc lượt đẹp. Lưu toàn bộ đầu ra thô.

## 2. Bộ dữ liệu

| Nhóm | Nguồn | Số lượng | Gold | Kiểm gì |
|---|---|---|---|---|
| D1 Ảnh sạch 10 ngôn ngữ | `fixtures/formats/{png,jpg}/{short,long}-<lang>` | 40 | `preserve_literals` (manifest) + văn bản gốc lúc render (xuất từ `gen_format_fixtures.py`) | Literal, CER, ngôn ngữ |
| D2 PDF scan | `fixtures/formats/pdf-scan/*` | 21 tệp | như D1 | Nhiều trang, literal, tốc độ/trang |
| D3 Ca nghiệp vụ có gold | `04-test-data.json` (type "dịch ảnh") + `fixtures/images/ocr-*.png`, `ocr-sensitive-en.png` | 19 ca + 11 ảnh | `output_gold.ocr_text` | CER sát nghiệp vụ |
| D4 Ca khó | `formats-extra/terminal-nmap-dark.png`, `scan-10p-with-blank.pdf`, `mixed-scan-text-5p.pdf`, `invoice-en.pdf` (render ảnh), `images/pdf-pages/*` (scan thật, 200/300 DPI) | ~8 tệp, ~20 trang | literal + gold chép tay cho trang scan thật | Nền tối, trang trắng, bảng, scan thật |
| D5 Ảnh suy giảm | Sinh tất định từ D1 (seed cố định): JPEG q=50, mờ Gaussian σ=1, nghiêng 2°, hạ còn 150 DPI | 4 biến thể × 20 ảnh = 80 | như D1 | Độ bền với ảnh kém |

Lưu ý: D1–D3 là ảnh render bằng Pillow + font Noto (sạch hơn ảnh thật). Vì vậy D4/D5 bắt buộc có, và kết luận phải nêu rõ phần nào chỉ đúng với ảnh sạch.

## 3. Bảng tiêu chí

**Cổng (bắt buộc):** trượt 1 cổng = không thay, bất kể điểm tổng.

| Mã | Tiêu chí | Cách đo | Ngưỡng ĐẠT |
|---|---|---|---|
| G1 | Giữ literal nhạy cảm (mật khẩu, IP:cổng, URL, CVE, email, khoá, tài khoản) | So khớp chuỗi con chính xác mọi `preserve_literals` trên D1+D2 (logic `ocr_special_char_check.py`) | B >= A, và mục tiêu 100% (tiêu chí 1b.7) |
| G2 | Không bịa nội dung | Đếm token/dòng có trong đầu ra nhưng không có trong gold (đặc biệt CVE/IP/số bịa); trang trắng trong `scan-10p-with-blank.pdf` phải ra rỗng | B <= A; 0 CVE/IP bịa |
| G3 | Đủ 10 ngôn ngữ dự án | CER từng ngôn ngữ trên D1 | Không ngôn ngữ nào có CER > 2× của A |
| G4 | Chung GPU với Hy-MT2-7B | Peak VRAM khi B luôn nạp cùng Hy-MT2-7B, chạy tải đồng thời 6 job | Vừa 80GB, còn >= 8192 MiB trống (guard bootstrap) |
| G5 | Ổn định | Lỗi/timeout/OOM trên toàn bộ lượt chạy | 0 |
| G6 | An toàn triển khai | Đọc mã `trust_remote_code`, ghim commit/hash, chạy được air-gap (không tải gì lúc chạy), giấy phép | Không có lệnh gọi mạng/thực thi lạ; ghim được hash |

**Điểm (so sánh khi cả hai qua cổng):**

| Mã | Tiêu chí | Cách đo | Trọng số |
|---|---|---|---|
| S1 | Độ chính xác ký tự | CER trung bình (và WER cho ngôn ngữ có dấu cách) so gold, D1+D3 | 20% |
| S2 | Ký tự dễ nhầm | Số lỗi `0/O`, `l/1/I`, `rn/m`, đảo/rớt `& * ( ) _ +`, trên mọi literal | 15% |
| S3 | Ảnh khó và suy giảm | CER + G1 trên D4+D5 | 15% |
| S4 | Cấu trúc | Thứ tự đọc (đúng khối/dòng, Ả Rập RTL), bảng: khớp ô (cell F1) trên trang có bảng | 10% |
| S5 | Tốc độ | giây/trang ấm p50/p95 (ảnh 1 trang và PDF 10 trang), engine tốt nhất mỗi bên; tham chiếu tiêu chí 1–3 s/trang | 15% |
| S6 | Thông lượng tải | trang/phút ở 6 và 20 job đồng thời qua stack thử :18285 | 10% |
| S7 | Chất lượng dịch cuối | Qua pipeline thật (OCR → Hy-MT2-7B), chấm `quality_score.py` G1–G5 + `check_output.py` | 10% |
| S8 | Tất định | Tỉ lệ đầu ra giống hệt qua 3 lượt | 5% |

Cách quy điểm: mỗi tiêu chí lấy A làm mốc 100, B = 100 × (A/B) với chỉ số "càng thấp càng tốt" (CER, giây) hoặc 100 × (B/A) với chỉ số "càng cao càng tốt", chặn trong [0, 150]. Điểm tổng = tổng có trọng số.

**Quy tắc quyết định:**
- Thay sang B khi: qua đủ G1–G6, điểm tổng B >= 110 (hơn A >= 10%), **và** không tiêu chí S1/S2 nào kém hơn A.
- Chênh lệch tính trên từng tệp (so cặp A/B cùng tệp), báo khoảng tin cậy 95% bằng bootstrap. Nếu khoảng tin cậy cắt 0 thì ghi "không khác biệt rõ".
- Hoà hoặc B chỉ thắng tốc độ: giữ A (chi phí tích hợp và rủi ro `trust_remote_code` không đáng).

## 4. Các bước

| Bước | Việc | Đầu ra | Ước lượng |
|---|---|---|---|
| 0 | Tải trọng số B, ghim hash, đọc mã remote (G6); kiểm vLLM có chạy B không | Ghi chú G6, quyết định engine | 0,5 ngày |
| 1 | Xuất gold văn bản cho D1/D2 từ `gen_format_fixtures.py`; sinh D5; chép tay gold D4 | `fixtures/ocr-compare/gold/*.json` | 0,5 ngày |
| 2 | Viết `fixtures/tools/ocr_compare.py`: gửi cùng ảnh tới 2 endpoint, hậu xử lý B, chuẩn hoá, tính G1–G3/S1–S4/S8, xuất JSON + HTML tự chứa (như `ccu_translate.py`) | Script + chạy thử 5 ảnh | 1 ngày |
| 3 | Đo mức model trên D1–D5, 3 lượt, cùng engine và engine tốt nhất | `optimization/results/ocr-compare/model-*.json` | 0,5 ngày |
| 4 | Viết adapter cho B theo đúng API `/v1/ocr`, dựng trên stack thử :18285 (port riêng, không dùng 18083 production); chạy `ocr_pipeline_check.py`, tải 6/20 job, đo VRAM chung Hy-MT2 (G4, S5–S7) | `pipeline-*.json`, log VRAM | 1 ngày |
| 5 | Tổng hợp bảng điểm, biểu đồ, kết luận theo quy tắc quyết định | `optimization/results/ocr-compare/report.html` + mục trong `10-nhat-ky-toi-uu.txt` | 0,5 ngày |

Tổng: ~4 ngày công. GPU cần ~6–8 giờ.

## 5. Cần quyết định trước khi chạy

1. **GPU để đo:** (a) dùng VM Vast hiện tại — chỉ khi đo được còn đủ VRAM để nạp thêm B mà không dừng resident; hoặc (b) thuê 1 A100 80GB riêng cho thử nghiệm (cần duyệt ngân sách, ~6–8 giờ). Khuyến nghị (b) để không ảnh hưởng dịch vụ đang chạy.
2. **Gold cho trang scan thật (D4):** chép tay ~5 trang; cần người duyệt.
3. **Trọng số:** bảng trên là đề xuất; nếu ưu tiên tốc độ hơn độ chính xác thì đổi S5/S6 trước khi chạy, không đổi sau khi có số.
