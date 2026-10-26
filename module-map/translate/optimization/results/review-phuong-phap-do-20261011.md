# Review độc lập phương pháp đo tải, tốc độ và chất lượng dịch tệp/ảnh (`/ai/v2/translate`), 11/10/2026

Phạm vi: chỉ đọc mã, công cụ và số liệu. Không chạy tải, không đổi cấu hình. Đường dẫn tương đối tính từ `module-map/translate/`. `secai-opt/` là `/home/ict/secai-opt/`. Mã dịch vụ đọc ở `secai-opt/overlay/src/secai_gateway/`. Cấu hình production đọc bằng `docker inspect` (chỉ đọc) trên `secai-translation-gateway/worker`, image `translate-opt-20261011f`.

## 0. Kết luận ngắn

Các con số hiện có (docx 20 trang 4,5 s; 29,7 tài liệu/phút; ĐẠT 34/36 = 94,4%; OCR 40/40) đúng với **bộ mẫu tự sinh, lặp nội dung, chỉ có một bộ literal và chạy từng tệp một**. Chưa thể dùng chúng làm kết luận cho người dùng thật. Có 5 điểm làm sai lệch kết luận nhiều nhất:

1. **Production đang giới hạn chung cho tất cả người dùng, không theo từng người.** Mọi request tới 18185 đều đi qua proxy 8131, nên dịch vụ dịch thấy cùng một IP. Vì vậy toàn hệ thống chỉ có 5 lần tải lên/phút, 60 lần tra cứu/phút và 5 job đang hoạt động. Không bài đo nào phản ánh điều này.
2. **Bộ mẫu lặp nội dung, cộng với cơ chế ghi nhớ bản dịch theo job, làm tốc độ trông nhanh hơn thật nhiều lần.** Tệp docx 20 trang có 221 đoạn nhưng chỉ 37 đoạn khác nhau; xlsx-xl có 10.004 ô nhưng chỉ 31 chuỗi khác nhau.
3. **Literal chỉ có một bộ 7 giá trị dùng ở cả 118 mẫu**, và bản sửa OCR được viết riêng cho đúng mật khẩu đó. Kết quả 40/40 là kết quả đã khớp theo bộ mẫu (overfit), không phải năng lực tổng quát.
4. **Bài tải là một đợt bắn dồn trong vòng kín.** Tệp tối đa khoảng 5 trang, không có soak, không có thử sự cố, không tải kết quả về, không kiểm chất lượng khi đang tải.
5. **Cách tính giây/trang không nhất quán.** Cùng một tệp docx-long được tính 5 trang ở vòng 50 nhưng 1 trang ở ui20. Mẫu nhỏ (n = 6–10 mỗi loại), chỉ báo trung vị, chỉ tính mẫu thành công và có đo lại riêng các mẫu ngoại lai.

## 1. Phát hiện chi tiết

Mức: **CAO** = có thể đảo ngược kết luận đạt/không đạt. **TRUNG** = làm lệch số đáng kể. **THẤP** = cần sửa cho chặt.

### A. Tính đại diện của mẫu (câu 1)

