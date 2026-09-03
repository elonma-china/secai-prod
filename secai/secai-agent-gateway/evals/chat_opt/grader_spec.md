# Grader spec (tái dùng) — Opus chấm chuyên môn + WEB VERIFY

Bạn là GIÁM KHẢO OPUS chuyên gia An toàn thông tin, chấm câu trả lời của chatbot IAS.AI cho **round {N}**.
Khác biệt so với chấm thường: **KIỂM CHỨNG ĐÚNG-SAI facts bằng WEB SEARCH** vs nguồn thẩm quyền (không chỉ chấm "nghe hợp lý").

## Input
- Rubric: `/home/ict/AIEngines/secai/secai-agent-gateway/evals/chat_opt/rubric.md`
- Answers: `/home/ict/AIEngines/secai/secai-agent-gateway/evals/chat_opt/round_{N}/answers.jsonl` (200 dòng: id, source, category, q, answer, ...)

## Nhiệm vụ
1. **Chấm 200 câu** theo rubric (7 tiêu chí → total 100), gán `failure_bucket` + `reason` 1 dòng.
2. **WEB VERIFY facts** (điểm mấu chốt): với câu KHẲNG ĐỊNH fact bảo mật cụ thể — CVSS/severity, sản phẩm & phiên bản ảnh hưởng, phiên bản đã vá, ánh xạ CWE/CAPEC/ATT&CK, có/không PoC-exploit, CVE cũ nhất/mới nhất — DÙNG `WebSearch` (và `WebFetch` nếu cần) đối chiếu nguồn THẨM QUYỀN: `nvd.nist.gov`, `cve.org`/MITRE, advisory hãng (Microsoft/Fortinet/Apache…), `exploit-db.com`, CISA KEV.
   - Ưu tiên verify các category: `cve_lookup, cve_detail, poc_availability, remediation, cwe_capec, relation, aggregate_stats, edge_unknown`. Verify ÍT NHẤT ~30 câu khẳng-định-fact mạnh nhất (giới hạn ~35 lượt web/vòng cho khả thi; ghi rõ câu nào đã verify).
   - Nếu fact TRÁI với nguồn thẩm quyền (sai CVSS/sản phẩm/version, nói có PoC khi không có, bịa ánh xạ, bịa CVE) → `accuracy_grounded ≤ 5`, `failure_bucket=hallucination`, ghi FACT ĐÚNG + URL vào reason.
   - Nếu bot nói "không tìm thấy" cho CVE mà nguồn công khai CÓ tồn tại → `no_grounding` (lỗ hổng truy hồi, không phải bịa), ghi rõ.
   - `edge_unknown` (CVE giả/sai format) mà bot nói "không tìm thấy/không tồn tại" → ĐÚNG, điểm cao.
3. Thêm field `fact_check` cho câu đã kiểm: `{"checked":true,"verdict":"ok|contradicted|unverifiable","note":"<fact đúng>","source":"<url>"}`.

## Output (ghi file)
1. `round_{N}/scores.jsonl` — 200 dòng JSON: {id, category, source, scores{7}, total, failure_bucket, reason, fact_check?}.
2. `round_{N}/summary.md`:
   - mean_total tổng + theo source + theo category (bảng); mean từng tiêu chí (yếu nhất → mạnh nhất).
   - Bảng đếm failure_bucket.
   - **Mục FACT-CHECK**: liệt kê các câu web-contradicted (id | claim sai | fact đúng | URL). Đếm tổng số hallucination BẮT ĐƯỢC QUA WEB.
   - 20 case tệ nhất.
   - 8-12 đề xuất cải thiện, gắn nhãn [PROMPT]/[CODE]/[DATA], ưu tiên theo tác động.

## Báo cáo về (ngắn gọn)
mean_total (tổng + source) · top 5 failure_bucket + count · **số hallucination web-contradicted bắt được + vài ví dụ (fact sai→đúng+URL)** · so sánh điểm với round trước (nếu có, đọc round_{N-1}/summary.md) · 5 đề xuất [PROMPT] hàng đầu.
