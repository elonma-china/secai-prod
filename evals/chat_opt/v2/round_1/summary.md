# Round 1 — Opus web-verify grading summary (50 questions)

Grader: Opus (web-verify). Rubric: 7 tiêu chí → 100/câu. Nguồn kiểm chứng: NVD, CISA KEV/advisory, MITRE, vendor. Ngày chấm: 2026-09-04.

## 1. Điểm tổng

| Phạm vi | n | mean_total |
|---|---|---|
| **Toàn bộ** | 50 | **80.92** |
| customer | 18 | **74.22** |
| secpro | 32 | **84.69** |

> Đảo chiều so với vòng chatbot trước (secpro từng là nút thắt vì DATA sai): lần này **customer THẤP hơn secpro ~10 điểm**. Nút thắt chuyển sang **câu hệ thống/meta/usage/vague** — bot né hoặc trả rỗng những câu KHÔNG cần KB mà chỉ cần trả lời trực tiếp.

## 2. Mean theo tiêu chí (yếu → mạnh, % tối đa)

| Tiêu chí | mean | % max |
|---|---|---|
| **accuracy_grounded** | 15.48 / 20 | **77.4%** ← yếu nhất |
| answer_attitude | 19.76 / 25 | 79.0% |
| tool_grounding | 16.04 / 20 | 80.2% |
| idk_handling | 8.10 / 10 | 81.0% |
| robustness | 8.36 / 10 | 83.6% |
| scope_discipline | 8.66 / 10 | 86.6% |
| language_format | 4.52 / 5 | 90.4% |

`accuracy_grounded` kéo xuống bởi (a) các câu trả **rỗng/boilerplate** (q010/q022/q025/q038 → điểm ~4), (b) 2 hallucination web-bắt (q024/q036), (c) aggregate sai lệch (q039), (d) retrieval-gap (q029). Ngôn ngữ/format nhìn chung tốt trừ 2 case rò khối `<untrusted_retrieved_content>`.

## 3. Mean theo category (thấp → cao)

| Category | n | mean |
|---|---|---|
| meta_self | 2 | 28.5 |
| vague_messy | 2 | 43.0 |
| poc_availability | 4 | 47.8 |
| aggregate_stats | 3 | 55.7 |
| usage | 2 | 61.5 |
| data_coverage | 3 | 68.3 |
| remediation | 4 | 80.5 |
| relation | 3 | 83.3 |
| greeting_chitchat | 2 | 89.5 |
| random_offtopic | 2 | 93.5 |
| cve_lookup | 4 | 98.0 |
| edge_unknown | 2 | 98.0 |
| cve_detail | 5 | 98.6 |
| capability | 2 | 99.5 |
| cwe_capec | 4 | 99.8 |
| system_overview | 3 | 100.0 |
| concept | 3 | 100.0 |

**Đọc nhanh:** tra cứu chuyên môn khi có dữ liệu (cve_lookup/cve_detail/cwe_capec/concept) đạt ~98-100. Sụt điểm tập trung ở **meta_self, vague_messy, poc_availability, aggregate_stats, usage, data_coverage** — hoặc bị từ chối oan, hoặc bot trả rỗng, hoặc retrieval trả về trống.

## 4. Bảng đếm failure_bucket

| bucket | count | id |
|---|---|---|
| none | 34 | (các câu đạt) |
| no_grounding | 4 | q022, q025, q029, q039 |
| deflection | 3 | q010, q012, q038 |
| bad_idk | 2 | q006, q008 |
| hallucination | 2 | q024, q036 |
| not_robust | 2 | q045, q046 |
| wrong_refusal | 1 | q011 |
| lang_format | 1 | q023 |
| other | 1 | q034 |

## 5. FACT-CHECK (web-verified)

**Tổng hallucination bắt qua web (fact TRÁI nguồn thẩm quyền): 2** — q024 (nhãn ATT&CK), q036 (mô tả CVE sai). Ngoài ra 3 case `contradicted` là **retrieval-gap / trả rỗng** (bot đáng lẽ có dữ liệu công khai) chứ không phải bịa.