| # | Mức | Phát hiện | Bằng chứng | Đề xuất |
|---|---|---|---|---|
| A1 | CAO | Toàn bộ kho `fixtures/formats` (118 mẫu) sinh từ cùng một văn bản khoảng 1000 từ mỗi ngôn ngữ (chủ đề ứng cứu sự cố). Gold chất lượng cũng là chính văn bản đó: 18 tệp là 1 văn bản dịch sang 9 thứ tiếng, 18 ảnh là 1 câu. | `fixtures/tools/gen_format_fixtures.py:68-76` (`sentences()` đọc `files/{lang}-1000.txt` + `text/{lang}-500*.txt`). `en-1000.txt` chỉ có 26 câu. `04-test-data.json`: FILE-* dùng chung `vi-1000.txt`, IMG-* dùng chung `ocr-vi.png`. | Thêm kho tài liệu thật, nhiều chủ đề (mục 2a). Tách bộ dùng để tinh chỉnh (dev) khỏi bộ dùng để nghiệm thu (test), bộ test giữ kín và không dùng để chỉnh ngưỡng/prompt. |
| A2 | CAO | Nội dung lặp rất nhiều. Cơ chế ghi nhớ (`memo`) chỉ dịch một lần cho các khối giống hệt nhau, nên số đo tốc độ tài liệu dài bị phóng đại. | `en-long-20pages.docx`: 221 đoạn, **37 đoạn khác nhau**, 443 câu, 55 câu khác nhau. `xlsx/xl-en.xlsx`: 10.004 ô chữ, **31 khác nhau**. `xlsx/long-en.xlsx`: 960/50. `docx/long-en.docx`: 69/45. Memo ở `translation/service.py:539-547`. Pdf-scan xl ghép đoạn `P[(pg*3+j)%len(P)]` (`gen_format_fixtures.py:400`). Các số bị ảnh hưởng: "docx 20 trang 4,5 s (0,22 s/trang)" (05 mục 0c) và "1.800 ô ~18 s" (T14). | Đo lại docx 20 trang, xlsx và PDF 20–300 trang bằng văn bản **không lặp** (tỉ lệ khối khác nhau ≥ 95%). Báo kèm số lệnh gọi MT thực tế/số khối. Giữ memo trong sản phẩm, nhưng mẫu đo không được dựa vào nó. |
| A3 | CAO | Không có tài liệu dài thật trong các vòng đo. Vòng 30/50 chỉ có tệp tối đa 5 trang (ước lượng), tệp lớn nhất chỉ khoảng 1,1 MB. Tài liệu lớn (64 trang 20 MB, 2512.19442v3.pdf 20 trang) chỉ chạy lẻ 1–2 lần, không có trong vòng lặp hay bài tải. | `l50b-loop50-prod-20261011.json`: số trang ∈ {1,3,4,5}. `pick_eval_sample.py:8,24` loại `size_class "xl"`. Manifest: trung vị 118 KB, lớn nhất 7,75 MB (pdf-scan xl, 20 trang). T38, T46 là các lần chạy đơn lẻ. | Đưa các lớp 20–300 trang và 10–50 MB vào bộ chuẩn với tỉ lệ cố định (mục 2a). |
| A4 | CAO | Ảnh và PDF scan đều sạch: render bằng Pillow + Noto, không nghiêng, không mờ, không nhiễu. Kết quả OCR (gồm 40/40 literal) không đại diện cho ảnh chụp điện thoại hay bản scan kém. | 03 mục 2 dòng 93. 04 `image_source`. `gen_format_fixtures.py:316-353`. Mục 1b.7 còn ghi "không được coi là do chất lượng ảnh khi ảnh render rõ như bộ fixture", tức chỉ đo được trường hợp dễ. | Sinh thêm biến thể suy giảm có tham số (mục 2a) và thêm ảnh chụp thật. Báo kết quả theo từng mức suy giảm. |
| A5 | TRUNG | Phân bố ngôn ngữ và hướng dịch lệch. `loadtest.py` có 10/12 tệp nguồn tiếng Anh, 100% đích tiếng Việt. Vòng 50: 50/50 cặp liên quan tiếng Việt; không có cặp en↔ja hay ngoại ngữ↔ngoại ngữ; fr chỉ 2 mẫu, ko 3. | `fixtures/tools/loadtest.py:19-26,56`. `loop50.py:69-70`. Thống kê `l50b`: en 7, ru 7, ja 6, vi 6, es 5, zh 5, de 5, ar 4, ko 3, fr 2. | Lấy mẫu phân tầng theo hướng dịch, tỉ lệ theo lưu lượng dự kiến (ví dụ en→vi 50%, vi→en 20%, CJK→vi 15%, còn lại 15%). Mỗi hướng có tối thiểu n. |
| A6 | TRUNG | Tỉ lệ định dạng chia đều (6 loại × 5) và dài:ngắn = 15:15 một cách nhân tạo. Thiếu hẳn TXT, DOC, JPEG chụp điện thoại, PDF hỗn hợp nhiều trang, xlsx nhiều sheet có công thức, docx nhiều ảnh nhúng. | `pick_eval_sample.py:5-8,16`. `loadtest.py` MIX không có .doc/.txt. | Dùng phân bố theo lưu lượng dự kiến, có lớp riêng cho tệp nặng (mục 2a). |

### B. Đo tốc độ giây/trang (câu 2)

