# Kết quả CCU dịch qua production 8131 — 13–14/10/2026

Đã kết thúc phép đo: **1538 yêu cầu tải**, 1528 xử lý thành công; preflight kiểm **155/155 mẫu**. Gateway: `http://127.0.0.1:8131`. 48 lượt/16 mức, thời gian đo chính thức khoảng **5.75 giờ**, chưa gồm preflight/chuẩn bị. 128/135 mẫu chính đã xuất hiện trong tải; mọi mẫu đều có kiểm preflight. Giữ nguyên giới hạn/dịch vụ production. Kết quả này đo tải qua API, không phải thời gian thao tác trên giao diện IAS.

[Báo cáo HTML](report.html) · [JSON thô](results.json) · [Đánh giá dẫn xuất](assessment.json) · [Manifest](../../../fixtures/ccu-within-limits/manifest.json)

## Bộ dữ liệu và cách chạy

- 135 mẫu chính + 20 ca khó; 5 bản/ô định dạng × độ dài, 7 literal riêng/mẫu; tổng tệp đầu vào khoảng 201 MB.
- DOCX/DOC/PDF chữ và PDF scan: **10/30/60 trang**; DOC/DOCX xác minh qua LibreOffice render; scan không có lớp chữ.
- TXT: **2.000/5.000/10.000 từ**; XLSX: **1.000/3.000/4.900 dòng**, 3 sheet, tối đa 19.620 ô; PNG/JPG: **500/1.000/1.500 từ**; text v3: **200/350/450 từ**, phù hợp giới hạn 16 chunk specialist.
- Ca khó: ảnh mờ/nghiêng/nền tối và PDF 30 trang trộn chữ/scan; kiểm tuần tự, không trộn vào baseline tải ảnh sạch.
- Mỗi mức 3 lượt; nhận tải 180 giây, ramp 30 giây, xả tối đa 600 giây, nghỉ 40 giây giữa lượt và mức tải thành công (chuyển nhóm sau stop rule không có cooldown riêng). VU gửi một yêu cầu, chờ kết quả rồi nghỉ 5–20 giây. Sampler v2 xáo trộn ô và bản mẫu, 3 seed công bố trong JSON.
- Tải mixed phân bổ gần 25% **số lần gửi** cho mỗi nhóm text/files/images/scan; không phải 25% tài nguyên hay 25% người dùng thực. Không giả IP, không nới rate limit.

## Kết quả từng nhóm

| Nhóm | Mức đã đo | Thành công/lần gửi | Giữ đủ literal/thành công | Mẫu duy nhất khi tải |
|---|---|---:|---:|---:|
| files | 5, 10, 20, 25, 30 | 436/436 | 436/436 | 70/75 |
| images | 5, 10, 20, 25, 30 | 744/744 | 627/744 | 30/30 |
| scan | 5, 10, 20 | 133/140 | 133/133 | 15/15 |
| mixed | 5, 10, 20 | 215/218 | 209/215 | 71/135 |

**PASS trong bảng HTML chỉ là xử lý**: không từ chối/lỗi hạ tầng và ≥99% yêu cầu được nhận hoàn tất. Nó không chứng minh đạt thời gian chờ, giữ literal hoặc nghĩa dịch.

| Nhóm | VU | Thành công/lần gửi | p95 giây | Tài liệu hoặc yêu cầu/phút | Literal đạt | Xử lý thô |
|---|---:|---:|---:|---:|---:|---|
| files | 5 | 47/47 | 181.634 | 3.45 | 47/47 | PASS |
| files | 10 | 64/64 | 276.162 | 3.61 | 64/64 | PASS |
| files | 20 | 92/92 | 413.501 | 3.57 | 92/92 | PASS |
| files | 25 | 108/108 | 525.9 | 3.55 | 108/108 | PASS |
| files | 30 | 125/125 | 570.038 | 3.55 | 125/125 | PASS |
| images | 5 | 87/87 | 26.841 | 9.11 | 73/87 | PASS |
| images | 10 | 130/130 | 42.93 | 12.96 | 109/130 | PASS |
| images | 20 | 163/163 | 88.967 | 12.97 | 138/163 | PASS |
| images | 25 | 175/175 | 108.315 | 12.9 | 148/175 | PASS |
| images | 30 | 189/189 | 130.705 | 12.9 | 159/189 | PASS |
| scan | 5 | 26/26 | 196.044 | 1.64 | 26/26 | PASS |
| scan | 10 | 42/42 | 363.788 | 1.68 | 42/42 | PASS |
| scan | 20 | 65/72 | 671.798 | 1.67 | 65/65 | FAIL |
| mixed | 5 | 46/46 | 172.442 | 3.89 | 44/46 | PARTIAL |
| mixed | 10 | 68/68 | 228.854 | 4.22 | 66/68 | PARTIAL |
| mixed | 20 | 101/104 | 331.438 | 4.34 | 99/101 | FAIL |

