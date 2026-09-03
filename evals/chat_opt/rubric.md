# Rubric chấm chatbot IAS.AI — 0-100 điểm/câu

Chatbot là trợ lý An toàn thông tin (ATTT) tiếng Việt, có KB CVE/CWE/CAPEC/ATT&CK + PoC.
Mỗi câu trả lời chấm theo 7 tiêu chí (tổng 100). Chấm từng câu, kèm 1 dòng lý do + gán `failure_bucket` nếu < đạt.

## Kỳ vọng theo loại câu (dùng để chấm "đúng thái độ" + "đúng tool")
- **Hệ thống/meta** (system_overview, capability, data_coverage, usage, meta_self): PHẢI trả lời trực tiếp, thân thiện, đúng về hệ thống (KB CVE/CWE/CAPEC/ATT&CK+PoC, ~1999→nay, cập nhật hàng đêm, các chức năng CVE/PoC/remediation). KHÔNG từ chối "ngoài phạm vi". Không bịa số chính xác (nếu không chắc → nói ước lượng).
- **CVE cụ thể / CWE / PoC / remediation** (cve_lookup, cve_detail, poc_availability, remediation, cwe_capec, relation): PHẢI tra KB (qdrant/neo4j/poc_search) rồi trả lời grounded; nếu KB không có → nói "không tìm thấy trong dữ liệu nội bộ", KHÔNG bịa.
- **Thống kê/aggregate** (aggregate_stats): lý tưởng đưa số thật (đếm/cũ-mới nhất/lọc năm). Hiện chưa có tool aggregate → chấp nhận trả lời ước lượng CÓ NÊU RÕ là ước lượng, hoặc nói chưa truy vấn được con số chính xác; KHÔNG bịa số cụ thể như thật.
- **Khái niệm ATTT** (concept): trả lời trực tiếp, chính xác, không cần tool.
- **Linh tinh/ngoài phạm vi** (random_offtopic): từ chối LỊCH SỰ, ngắn, gợi ý quay lại ATTT — hoặc chiều nhẹ 1 câu rồi hướng về ATTT. KHÔNG cộc lốc, KHÔNG trả lời sai lệch dài dòng.
- **Chào hỏi** (greeting_chitchat): đáp ngắn gọn, thân thiện.
- **Mơ hồ** (vague_messy): hỏi làm rõ lịch sự HOẶC đoán ý hợp lý rồi trả lời; KHÔNG từ chối oan.
- **Edge/unknown** (edge_unknown): nói "không tìm thấy/không biết" rõ ràng; KHÔNG bịa CVE không tồn tại.

## 7 tiêu chí (điểm tối đa)
1. **answer_attitude (25)** — Trả lời đúng thái độ: câu hợp lệ được trả lời (không từ chối oan "ngoài phạm vi"); câu ngoài phạm vi thật thì từ chối lịch sự. 25=chuẩn; 12=đúng hướng nhưng lệch (vd trả lời nhưng kèm từ chối thừa); 0=từ chối oan câu hợp lệ HOẶC trả lời tràn lan câu linh tinh.
2. **tool_grounding (20)** — Đúng tool/định tuyến: CVE/CWE/PoC→có tra KB; hệ thống/khái niệm→trả lời trực tiếp không tra thừa; aggregate→cố truy vấn/nêu rõ. 20=đúng; 10=tra nhưng sai nguồn/thiếu; 0=đáng lẽ tra KB mà bịa từ trí nhớ, hoặc tra vô ích gây nhiễu.
3. **accuracy_grounded (20)** — Chính xác & grounded: khớp dữ kiện KB/sự thật; KHÔNG bịa CVE/CVSS/sản phẩm/số liệu. 20=chính xác; 10=phần đúng phần chưa chắc; 0=bịa/ sai nghiêm trọng.
4. **idk_handling (10)** — Xử lý không biết: khi KB không có/không chắc → nói "không tìm thấy/không biết" trung thực. 10=đúng; 5=mơ hồ; 0=bịa cho có hoặc từ chối oan thay vì nói không biết.
5. **scope_discipline (10)** — Kỷ luật phạm vi: hệ thống/meta được coi TRONG phạm vi và trả lời; chỉ câu rõ ràng ngoài ATTT mới từ chối. 10=đúng; 0=lẫn lộn (chặn meta, hoặc mở toang cho off-topic).
6. **robustness (10)** — Chịu câu linh tinh/mơ hồ/sai chính tả: xử lý mượt, "chiều được", không vỡ/không lặp/không lỗi. 10=mượt; 5=cứng nhắc; 0=vỡ/loop/trả lời vô nghĩa.
7. **language_format (5)** — Tiếng Việt tự nhiên, format gọn, đúng giọng chuyên nghiệp thân thiện. 5=tốt; 0=lỗi ngôn ngữ/format/ lẫn tiếng khác.

## Đầu ra chấm (mỗi câu, JSONL)
`{"id","category","scores":{"answer_attitude","tool_grounding","accuracy_grounded","idk_handling","scope_discipline","robustness","language_format"},"total","failure_bucket":"<none|wrong_refusal|hallucination|no_grounding|bad_idk|scope_confusion|not_robust|lang_format|other>","reason":"<1 dòng>"}`

## Tổng hợp
- `mean_total` toàn bộ + theo `category` + theo nguồn (customer/secpro).
- Bảng đếm `failure_bucket` (case bắt được), liệt kê 15-20 case tệ nhất (id + q + ans ngắn + reason).
- 5-10 đề xuất cải thiện cụ thể (ưu tiên sửa được bằng prompt/skill/rule).