| # | Mức | Phát hiện | Bằng chứng | Đề xuất |
|---|---|---|---|---|
| B1 | CAO | Mẫu số "trang" không nhất quán và phần lớn là ước lượng. Cùng tệp docx-long: vòng 30/50 tính **5 trang** (hằng số cứng), ui20 tính **1 trang**. Thực tế `docx/long-en.docx` có 1.469 từ, khoảng 3 trang theo quy ước 500 từ/trang; bản PDF cùng nội dung có 4 trang. XLSX tính "1 sheet = 1 trang" nên xlsx-xl 5.000 dòng = 1 trang còn xlsx-short 8 dòng cũng = 1 trang. Ảnh "long" dày chữ (3.674 ký tự) = 1 trang. TXT (500 từ/trang theo 1c.2) không có trong công cụ đo. | `secai-opt/loop50.py:74`, `loop30.py:26` (`5 if docx long`). `ui20-ias-20261011.json` (`docx-long-ar pages 1, spp 5.05`). 03 mục 1c.2. | Ghi `pages` **đo được** vào manifest: DOCX qua LibreOffice → PDF lấy số trang; PDF lấy số trang thật; TXT = từ/500. Song song báo **giây/1000 ký tự nguồn** và **giây/khối dịch** làm chỉ số độc lập định dạng. XLSX báo theo ô có chữ (ví dụ giây/100 ô) bên cạnh sheet. |
| B2 | CAO | Thời gian đo phía máy khách gồm sai số lượng tử của chu kỳ poll (1,5 s trong bench; khoảng 2 s ở UI; tự lùi tới 10 s khi bị 429) và **không gồm** thời gian tải kết quả về. Với mục tiêu 3 s cho tệp 1 trang, sai số poll tới 50%. | `secai-opt/bench.py:67,70,86` (wall chốt trước khi gọi `/result`). `loadtest.py:66-73`. T49: 8 mẫu ảnh 33–59 s là do tự lùi khi 429. API đã có `created_at`/`updated_at` độ phân giải micro giây (`api/translate_v2/routes.py:107`). | Báo hai số tách nhau: (1) **thời gian phía server** = `updated_at` lúc kết thúc − `created_at`, cộng thời gian xếp hàng (từ `stage`); (2) **thời gian đầu-cuối người dùng** = bắt đầu upload → tải xong `/result` → render, đo bằng trình duyệt (Playwright, mốc thời gian navigation và resource). Ghi rõ poll interval trong mỗi báo cáo. |
| B3 | CAO | Tập mẫu nhỏ, chỉ báo trung vị, đuôi phân bố bị che. Vòng 50 có 6–10 mẫu mỗi định dạng, không tính được p95. Chỉ tính mẫu succeeded. **Chỉ đo lại các mẫu xấu** (8 mẫu ảnh/scan, `pdf-long-ru`) rồi thay số, làm số tổng thiên về phía tốt. | T49. `loop50.py:84-86`. `loadtest.py:111` (p50/p95 chỉ trên `ok`). `l50b`: png max 49,4 s, jpg 47,3 s, pdf-scan 58,9 s. | n ≥ 30 mỗi lớp (định dạng × độ dài). Báo p50/p90/p95/max kèm khoảng tin cậy bootstrap. Mẫu thất bại hoặc timeout tính là +∞ trong phân vị (hoặc báo riêng). Khi phát hiện lỗi đo thì **chạy lại toàn bộ lượt**, không vá từng mẫu. |
| B4 | TRUNG | Phần lớn số đo trên stack thử dùng giới hạn đã nới (upload/lookup 100.000/phút, poll 0,3 s). Stack thử và production **dùng chung một GPU** (Vast 55235250) nhưng qua tunnel khác nhau. Lượt đo stack thử và người dùng production có thể làm nhiễu lẫn nhau, và log không ghi điều này. | `secai-opt/stack_exp.sh`, `stack_prodlike.sh` (cả "A = giống production" cũng có `UPLOAD/LOOKUP_RATE=100000`). T28, T29. README tối ưu "tunnel 28083->18083, 28868->18868". | Mỗi báo cáo ghi: môi trường, giới hạn đang áp, có tải khác cùng GPU không (chụp `nvidia-smi`/`vllm:num_requests_running` đầu và cuối lượt). Số nghiệm thu chỉ lấy trên cấu hình production thật, trong khung giờ không có thí nghiệm khác. |
| B5 | TRUNG | Đo khi hệ thống đang ấm, tệp lặp lại và nội dung lặp giữa các lần chạy. vLLM bật prefix caching nên các chunk giống hệt từ lần chạy trước có thể trúng cache KV. Tệp thật của người dùng là duy nhất. | T07 (`prefix caching`). T24 (làm ấm khi boot). Các vòng dùng cùng 118 tệp. | Thêm lượt "tài liệu chưa từng thấy": mỗi lượt sinh văn bản mới hoặc xáo câu và đổi giá trị. Đo riêng lần đầu sau boot (1c.2 đã yêu cầu) và lần đầu sau khoảng 30 phút không hoạt động. |
| B6 | THẤP | Đo qua UI và qua API khác nhau mà không có bảng quy đổi. ui20 tính "từ lúc chọn tệp", 1c.2 lại yêu cầu "không gồm thời gian chọn tệp". | T35. 03 dòng 42. | Thống nhất mốc bắt đầu là lúc gửi request upload (bắt bằng sự kiện network). Báo chênh UI − API theo từng lớp. |

### C. Đo tải và đồng thời (câu 3)