## Đánh giá và giới hạn

- files: 436/436 hoàn tất, 436/436 giữ đủ literal; đo các mức [5, 10, 20, 25, 30]. p95 từ 181.6 giây (VU 5) đến 570.0 giây (VU 30); thông lượng tương ứng 3.45 và 3.55 yêu cầu/phút.
- images: 744/744 hoàn tất, 627/744 giữ đủ literal; đo các mức [5, 10, 20, 25, 30]. p95 từ 26.8 giây (VU 5) đến 130.7 giây (VU 30); thông lượng tương ứng 9.11 và 12.9 yêu cầu/phút.
- scan: 133/140 hoàn tất, 133/133 giữ đủ literal; đo các mức [5, 10, 20]. p95 từ 196.0 giây (VU 5) đến 671.8 giây (VU 20); thông lượng tương ứng 1.64 và 1.67 yêu cầu/phút.
- Tệp chữ/bảng nhận và xử lý được tải đến VU 30, nhưng thông lượng gần như không tăng khi tăng VU; thời gian chờ tăng mạnh. Đây là kết quả xử lý đã thử, chưa phải CCU đạt SLA.
- Ảnh có lỗi literal chủ yếu ở chuỗi mật khẩu qua OCR và có biến động giữa các lần đo. Xử lý thành công chưa đủ nghiệm thu yêu cầu giữ nguyên thông tin.
- PDF scan: mức cuối đã đo 20; mức chưa chạy theo stop rule: 25 và 30. Không suy ra trần GPU hoặc CCU tối đa tuyệt đối.
- Mixed: hoàn tất 215/218; giữ đủ literal 209/215; mẫu duy nhất 71/135; 3 lỗi TRANSLATION_TIMEOUT (HTTP 504). Mẫu text-within-limits-short-vi-ja-03 bị loại sau preflight vì OUTPUT_VALIDATION_FAILED (HTTP 502), dù đầu vào hợp lệ; chỉ phần sẵn sàng được đưa vào tải. Các mức không lỗi xử lý: 5 và 10. TRANSLATION_TIMEOUT ở text là deadline 120 giây do dịch vụ trả về, tách biệt với lỗi polling tại hạn run 780 giây. Chất lượng và lỗi phải đọc cùng với tỷ lệ thành công, không chỉ PASS/PARTIAL.
- Có **7 lỗi phù hợp ranh giới thời gian benchmark 780 giây**. Raw status/error giữ nguyên; xem [chẩn đoán deadline](deadline-diagnostics.json). Runner đo thiếu guard sau sleep, có thể ghi poll_error/TimeoutError khi đã hết hạn đo. Các yêu cầu này thực sự chưa lấy được kết quả trong hạn, nên vẫn tính không đạt; không coi chúng là bằng chứng gateway mất kết nối hay hết TTL job 7.200 giây.
- Ngưỡng p95 ngắn ≤30 giây, dài ≤120 giây là **tham chiếu từ bộ cũ nhỏ hơn**; mức vừa ≤120 giây là giả định tạm. JSON đánh giá báo riêng theo size, không áp tên “short” để kết luận hai bộ tương đương. Chưa xác nhận một CCU đáp ứng đầy đủ thời gian + chất lượng cho bộ lớn mới. Mixed 10 là mức cao nhất trong các mức đã thử không có lỗi xử lý trên tập sẵn sàng; chưa thử mức 11–19 để chốt ngưỡng chính xác.
- p95 chỉ tính trên yêu cầu thành công, dùng sorted[round(p/100×(n−1))]; không lấy trung bình p95 từng lượt. Các timeout không nằm trong p95 và được báo riêng. Thông lượng gồm ramp + drain thực tế, không gồm cooldown.
- Đa số ô files/scan/mixed có n<20 ở từng mức; p95 từng ô chỉ tham khảo, chưa có khoảng tin cậy. Mẫu lặp giữa các lượt, không phải soak test liên tục.
- Chỉ tính trang đã xác minh cho DOC/DOCX/PDF/scan. Trong JSON/HTML thô, ảnh có pages=1 là một **đơn vị ảnh**, không phải phân trang tài liệu; số s/đơn vị nguồn ở mixed trộn hai loại đơn vị nên không dùng nghiệm thu s/trang. XLSX/TXT không có số trang cố định.
- Cảnh báo từ result được giữ trong JSON đánh giá (warning_codes/requests_with_warnings), gồm NUMERIC_MISMATCH ở tệp chữ/bảng và cảnh báo ngôn ngữ ở text; chưa đối chiếu mọi trị số ngoài 7 literal.
- Giữ đủ 7 literal không chứng minh nghĩa dịch, độ đầy đủ văn bản, bố cục hay OCR CER. Gold là chữ nguồn, chưa có bản dịch chuẩn. Mixed PDF giữ literal ở phần chữ không chứng minh nửa scan đúng.
- Nguồn chủ yếu Latin (Anh/Việt/Pháp), có đích Nhật/Đức; chưa phủ nguồn CJK/RTL/mọi cặp ngôn ngữ. Dữ liệu dùng nhiều mẫu câu chung dù ID khác: [kiểm lặp nguồn](source-repetition.json) là proxy theo dòng/câu, không phải memo-hit rate của worker. Warm/repeated samples và tải nền production chưa xác minh đầy đủ có thể ảnh hưởng thông lượng.
- Số liệu cũ ngày 11/10 khác dataset, độ dài và môi trường; không dùng chênh lệch để kết luận backend đã nhanh/chậm hơn. Không thay backend/serving để làm kết quả đạt.

