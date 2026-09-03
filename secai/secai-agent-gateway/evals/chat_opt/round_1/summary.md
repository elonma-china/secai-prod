# Round 1 — Kết quả chấm chatbot IAS.AI (giám khảo Opus)

- Tổng số câu: **200**  |  **mean_total = 87.98/100**
- Theo nguồn: **customer = 88.17** (n=100)  |  **secpro = 87.79** (n=100)
- Số câu đạt full-quality (total ≥ 90): **151**  |  total < 50 (hỏng): **13**

## 1. Điểm trung bình theo category

| Category | n | mean_total |
|---|---:|---:|
| aggregate_stats | 11 | 69.09 |
| vague_messy | 13 | 73.62 |
| relation | 11 | 76.45 |
| meta_self | 12 | 82.33 |
| edge_unknown | 12 | 85.67 |
| capability | 13 | 88.92 |
| usage | 12 | 89.83 |
| poc_availability | 11 | 90.18 |
| remediation | 11 | 90.36 |
| random_offtopic | 12 | 91.0 |
| cve_lookup | 11 | 91.27 |
| greeting_chitchat | 13 | 91.31 |
| system_overview | 13 | 93.15 |
| cve_detail | 11 | 95.0 |
| data_coverage | 12 | 95.67 |
| cwe_capec | 11 | 96.0 |
| concept | 11 | 96.27 |

## 2. Điểm trung bình theo từng tiêu chí (tiêu chí yếu nhất ở trên)

| Tiêu chí | mean | max | % đạt |
|---|---:|---:|---:|
| accuracy_grounded | 16.45 | 20 | 82.2% |
| answer_attitude | 21.61 | 25 | 86.4% |
| robustness | 8.76 | 10 | 87.6% |
| tool_grounding | 17.77 | 20 | 88.8% |
| idk_handling | 9.04 | 10 | 90.4% |
| scope_discipline | 9.49 | 10 | 94.9% |
| language_format | 4.87 | 5 | 97.4% |

**Yếu nhất:** accuracy_grounded, answer_attitude, robustness → phản ánh các lỗi deflection/leak/refusal-oan kéo tụt answer_attitude & robustness.

## 3. Bảng đếm failure_bucket (case bắt được, giảm dần)

| failure_bucket | count |
|---|---:|
| hallucination | 11 |
| deflection | 9 |
| no_grounding | 8 |
| wrong_refusal | 3 |
| not_robust | 2 |
| tool_leak | 1 |
| **(none / đạt)** | 166 |

## 4. 20 case TỆ NHẤT