| # | Mức | Phát hiện | Bằng chứng | Đề xuất |
|---|---|---|---|---|
| C1 | CAO | **Giới hạn "theo IP" trên production thực chất là giới hạn chung toàn hệ thống.** Dịch vụ 18185 lấy `request.client.host`. Mọi request đến qua proxy 8131 (`host.docker.internal`), proxy không thêm `X-Forwarded-For` và uvicorn không bật `--proxy-headers`. Tất cả người dùng IAS vì vậy dùng chung một bucket: **5 lần upload/phút, 60 lần tra cứu/phút, 5 job đang hoạt động** (`MAX_ACTIVE_PER_IP=5`, nhỏ hơn `MAX_RUNNING_JOBS=6`, nên `MAX_QUEUED_JOBS=60` không bao giờ đạt tới). Chỉ cần một người mở UI tải 5 tệp, poll mỗi khoảng 2 s, là đã có khoảng 150 tra cứu/phút, đủ để chính người đó và mọi người khác nhận 429. Lượt loop50 (cách 13 s) đã chiếm trọn hạn mức upload chung trong lúc chạy. | `api/translate_v2/routes.py:127-128,249-250,263-264`. `src/secai_gateway/unified_app.py:49-52` (chỉ chuyển tiếp header gốc). Lệnh container `uvicorn ... --port 18185` không có `--proxy-headers`. Env production: không đặt `UPLOAD/LOOKUP_RATE` nên dùng mặc định 5/60 (`translation/settings.py:70-71`). T45: 24/50 `poll_error` khi bị 429. | Sửa trước mọi đo tải nghiệm thu (việc của owner, không thuộc review này): 8131 đặt `X-Forwarded-For`/`X-Real-IP` từ IP thật (lấy từ nginx IAS), 18185 chỉ tin header này khi đến từ proxy tin cậy. Tốt hơn nữa là khóa giới hạn theo **tài khoản/phiên**. Lookup nên miễn hoặc rất rộng cho job của chính người gọi. Sau đó loadtest phải mô phỏng nhiều người dùng (header khác nhau) **qua đúng đường 8131**. |
| C2 | CAO | Bài tải là một đợt bắn dồn qua barrier, vòng kín (vòng sau chờ vòng trước xong). Không có thời gian đến kiểu Poisson, không có think time, không tăng tải dần, không có trạng thái ổn định. "Thông lượng" = N / thời gian hoàn tất cả đợt, tức là đo tốc độ xả một đợt, không phải năng lực bền vững. | `loadtest.py:57,100-107`. Kết quả `load/*.json` đều 1 vòng, wall 17–195 s. | Dùng mô hình **vòng mở**: đến theo Poisson với λ tăng dần (ví dụ 0,5 → 1 → 2 → 4 tài liệu/phút/người × số người), mỗi mức giữ ≥ 15 phút. Báo đường cong độ trễ theo λ và điểm gãy (p95 vượt ngưỡng hoặc 429 > 1%). |
| C3 | CAO | Không có job dài, nên không thấy được **chặn đầu hàng (head-of-line)**. Claim theo FIFO `ORDER BY created`, không ưu tiên theo cỡ job, không công bằng theo người dùng. Sáu job PDF scan 200 trang sẽ chiếm cả 6 slot hàng chục phút. `llm_gate` 128 dùng chung, mỗi job tự mở tới 128 khối song song, nên job ngắn chỉ được khoảng 1/N phần. `pdf_ocr_gate` 16 dùng chung cho mọi PDF. Ngoài ra `pdf_extract_timeout` = 900 s có thể bị vượt khi OCR 200 trang chạy cùng tải khác. | `translation/jobs.py:243-245`. `translation/worker.py:36,162-165,224-228`. `translation/settings.py:63`. Env production: `MAX_RUNNING_JOBS=6`, `PDF_MAX_OCR_PAGES=200`, `PDF_OCR_CONCURRENCY=16`. | Kịch bản riêng: 2–6 job dài (PDF scan 100–200 trang, docx 300 trang) chạy nền, đồng thời có luồng tệp ngắn đến đều. Đo p95 của tệp ngắn so với lúc không có job dài (mục tiêu: tăng ≤ 2×), và kiểm job dài không dính `PROCESSING_TIMEOUT`. |
| C4 | CAO | "Thành công" khi đang tải không kiểm nội dung. Loadtest không gọi `/result`, không kiểm literal hay chất lượng. Lưới an toàn T38 **giữ nguyên chữ gốc** của khối lỗi kèm cảnh báo, nên job vẫn `succeeded`. Hedge (T23) và timeout 30 s nhiều lên khi tải cao có thể làm tăng số khối giữ nguyên chữ gốc mà chỉ số succeeded ≥ 95% không thấy. | `loadtest.py:76-77` (chỉ đọc `status`). `translation/specialist.py:44` (hedge `4+len/40` s). T38 `TOKEN_INTEGRITY_FALLBACK`. | Mọi job trong bài tải phải tải `/result` và chấm `check_output`/`quality_score`. Báo tỉ lệ khối fallback, `NUMERIC_MISMATCH`, khối rỗng và tỉ lệ khối chưa dịch (đầu ra trùng nguồn) theo mức tải. Ngưỡng: các tỉ lệ này khi có tải không được tăng so với khi không tải. |
| C5 | TRUNG | Hedge tự gửi trùng request khi phản hồi chậm hơn `4+len/40` s. Khi GPU bão hòa, mọi lệnh gọi đều chậm, hedge nhân đôi tải đúng lúc quá tải (hiệu ứng dồn retry). Chưa có số đo tỉ lệ hedge. | `translation/specialist.py:41-60`. | Đếm số lệnh hedge/tổng số lệnh trong bài tải. Đặt trần hedge (ví dụ ≤ 10% số lệnh đang bay) hoặc tắt hedge khi hàng chờ vLLM > ngưỡng. Đo hai chế độ bật và tắt hedge ở mức bão hòa. |
| C6 | TRUNG | Không có soak test. Đã từng hết fd (T11). Worker không đặt giới hạn bộ nhớ. Kết quả lưu dạng JSON trong SQLite 24 giờ (`result_json`, gồm cả `source_text` lẫn `translated_text` cho từng khối, nên khoảng 2–3 lần kích thước văn bản). Mỗi lần poll là một transaction ghi (`rate()` + `_expire_overdue_jobs`), và `get` đọc `SELECT *` kèm blob kết quả. WAL và DB sẽ phình khi có nhiều tài liệu lớn. | `translation/jobs.py:152-162,118-130,276-283`. `worker.py:244-256`. `settings.py:79` (`result_ttl` 24 h). `docker inspect`: Memory 0 (không giới hạn). | Soak 2–4 giờ ở khoảng 60% năng lực (mục 2c), lấy mẫu mỗi phút: RSS gateway/worker, số fd, số luồng, kích thước `jobs.sqlite3` và `-wal`, dung lượng thư mục artifact, VRAM. Thêm bài poll dày (nhiều job) để đo độ trễ ghi SQLite và lỗi `database is locked`. |
| C7 | TRUNG | Không đo kích thước và thời gian tải kết quả, cũng không đo timeout của chuỗi proxy. Upload được đọc toàn bộ vào RAM (≤ 50 MB × 2 do `join`). Proxy 8131 giới hạn `max_connections=32` dùng chung với report, timeout 600 s. nginx/IAS chưa rõ giới hạn body và timeout. Kết quả của tài liệu 300 trang có thể nặng vài MB, ảnh hưởng FE. | `api/translate_v2/routes.py:132-139`. `unified_app.py:25-27`. | Đo theo lớp tệp: kích thước `/result`, thời gian tải và render trên trình duyệt. Thử 5 upload 50 MB đồng thời qua ias.id.vn (độ dài body, timeout, RSS gateway). Bài "upload chậm" (băng thông 1–5 Mbit/s) để xem có chiếm hết 32 kết nối của proxy không. |
| C8 | TRUNG | Chưa thử sự cố. `lease` 60 s, heartbeat 15 s, `max_reclaims=1`: restart worker giữa chừng thì job chờ khoảng 60 s rồi **chạy lại từ đầu**, và restart lần 2 thì thất bại. Đứt tunnel hoặc Vast thì chưa rõ job lỗi gọn hay treo tới deadline 7.200 s. Tunnel SSH đơn kết nối từng gây treo khoảng 1/360 lệnh gọi. | `settings.py:82-84`. `jobs.py:236-255`. README tối ưu "Treo 30 s ngẫu nhiên". | Bộ thử sự cố ở mục 2d. |
| C9 | THẤP | Chi tiết công cụ: p95 kiểu nearest-rank với n = 5 thực chất là max. 429 lúc upload không bị tính vào tỉ lệ lỗi, nên có thể từ chối 90% mà vẫn "PASS". Số trang trong MIX là hằng số cứng. Không gửi token (không đo được đường có xác thực). | `loadtest.py:85-87,110-124,19-26,59`. | Báo tỉ lệ 429/tổng và tách theo RATE_LIMITED/QUEUE_FULL, kèm ngưỡng (ví dụ ≤ 1% ở tải danh định). Số trang lấy từ manifest đo thật. Thêm `--token-file`. |