| id | claim của bot | Sự thật (nguồn) | Loại |
|---|---|---|---|
| q036 | CVE-2006-1547 = "Commons Beanutils RCE" (CAPEC-500) | Là **Denial of Service** trong ActionForm (Struts <1.2.9), KHÔNG phải RCE Beanutils. https://github.com/advisories/GHSA-7qwv-cwgj-c8rj | hallucination |
| q024 | ATT&CK T1556="Use of Web Shell", T1542.002="Boot/Logon Autostart" | T1556=**Modify Authentication Process**; T1542.002=**Component Firmware**. (CVSS9.8/CWE-288/KEV thì ĐÚNG). https://www.cisa.gov/known-exploited-vulnerabilities-catalog | hallucination |
| q022 | (bỏ trống) — không nêu PoC Log4Shell | PoC công khai **CÓ** rất nhiều (log4j-shell-poc…). https://github.com/kozmer/log4j-shell-poc | no_grounding |
| q025 | (bỏ trống) — không nêu script Zerologon | Python PoC **CÓ**: zerologon_tester.py (Secura), Impacket. https://github.com/SecuraBV/CVE-2020-1472 | no_grounding |
| q029 | "Không tìm thấy mitigation PAN-OS trong dữ liệu nội bộ" | Mitigation **CÓ**: tắt Device Telemetry + Threat ID 95187. https://security.paloaltonetworks.com/CVE-2024-3400 | no_grounding |
| q039 | "CVE cao nhất chỉ 7.5" | KB rõ ràng có nhiều CVE **10.0** (Log4Shell q013, danh sách q034) — kết luận sai. | no_grounding |
| q017 | vector `...A:N` với điểm 9.8 | 9.8 (NVD) dùng **A:H**; A:N là điểm 9.1 của MS — số đúng, vector lệch. https://nvd.nist.gov/vuln/detail/CVE-2021-26855 | minor |

**Câu fact ĐÚNG đã xác minh (verdict ok):** q013 (Log4Shell 10.0/CWE-502/917), q014 (Zerologon 10.0 — gốc crypto), q015 (SMBv1 8.8), q016 (MOVEit SQLi 9.8), q018 (Citrix Bleed CWE-119), q019 (PAN-OS 10.0), q020 (FortiOS/FortiProxy), q021 (Heartbleed 7.5), q023 (module Metasploit tồn tại), q027 (MS17-010), q034/q035 (CVE-2018-14721 SSRF thật, CVE-2018-13382 v3 7.5), q043/q044 (CVE giả → đúng khi báo "không tìm thấy").

## 6. 15 case tệ nhất

| id | total | bucket | q (rút gọn) | vấn đề |
|---|---|---|---|---|
| q010 | 23 | deflection | "cho vài câu hỏi mẫu" | Chỉ trả boilerplate, KHÔNG có câu mẫu nào |
| q038 | 23 | deflection | "CVE mới/cũ nhất trong DB" | Trả chuỗi RỖNG hoàn toàn (28.8s) |
| q022 | 26 | no_grounding | "Log4Shell có PoC GitHub?" | Bỏ trống; thực tế có hàng trăm PoC |
| q025 | 26 | no_grounding | "Zerologon có script Python?" | Bỏ trống (51s); thực tế có zerologon_tester.py |
| q012 | 28 | deflection | "Dữ liệu tôi nhập có bị lưu?" | Lờ câu privacy, chỉ trả greeting |
| q011 | 29 | wrong_refusal | "Bạn chạy model AI nào?" | Từ chối oan "ngoài phạm vi ATTT" |
| q045 | 43 | not_robust | "cve" | Không hỏi làm rõ, chỉ boilerplate |
| q046 | 43 | not_robust | "lỗ hổng" | Không hỏi làm rõ (32.9s) |
| q029 | 54 | no_grounding | "Mitigation PAN-OS trước vá?" | "Không tìm thấy" dù mitigation công khai tồn tại |
| q006 | 55 | bad_idk | "CVE cập nhật đến khi nào?" | Né "không có thông tin" thay vì "cập nhật hàng đêm" |
| q008 | 55 | bad_idk | "CVE từ năm nào tới năm nào?" | Né thay vì nêu ~1999→nay |
| q039 | 58 | no_grounding | "CVE CVSS cao nhất?" | Kết luận sai "cao nhất 7.5" (KB có 10.0) |
| q023 | 63 | lang_format | "Struts2 có module Metasploit?" | Rò nguyên khối `<untrusted_retrieved_content>` thô |
| q024 | 76 | hallucination | "TeamCity có PoC?" | Không xác nhận PoC + bịa nhãn ATT&CK |
| q034 | 79 | other | "CVE liên quan CWE-918?" | Lẫn loạt CVE-2026-* 10.0 khả nghi (KB data) |

## 7. Đề xuất cải thiện (ưu tiên theo tác động)