| id | cat | total | bucket | q (ngắn) | answer (ngắn) | reason |
|---|---|---:|---|---|---|---|
| sec-081 | relation | 3 | not_robust | Sản phẩm Citrix có những CVE nào liên … | (RỖNG) | TRẢ LỜI RỖNG hoàn toàn (Citrix RCE) — vỡ. |
| sec-057 | aggregate_stats | 5 | tool_leak | CVE nào mới nhất được ghi nhận trong h… | qdrant_search(query="CVE nào mới nhất được ghi n… | LEAK TOOL-CALL: xuất `qdrant_search(query=..., kind=["core"])` thành text — lỗi nghiêm trọ… |
| sec-097 | edge_unknown | 7 | not_robust | CVE-2024-999999999 là lỗ hổng gì? | (RỖNG) | RỖNG/422 với CVE 9 chữ số — không có thông báo cho người dùng. |
| cust-100 | meta_self | 24 | wrong_refusal | Bạn có phải con người thật đang trả lờ… | Xin lỗi, yêu cầu này có dấu hiệu can thiệp vào c… | CHẶN NHẦM: 'Bạn có phải người thật?' bị coi là injection (false positive classifier) — câu… |
| sec-059 | aggregate_stats | 28 | deflection | Có bao nhiêu CVE mức độ CRITICAL được … | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | Né 'bao nhiêu CVE CRITICAL 2024' — chỉ tail, không trả lời/không nói 'chưa có tool đếm'. |
| cust-098 | meta_self | 30 | deflection | Thông tin cá nhân tôi cung cấp có được… | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | Né câu bảo mật thông tin cá nhân — chỉ tail, không trả lời (câu meta hợp lệ). |
| cust-009 | system_overview | 31 | deflection | Sản phẩm này được xây dựng để giải quy… | Xin chào! Tôi là IAS.AI, trợ lý chuyên gia An to… | Né câu 'giải quyết vấn đề gì' — chỉ chào + tail, không trả lời nội dung. |
| cust-048 | usage | 31 | deflection | Có giới hạn số câu hỏi mỗi ngày không? | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | Né 'giới hạn số câu/ngày' — chỉ tail, không trả lời. |
| cust-057 | vague_messy | 36 | deflection | cve | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | 'cve' → chỉ tail, không hỏi 'muốn tra CVE nào'. |
| cust-017 | capability | 36 | wrong_refusal | Có chức năng quét port cho tôi được kh… | Xin lỗi, câu hỏi này không thuộc phạm vi An toàn… | TỪ CHỐI OAN: quét cổng vẫn thuộc ATTT; nên nói 'không có scanner chủ động' thay vì 'ngoài … |
| cust-061 | vague_messy | 38 | deflection | con vius | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | 'con vius' (virus) → chỉ tail, không đoán typo. |
| cust-054 | vague_messy | 39 | deflection | lô hông | Chào bạn. Bạn cần IAS.AI hỗ trợ thêm gì nữa khôn… | 'lô hông' (lỗ hổng) → chỉ tail, không đoán được typo (trái ngược cust-062 xử lý tốt). |
| cust-058 | vague_messy | 40 | deflection | sao lại thế này | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | 'sao lại thế này' → chỉ tail, nên hỏi lại làm rõ. |
| cust-055 | vague_messy | 53 | wrong_refusal | cái này là seo | Xin lỗi, câu hỏi này không thuộc phạm vi An toàn… | 'cái này là seo' mơ hồ → nên hỏi lại; từ chối 'ngoài phạm vi' hơi vội. |
| sec-065 | aggregate_stats | 63 | hallucination | CVE nào có điểm CVSS cao nhất trong cơ… | Dựa trên kết quả tra cứu từ cơ sở dữ liệu nội bộ… | SAI/MÂU THUẪN: nói CVSS cao nhất = 8.8 dù KB đầy CVE 10.0 (Log4Shell/OOB) — bịa. |
| sec-011 | cve_lookup | 64 | hallucination | CVE-2020-1472 (Zerologon) là lỗ hổng g… | ## CVE-2020-1472 (Zerologon)  CVE-2020-1472 là m… | SAI NGHIÊM TRỌNG: Zerologon là 10.0 CRITICAL không phải 5.5 MEDIUM/AV:L; tự mâu thuẫn (5.5… |
| sec-080 | relation | 66 | no_grounding | Fortinet có những lỗ hổng nào đáng chú… | Dữ liệu nội bộ chỉ ghi nhận **CVE-2005-3400** li… | Chỉ 1 CVE Fortinet cũ (2005-3400), MÂU THUẪN sec-006 (2018-13379) → sai lệch, gây hiểu nhầ… |
| sec-082 | relation | 67 | hallucination | Những CVE nào liên quan đến CWE-787 (O… | Dưới đây là một số **CVE** liên quan đến **CWE-7… | Bảng CWE-787: cột 'năm' lệch mã CVE, ATT&CK (steganography) + CAPEC gán bừa, đồng loạt 10.… |
| cust-083 | greeting_chitchat | 68 | deflection | Cảm ơn vì đã giúp tôi lúc nãy. | Bạn cần IAS.AI hỗ trợ thêm gì nữa không? | Bỏ qua lời cảm ơn, nhảy thẳng câu tail — cụt/vô cảm. |
| sec-058 | aggregate_stats | 68 | hallucination | CVE nào cũ nhất có trong cơ sở dữ liệu… | Dựa trên kết quả tra cứu, CVE cũ nhất có trong c… | Nói CVE cũ nhất = 2007-0061, MÂU THUẪN tuyên bố phủ từ 1999 (có CVE-1999-xxxx). |

## 5. Danh sách id theo bucket (tham chiếu)

- **hallucination** (11): sec-055, sec-033, sec-058, sec-082, sec-011, sec-038, sec-029, sec-065, sec-042, sec-002, sec-075
- **deflection** (9): cust-057, cust-009, cust-058, cust-083, cust-061, sec-059, cust-048, cust-098, cust-054
- **no_grounding** (8): sec-079, sec-078, sec-007, sec-080, sec-060, sec-041, sec-043, sec-024
- **wrong_refusal** (3): cust-017, cust-100, cust-055
- **not_robust** (2): sec-081, sec-097
- **tool_leak** (1): sec-057

## 6. Đề xuất cải thiện (ưu tiên theo tác động)

> Nhãn: **[PROMPT]** sửa bằng system-prompt/skill/rule · **[CODE]** cần sửa code/pipeline · **[DATA]** cần sửa KB/retrieval

1. **[PROMPT] Diệt "canned-tail deflection".** 8 case (cust-009, cust-048, cust-098, cust-057, cust-058, cust-061, cust-054, sec-059) chỉ trả về "Bạn cần IAS.AI hỗ trợ thêm gì nữa không?" mà KHÔNG trả lời nội dung. Đây là lỗi nặng nhất, phần lớn ở câu hệ thống/meta/vague. Sửa prompt: (a) chỉ được thêm câu tail SAU khi đã trả lời thực chất; (b) nếu input mơ hồ/1 từ ("cve", "lô hông", "con vius") → PHẢI hỏi lại làm rõ, cấm dùng tail làm câu trả lời.

2. **[CODE] Chặn leak tool-call & empty-answer.** sec-057 xuất thẳng `qdrant_search(...)` ra text; sec-081 và sec-097 trả RỖNG. Cần: parser bắt chuỗi `*_search(...)` trong output → ép chạy tool hoặc fallback; và guard "empty answer" → thay bằng thông báo "không tìm thấy/không xử lý được" thay vì trả chuỗi rỗng (đặc biệt nhánh 422).

3. **[PROMPT] Sửa false-positive injection & wrong-refusal câu meta.** cust-100 ("Bạn có phải người thật?") bị chặn nhầm là injection; cust-017 ("quét port") và cust-055 bị từ chối "ngoài phạm vi" oan. Nới input-classifier + prompt: câu hỏi về bản thân bot / năng lực ATTT (kể cả quét port, dịch trong ngữ cảnh bảo mật) là TRONG phạm vi; chỉ từ chối khi rõ ràng off-topic (thời tiết/nấu ăn).

4. **[DATA] Vá độ nhất quán retrieval (cùng CVE, lúc có lúc không).** 2022-1388 (sec-007 "không thấy" vs sec-015/sec-037 trả lời được), Fortinet 2018-13379 (sec-006 có vs sec-080/sec-060 "chỉ 2005-3400"), PaperCut 27350 (sec-021 có vs sec-043 "không thấy"), Apache (sec-079 "không thấy"). Truy vấn theo tên sản phẩm/năm bị miss. Cần cải thiện recall (đúng cảnh báo CLAUDE.md: embed/rerank lệch model → tụt BM25-only).

5. **[DATA/PROMPT] Chuẩn hoá CVSS/severity để hết mâu thuẫn nội tại.** Zerologon bị gán 5.5 MEDIUM ở sec-011/sec-029/sec-038 (thực tế 10.0 CRITICAL); sec-065 nói "CVSS cao nhất = 8.8" dù KB đầy 10.0; sec-058 nói "CVE cũ nhất 2007" trái tuyên bố 1999. Nếu là dữ liệu KB thì sửa DATA; nếu bịa thì thêm rule "không suy đoán CVSS, chỉ đọc từ ledger; nếu thiếu → nói không rõ".

6. **[PROMPT] Cấm bịa ánh xạ ATT&CK/CAPEC & ví dụ sự cố.** Lặp lại nhiều: T1574.006/T1553.002 gán bừa cho Log4Shell/Drupalgeddon (sec-002, sec-033), CAPEC bịa cho CWE-787 (sec-082), kỹ thuật Initial Access không tồn tại (sec-055), ví dụ "Kaspersky 2017/EventStorm 2022" bịa (sec-075). Rule: chỉ liệt kê ATT&CK/CAPEC/ví dụ khi có trong ledger; không thì bỏ qua. Giảm "trang trí" bằng ID để trông grounded.

7. **[PROMPT] Sửa 1 lỗi remediation NGUY HIỂM.** sec-042 khuyên nâng XZ lên 5.6.1 (vẫn dính backdoor; đúng phải 5.6.2) và sec-002 hướng dẫn cờ `formatMsgNoLookups=false` (sai, phải =true). Cần rule kiểm chứng "fixed version/flag" theo ledger, và ưu tiên copy nguyên văn advisory thay vì tự diễn giải.

8. **[PROMPT] Xử lý aggregate_stats đúng khuôn.** Câu đếm/max/min (aggregate) nên theo mẫu sec-056/sec-063 (thành thật "chưa có tool đếm tổng, đây là các mã tìm được, tham khảo NVD"). Cấm trả số cụ thể kiểu "hơn 300", "8.8 cao nhất" như sự thật khi không có tool đếm.

9. **[PROMPT] Chuẩn hoá phản hồi social.** Câu cảm ơn (cust-083) → "Rất vui được giúp" trước khi tail; câu buồn/khó khăn (cust-069 làm tốt, cust-075 chưa) → đồng cảm 1 câu rồi hướng ATTT. Off-topic bare (cust-065/066/068) nên kèm 1 câu gợi ý quay lại ATTT cho đỡ cụt.

10. **[PROMPT] Bám sát câu hỏi hệ thống, tránh lạc đề.** cust-024 (báo cáo pentest) và cust-049 (dùng trên điện thoại) trả lời lạc sang khảo sát công cụ ngoài thay vì năng lực/UX của IAS.AI. Rule: câu về "hệ thống/sản phẩm này" phải trả lời VỀ IAS.AI trước, mở rộng sau.

11. **[PROMPT/CODE] Giảm lặp câu tail máy móc.** "Bạn cần IAS.AI hỗ trợ thêm gì nữa không?" xuất hiện gần như mọi câu, đôi khi 2 lần/câu (cust-021, cust-024). Chỉ nên xuất 1 lần và bỏ khi không phù hợp (sau lời tạm biệt/cảm ơn).

12. **[DATA] Sửa 'CVE cũ nhất/mới nhất' và độ phủ 1999.** sec-058 mâu thuẫn tuyên bố phủ từ 1999; nếu KB thực sự bắt đầu 2007 thì phải sửa các câu data_coverage đang nói "1999→nay" cho khớp (tính nhất quán tổng thể).