### D. Chất lượng và giữ nguyên thông tin (câu 4)

| # | Mức | Phát hiện | Bằng chứng | Đề xuất |
|---|---|---|---|---|
| D1 | CAO | **Literal bị overfit.** Cả 118 mẫu `formats` dùng đúng một bộ 7 giá trị (`svc_backup_01`, `P@ss!w0rd#2026$%^&*()_+`, …). Bản sửa OCR được viết theo đúng mật khẩu này: `SPECIALS = "!@#$%^&*()_+~|\<>{}[]"` thiếu `= : ; ' " , . ? / \` -`; prompt đọc lại liệt kê nguyên văn `! @ # $ % ^ & * ( ) _ +`; chỉ đọc lại token có ≥ 4 ký tự đặc biệt. Mật khẩu thường gặp như `Tr0ub4dor&3`, `S3cr3t!`, `a=b;c`, khóa base64 hay JWT **không kích hoạt** bước đọc lại. Nhầm 0/O, l/1/I và rớt `=` cuối base64 thì không được xử lý. | `fixtures/formats/manifest.json` (1 bộ literal duy nhất). `optimization/serving/fast_ocr_amp.py:175,178-179,220-221`. T27, T43 tinh chỉnh trên cùng 40 ảnh. 1b.7 ngưỡng "40 ảnh". | Tạo **bộ literal sinh ngẫu nhiên theo seed cho mỗi mẫu** (mục 2a-iv). Giữ một bộ test kín chưa từng dùng để tinh chỉnh. Báo tỉ lệ giữ literal theo **lớp ký tự**, không chỉ theo mẫu. |
| D2 | CAO | chrF chỉ chấm trên `.txt` (18 tệp, cùng 1 văn bản) và ảnh 1 câu (18 ảnh, cùng 1 câu), tức là chỉ 2 nội dung nguồn. Không chấm docx/pdf/xlsx/pdf-scan, nơi trích xuất, chunk và thứ tự đọc dễ làm hỏng nghĩa. Ảnh 1 câu cho chrF dao động mạnh (đúng 2 mẫu trượt ở T41). | 03 mục 1d dòng 54. `quality_score.py:208-227`. T41. | Tạo gold cho bộ tài liệu cấu trúc bằng song ngữ có sẵn và hợp pháp (mục 2a-ii). Mỗi hướng ≥ 300 câu hoặc ≥ 5.000 từ. Thêm metric thứ hai ít phụ thuộc bề mặt (COMET/xCOMET hoặc MetricX chạy offline; tối thiểu chrF++ hoặc BLEU qua sacrebleu) và kiểm tra lại bằng người trên mẫu nhỏ. |
| D3 | TRUNG | Ngưỡng chrF lấy từ số đo của **chính hệ thống** (BF16) rồi trừ 12–17 điểm, nên vòng lặp tự xác nhận và chỉ bắt được hỏng nặng. Gold "dịch thủ công" chưa được người dịch kiểm lại. | 03 dòng 64-72. `quality_score.py:35-39`. 05 dòng 176. | Neo ngưỡng vào một hệ tham chiếu độc lập (ví dụ một MT thương mại hoặc LLM lớn chạy offline trên cùng gold), hoặc vào điểm đánh giá của người (MQM rút gọn, 50 đoạn/hướng). Đặt ngưỡng tương đối: không thấp hơn tham chiếu quá X điểm. |
| D4 | TRUNG | Không chấm chất lượng khi tải và khi suy giảm. Chưa có so sánh chất lượng giữa 1 job và lúc bão hòa (timeout, hedge, fallback). Chất lượng OCR (`ocr_chrf`) chỉ để tham khảo, không vào cổng. | `quality_score.py:398-399`. `loadtest.py`. | Chạy `quality_score` trên đầu ra của bài tải và bài suy giảm ảnh. Đưa CER của OCR (so `ocr_text` gold) thành cổng theo mức suy giảm. |
| D5 | THẤP | Cổng G4 dùng `int(0.1*gl)`, nên văn bản dưới 10 dòng không được lệch dòng nào. Đếm khối cho ảnh lệ thuộc bố cục. G2 có thể trượt oan với de/ru (T41: IMG-vi-ru 1,33). | `quality_score.py:191,40`. | Đặt dung sai tối thiểu là 1 dòng/khối và nới G2 riêng cho de/ru/ar với câu ngắn, hoặc chỉ áp G2 cho mẫu ≥ 400 ký tự. |