## Coverage theo định dạng × độ dài

| Nhóm | Định dạng | Size | Sample preflight | Sample duy nhất khi tải | Lần gửi | Thành công | Literal đạt |
|---|---|---|---:|---:|---:|---:|---:|
| difficult | pdf-mixed | medium | 5/5 | 0/5 | 0 | 0 | 0 |
| difficult | png | medium | 15/15 | 0/15 | 0 | 0 | 0 |
| files | doc | long | 5/5 | 5/5 | 37 | 37 | 37 |
| files | doc | medium | 5/5 | 4/5 | 37 | 37 | 37 |
| files | doc | short | 5/5 | 4/5 | 38 | 38 | 38 |
| files | docx | long | 5/5 | 5/5 | 32 | 32 | 32 |
| files | docx | medium | 5/5 | 5/5 | 35 | 35 | 35 |
| files | docx | short | 5/5 | 5/5 | 34 | 34 | 34 |
| files | pdf | long | 5/5 | 5/5 | 37 | 37 | 37 |
| files | pdf | medium | 5/5 | 5/5 | 26 | 26 | 26 |
| files | pdf | short | 5/5 | 5/5 | 32 | 32 | 32 |
| files | txt | long | 5/5 | 5/5 | 28 | 28 | 28 |
| files | txt | medium | 5/5 | 5/5 | 36 | 36 | 36 |
| files | txt | short | 5/5 | 5/5 | 28 | 28 | 28 |
| files | xlsx | long | 5/5 | 4/5 | 37 | 37 | 37 |
| files | xlsx | medium | 5/5 | 5/5 | 27 | 27 | 27 |
| files | xlsx | short | 5/5 | 3/5 | 29 | 29 | 29 |
| images | jpg | long | 5/5 | 5/5 | 136 | 136 | 136 |
| images | jpg | medium | 5/5 | 5/5 | 132 | 132 | 107 |
| images | jpg | short | 5/5 | 5/5 | 136 | 136 | 93 |
| images | png | long | 5/5 | 5/5 | 132 | 132 | 131 |
| images | png | medium | 5/5 | 5/5 | 130 | 130 | 117 |
| images | png | short | 5/5 | 5/5 | 132 | 132 | 91 |
| scan | pdf-scan | long | 5/5 | 5/5 | 64 | 61 | 61 |
| scan | pdf-scan | medium | 5/5 | 5/5 | 67 | 64 | 64 |
| scan | pdf-scan | short | 5/5 | 5/5 | 63 | 62 | 62 |
| text | text | long | 5/5 | 5/5 | 16 | 13 | 13 |
| text | text | medium | 5/5 | 4/5 | 18 | 18 | 18 |
| text | text | short | 5/5 | 4/5 | 19 | 19 | 19 |

## Preflight, bằng chứng và tái lập

Preflight: số xử lý/literal từng nhóm đã đối chiếu với preflight-summary.json và ghi trong assessment.json (preflight). Một text vi→ja bị OUTPUT_VALIDATION_FAILED dù đầu vào đạt giới hạn. Các mẫu thành công nhưng thiếu literal vẫn được giữ trong tải, không lọc để cải thiện điểm.

Dữ liệu thô từng lượt, log terminal, outputs và owned-jobs.jsonl được giữ. Pilot có sampler cũ được lưu riêng, không gộp vào phép đo này. Hash manifest/mọi đầu vào, nội dung/literal output, tổng hợp và registry đã kiểm tra; hash output được lập tại lúc đánh giá, không phải tại thời điểm request hoàn tất; không còn job benchmark đang hoạt động.

Code thực sự dùng khi đo lưu tại runner-snapshot/; config giữ runner SHA và execution-state giữ helper SHA. Công cụ hiện tại đã sửa guard deadline và thêm helper fingerprint sau khi đo; dùng snapshot để đối chiếu/tái lập phiên cũ và output mới khi chạy bản mới. Kết quả kiểm thử và snapshot runtime cuối nằm trong verification.json.