1. **[PROMPT] Cho phép & định khung nhóm câu hệ thống/meta/usage.** Thêm vào system prompt các "known facts" trả lời trực tiếp không cần tool: KB cập nhật **hàng đêm**, dữ liệu CVE **~1999→nay**, nguồn NVD/MITRE/CAPEC/CWE/KEV, chính sách lưu dữ liệu người dùng. Sửa ngay q006/q008/q011/q012 (+~45đ/câu). Đòn bẩy điểm lớn nhất cho phía customer.

2. **[PROMPT] Cấm trả lời RỖNG / chỉ-boilerplate.** Bắt buộc: nếu không có nội dung thực chất thì phải (a) hỏi làm rõ, hoặc (b) nêu rõ "chưa truy vấn được". Sửa q010/q022/q025/q038/q045/q046 — 6 câu đang ~23-43đ.

3. **[RETRIEVAL] Vá lỗ hổng truy hồi PoC/mitigation cho CVE nổi tiếng.** q022 (Log4Shell PoC), q025 (Zerologon script), q029 (PAN-OS mitigation) đều có dữ liệu công khai nhưng poc_search/qdrant trả trống. Kiểm tra index PoC + fallback sang mô tả KB; cân nhắc timeout (q025 51s, q038 28.8s, q046 32.9s cho thấy retrieval treo → trả rỗng).

4. **[PROMPT] Sửa routing câu vague_messy.** "cve"/"lỗ hổng" → PHẢI hỏi làm rõ ("Bạn muốn tra CVE/lỗ hổng nào cụ thể?") thay vì boilerplate. Rẻ, nâng q045/q046.

5. **[PROMPT/CODE] Chặn rò `<untrusted_retrieved_content>` ra người dùng.** q023 và q026 in nguyên khối wrapper nội bộ. Thêm bước lọc/strip wrapper + bắt buộc diễn giải tiếng Việt trước khi trả. q023 đang mất ~35đ chỉ vì format.

6. **[PROMPT] Cấm bịa ánh xạ ATT&CK/CAPEC khi không chắc.** q024 gán sai tên T1556/T1542.002. Yêu cầu chỉ liệt kê mapping khi có trong KB; nếu suy đoán → bỏ hoặc ghi "chưa xác định". Ngăn hallucination "nghe hợp lý".

7. **[DATA] Rà soát chất lượng dữ liệu CWE-918 / synthetic CVE.** q034 trả về loạt CVE-2026-xxxxx đều CVSS 10.0 SSRF — dấu hiệu KB chứa CVE tổng hợp/test. Kiểm tra pipeline ingest, gắn nhãn nguồn, hoặc lọc bỏ CVE không xác thực khỏi kết quả người dùng.

8. **[RAG] Cải thiện aggregate_stats.** q039 kết luận sai vì chỉ đọc 1 batch retrieval. Với câu "cao nhất/nhiều nhất/đếm" → hoặc thêm truy vấn aggregate thực (đếm/sort theo cvss), hoặc trả lời thận trọng "chưa có tool thống kê, đây chỉ là mẫu" (như q037 đã làm đúng — nên chuẩn hóa theo q037).

9. **[PROMPT] Hoàn thiện câu remediation nổi tiếng.** q028 Citrix Bleed thiếu điểm mấu chốt "**kill active ICA/PCoIP sessions**"; nên nhúng checklist remediation chuẩn cho các CVE KEV lớn (patch + hành động bổ sung).

10. **[PROMPT] Thêm gợi ý quay về ATTT khi từ chối off-topic.** q047 hơi cụt. Chuẩn hóa mẫu từ chối = 1 câu lịch sự + 1 gợi ý ATTT (q048 đã đúng).

11. **[PROMPT] Nhất quán vector/điểm CVSS.** q017 in điểm 9.8 nhưng vector A:N (của 9.1). Khi nêu cả điểm và vector, đảm bảo khớp; nếu có 2 nguồn (NVD vs vendor) thì nói rõ.

12. **[CODE] Chuẩn hóa root-cause mô tả.** q014 Zerologon mô tả "thiếu xác thực" bỏ gốc mật mã (IV=0). Với CVE có cơ chế đặc thù, KB nên giữ trường "root_cause" để bot nêu chính xác.

---
### Ghi chú divergence hợp lệ (không trừ điểm)
ProxyLogon NVD 9.8 vs MS 9.1; Citrix Bleed NVD 7.5 vs Citrix 9.4; Fortinet CVE-2018-13379 NVD 9.8 vs 9.1; Heartbleed v3 7.5 vs v2 5.0; EternalBlue NVD 8.8 vs MS 8.1 — bot nêu MỘT giá trị hợp lệ đều được chấp nhận. Zerologon MS-submitted 5.5 là lỗi nhập liệu; giá trị đúng 10.0 (bot dùng 10.0 — đúng).