## 2. Kế hoạch đo sát thực tế (câu 5)

### 2a. Bộ dữ liệu đề xuất: "REAL-200", khoảng 200 tệp, chia dev/test 50/50, test giữ kín

| Lớp | Tỉ lệ | Nội dung | Nguồn hợp pháp / cách sinh |
|---|---|---|---|
| PDF chữ dài 20–300 trang | 15% | Bài khoa học nhiều bảng, công thức, hình, chú thích, 2 cột | arXiv (giấy phép CC-BY, chọn bài có license), RFC (IETF, đọc lại công khai), NIST SP 800-x (công khai, US Gov), báo cáo ENISA/CISA |
| PDF chữ ngắn 1–10 trang | 10% | Advisory, hóa đơn, slide xuất PDF | CISA advisories, vendor advisory, slide tự xuất từ LibreOffice |
| PDF scan 1–200 trang | 15% | Bản scan thật và bản giả lập scan kém | In PDF ra ảnh rồi suy giảm (bên dưới). Kèm 10–20 bản quét thật từ máy scan văn phòng (tài liệu công khai) |
| Ảnh chụp điện thoại | 15% | Màn hình terminal, slide, giấy in, biển báo; nghiêng, mờ, lóa, nhiều cột | Tự chụp tài liệu công khai đã in. Giả lập bằng phép biến đổi phối cảnh |
| DOCX 1–300 trang | 20% | Nhiều bảng lồng, ảnh nhúng, footnote, header/footer, tracked changes, TOC | Văn bản pháp luật/chính sách công khai (vbpl, EUR-Lex có bản song ngữ) tải về DOCX. Tài liệu tự soạn |
| XLSX | 10% | Nhiều sheet, công thức, ô gộp, 5.000–50.000 ô, log SIEM | Dữ liệu mở (data.gov), CVE list export (MITRE/NVD JSON → xlsx) |
| DOC / TXT / JSON / log | 10% | Tệp cũ .doc, log dài, cấu hình | Chuyển docx → doc bằng LibreOffice. Log tự sinh |
| Kích thước | — | 60% < 1 MB, 25% 1–10 MB, 15% 10–50 MB | — |
| Ngôn ngữ (hướng) | — | en→vi 45%, vi→en 20%, zh/ja/ko→vi 15%, fr/de/es/ru/ar→vi 10%, ngoại ngữ↔ngoại ngữ / vi→CJK 10% | — |

**i. Gold chất lượng.** Dùng ngữ liệu song song có sẵn để không tự sinh gold: EUR-Lex (en/fr/de/es), UN Parallel Corpus (en/zh/ru/ar/fr/es), văn bản pháp luật Việt Nam có bản dịch tiếng Anh chính thức, FLORES-200 devtest (có vi, câu rời nhưng giấy phép rõ), WMT test sets. Ghép câu song song thành DOCX/PDF/XLSX có cấu trúc rồi chấm theo khối.

**ii. Giả lập scan kém (ghi tham số theo seed vào manifest).** Dùng Pillow/OpenCV và augraphy:
- xoay ±0,5–5°, biến đổi phối cảnh (góc chụp 5–25°);
- mờ Gauss σ 0,5–2, mờ chuyển động;
- nhiễu muối tiêu và Gauss;
- JPEG chất lượng 30–70, sau đó nén lại thêm lần nữa;
- độ phân giải 150/200/300 DPI;
- giảm tương phản, nền giấy ngả vàng;
- đè dấu đỏ hoặc chữ ký;
- bóng đổ, lóa sáng;
- cong gáy sách.

Mỗi ảnh gốc sinh 3 mức nhẹ / vừa / nặng.

**iii. Không lặp.** Tỉ lệ khối khác nhau ≥ 95% trong mỗi tài liệu đo tốc độ. Ghi `unique_blocks/total_blocks` vào manifest.

**iv. Literal ngẫu nhiên theo seed, chèn 5–15 giá trị mỗi tệp, xuất hiện ở văn xuôi, ô bảng, chú thích và ảnh.** Các nhóm:
- mật khẩu 8–32 ký tự có đủ lớp `0/O`, `l/1/I`, `rn/m`, mọi ký tự ASCII in được kể cả `= ; : ' " , . ? / \ \` ~ -`;
- mật khẩu chứa Unicode/RTL (`Pass_سر_2026`, `密码Ab#1`) và dấu cách bên trong;
- URL có query dài, ký tự `%XX`, fragment `#`;
- IPv6 đầy đủ và rút gọn (`2001:db8::1`, `[fe80::1%eth0]:8443`);
- JWT 3 phần; khóa PEM nhiều dòng (`-----BEGIN ... -----`); base64 có `==`;
- SĐT (`+84 912 345 678`, `(028) 3823-4567`), IBAN, số tài khoản `0123 4567 8901`;
- số kiểu Việt và Mỹ (`1.250.000,00` / `1,250,000.00`), ngày `dd/mm/yyyy`;
- đường dẫn Windows `C:\Program Files\...`, hash SHA-256, chuỗi kết nối, lệnh `curl -H "..."`.

Giá trị phải sinh mới cho bộ test, không trùng bộ dev.

### 2b. Kịch bản người dùng ảo (k6 hoặc Locust, chạy **qua ias.id.vn hoặc 8131**, mỗi người dùng có IP/tài khoản riêng sau khi sửa C1)

- **Đến:** Poisson. Số người đồng thời theo bậc 1 → 5 → 10 → 20 → 40, mỗi bậc giữ 15 phút, tăng dần trong 5 phút.
- **Phiên giống UI:** chọn 1–5 tệp (phân bố 1: 50%, 2–3: 35%, 4–5: 15%), upload song song như FE, poll mỗi 2 s, tải `/result`, think time log-normal trung vị 60 s, 30% số phiên tải thêm tệp DOCX/TXT kết quả, 5% hủy giữa chừng.
- **Kịch bản hỗn hợp nặng:** 10% số phiên gửi tệp 100–300 trang hoặc 20–50 MB. Một kịch bản "kẻ phá" gửi liên tục tối đa hạn mức để kiểm tính công bằng.

### 2c. Chỉ số và ngưỡng đạt (đề xuất, owner chốt)

| Chỉ số | Ngưỡng |
|---|---|
| Tốc độ phía server, không tải (n ≥ 30/lớp) | Chữ: p95 ≤ 3 s/trang. Ảnh/scan 1 trang: p95 ≤ 10 s (nếu owner chấp nhận ngưỡng tổng cho ≤ 3 trang, 05 mục 0c). Scan nhiều trang: p95 ≤ 3 s/trang. 20 trang ≤ 60 s |
| Đầu-cuối UI ở tải danh định (10 người) | p95 ≤ 1,5 × số không tải. Lỗi 5xx/đứt kết nối = 0. 429 ≤ 1% số request |
| Job ngắn khi có job dài chạy nền | p95 ≤ 2 × số không tải |
| Thông lượng bền vững | Báo trang/phút và tài liệu/phút tại điểm p95 còn đạt; ghi rõ là trên 1×A100 |
| Giữ literal (bộ test ngẫu nhiên) | Chữ: 100%. Ảnh/scan nhẹ: ≥ 99,5% theo literal. Vừa: ≥ 98%. Nặng: báo, không chặn. Báo theo lớp ký tự |
| Chất lượng | chrF/COMET trên gold độc lập, không thấp hơn hệ tham chiếu quá ngưỡng owner chốt. Khối fallback ≤ 0,5%, không tăng khi có tải |
| Soak 2–4 giờ ở 60% năng lực | RSS và fd tăng ≤ 10% sau 30 phút đầu. WAL ≤ 256 MB. Không có `database is locked` và không có job treo quá deadline |

### 2d. Thử sự cố (mỗi bài có job đang chạy cả ngắn lẫn dài)

| Sự cố | Kết quả mong đợi |
|---|---|
| Ngắt tunnel/Vast 30 s, rồi 5 phút | Job hiện tại `failed` với mã rõ (`DEPENDENCY_UNAVAILABLE`/`PROCESSING_TIMEOUT`) trong ≤ thời hạn cấu hình, hoặc tự tiếp tục khi tunnel về. Không treo tới 7.200 s. Upload mới bị từ chối gọn hoặc xếp hàng |
| `docker restart` worker giữa job 200 trang (1 lần, 2 lần) | Lần 1: được lấy lại sau ≤ 60 s lease và hoàn tất. Lần 2: `failed` có mã. Không có job mồ côi ở `running` |
| MT chậm (độ trễ +5 s hoặc treo 50% request bằng tc/netem hoặc proxy chèn độ trễ) | Hedge không nhân tải quá trần. Timeout 30 s kích hoạt. Tỉ lệ fallback được báo |
| OCR trả 503 hoặc trống | Job ảnh/scan `failed` có lý do, hoặc trang lỗi được đánh dấu `needs_review`. Không bịa chữ |
| GPU đầy VRAM (tiến trình khác chiếm) | Admission hoặc health báo không sẵn sàng, không có 5xx giả "thành công" |
| Đầy đĩa artifact hoặc vượt quota 5 GB | 429/507 gọn (`STORAGE_*`), job cũ không hỏng |
| SQLite WAL lớn và poll dày (200 job × poll 1 s) | Độ trễ API p95 < 200 ms, không có `locked` |
| Upload 5 × 50 MB song song qua IAS, upload băng thông thấp | Không timeout proxy. Không làm nghẽn kết nối của người khác |

### 2e. Thứ tự ưu tiên (theo mức ảnh hưởng tới độ tin cậy kết luận)

1. **C1:** sửa và xác minh danh tính client qua proxy, sau đó đo lại tất cả số tải trên đường 8131 hoặc IAS. Không có bước này thì mọi kết luận về tải trên production đều sai.
2. **A2 + B1:** bộ mẫu không lặp và số trang đo thật; đo lại docx 20 trang, xlsx, PDF 20–300 trang.
3. **D1:** literal ngẫu nhiên theo seed và bộ test kín; chạy lại 1b.7 (OCR) và 1b.
4. **B2/B3:** dùng thời gian phía server và đầu-cuối, n ≥ 30/lớp, p95 kèm khoảng tin cậy, không vá từng mẫu.
5. **C3 + C4:** bài head-of-line với job dài, kiểm nội dung khi có tải (fallback, literal).
6. **A4:** suy giảm ảnh/scan theo 3 mức và ảnh chụp thật.
7. **C2:** tải vòng mở theo bậc. **C6:** soak 2–4 giờ.
8. **C8 / 2d:** thử sự cố.
9. **D2/D3:** gold độc lập cho tài liệu cấu trúc, metric thứ hai, ngưỡng neo vào tham chiếu.
10. Các mục THẤP (B6, C9, D5).
