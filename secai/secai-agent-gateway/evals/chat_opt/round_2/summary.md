# Round 2 — Kết quả chấm chatbot IAS.AI (giám khảo Opus + WEB VERIFY)

- Tổng số câu: **200** | **mean_total = 89.36/100**
- Theo nguồn: **customer = 90.72** (n=100) | **secpro = 88.01** (n=100)
- Full-quality (total ≥ 90): **150** | hỏng (total < 50): **7**
- **So với Round 1 (mean 87.98): Δ = +1.38** (customer 88.17→90.72 = **+2.55**; secpro 87.79→88.01 = **+0.22**)
- Web verify: **25 câu khẳng-định-fact** đối chiếu nguồn thẩm quyền (20 lượt WebSearch/WebFetch) — **12 mâu thuẫn (contradicted)**, 13 khớp (ok).

> Cải thiện chủ yếu ở phía **customer** (hết loạt "canned-tail deflection", vague/typo được hỏi làm rõ). Phía **secpro** gần như đứng yên: các lỗi **accuracy_grounded** (Zerologon CVSS, XZ fix version, ánh xạ ATT&CK/CAPEC bịa, quan hệ sản phẩm–CVE) vẫn tồn tại — đây là điểm yếu số 1 còn lại.

---

## 1. Điểm trung bình theo category

| Category | n | R2 mean | R1 mean | Δ |
|---|---:|---:|---:|---:|
| aggregate_stats | 11 | 72.82 | 69.09 | +3.73 |
| relation | 11 | 78.55 | 76.45 | +2.10 |
| edge_unknown | 12 | 83.67 | 85.67 | −2.00 |
| meta_self | 12 | 86.42 | 82.33 | +4.09 |
| vague_messy | 13 | 88.15 | 73.62 | **+14.53** |
| random_offtopic | 12 | 89.00 | 91.00 | −2.00 |
| poc_availability | 11 | 89.36 | 90.18 | −0.82 |
| remediation | 11 | 90.00 | 90.36 | −0.36 |
| greeting_chitchat | 13 | 90.23 | 91.31 | −1.08 |
| capability | 13 | 91.00 | 88.92 | +2.08 |
| system_overview | 13 | 91.62 | 93.15 | −1.53 |
| cve_lookup | 11 | 92.73 | 91.27 | +1.46 |
| cwe_capec | 11 | 93.36 | 96.00 | −2.64 |
| data_coverage | 12 | 94.75 | 95.67 | −0.92 |
| usage | 12 | 94.75 | 89.83 | +4.92 |
| concept | 11 | 95.82 | 96.27 | −0.45 |
| cve_detail | 11 | 96.18 | 95.00 | +1.18 |

Yếu nhất vẫn là **aggregate_stats** (không có tool đếm → bịa số/mâu thuẫn nội tại) và **relation** (quan hệ sản phẩm/CWE→CVE gán sai năm, sai chuỗi, CVSS đồng loạt 10.0).

## 2. Điểm trung bình theo từng tiêu chí (yếu nhất trên cùng)

| Tiêu chí | mean | max | % đạt | R1 % |
|---|---:|---:|---:|---:|
| accuracy_grounded | 16.48 | 20 | 82.4% | 82.2% |
| tool_grounding | 17.70 | 20 | 88.5% | 88.8% |
| answer_attitude | 22.25 | 25 | 89.0% | 86.4% |
| idk_handling | 9.05 | 10 | 90.5% | 90.4% |
| robustness | 9.32 | 10 | 93.2% | 87.6% |
| scope_discipline | 9.63 | 10 | 96.3% | 94.9% |
| language_format | 4.93 | 5 | 98.6% | 97.4% |

**answer_attitude** (+2.6đ%) và **robustness** (+5.6đ%) tăng rõ nhờ diệt deflection & xử tốt vague/typo. **accuracy_grounded gần như đứng yên (82.4%)** — nút thắt cốt lõi, tập trung ở secpro (CVE/CVSS/ánh xạ/quan hệ).

## 3. Bảng đếm failure_bucket

| failure_bucket | count | R1 |
|---|---:|---:|
| **(none / đạt)** | 170 | 166 |
| hallucination | 11 | 11 |
| other | 9 | — |
| wrong_refusal | 4 | 3 |
| tool_leak | 2 | 1 |
| no_grounding | 2 | 8 |
| deflection | 1 | 9 |
| not_robust | 1 | 2 |

deflection 9→1 và no_grounding 8→2 giảm mạnh; nhưng **hallucination vẫn 11** (không giảm) và tool_leak tăng 1→2.

## 4. FACT-CHECK (WEB VERIFY) — 12 câu web-contradicted

Đếm: **12 hallucination/sai-fact BẮT ĐƯỢC QUA WEB** (trên 25 câu đã verify). Bảng: id | claim SAI | fact ĐÚNG | URL.

| id | claim SAI của bot | fact ĐÚNG (nguồn thẩm quyền) | URL |
|---|---|---|---|
| **sec-011** | Zerologon CVE-2020-1472 = **CVSS 5.5 MEDIUM, AV:L** | **CVSS 10.0 CRITICAL** (unauth → domain admin) | crowdstrike.com/.../cve-2020-1472-zerologon-security-advisory |
| **sec-038** | (remediation) lặp lại Zerologon **"CVSS 5.5 MEDIUM"**; vá "T10/2020" | 10.0 CRITICAL; bản vá **Aug 2020** | crowdstrike.com/.../cve-2020-1472-zerologon-security-advisory |
| **sec-042** | Vá XZ CVE-2024-3094 → **"nâng lên 5.6.1"** | 5.6.1 **VẪN dính backdoor**; phải **5.6.2** (hoặc <5.6.0) | en.wikipedia.org/wiki/XZ_Utils_backdoor |
| **sec-043** | Vá PaperCut CVE-2023-27350 → **"22.0.6"** | Bản vá là **20.1.7 / 21.2.11 / 22.0.9** | rapid7.com/blog/post/2023/05/17/etr-cve-2023-27350 |
| **sec-002** | Log4Shell ánh xạ **T1574.006, T1553.002**; cờ **formatMsgNoLookups=false** | ATT&CK thật ~**T1190/T1203/T1059**; cờ đúng **=true** | research.splunk.com/endpoint/9be30d80-...-log4shell |
| **sec-055** | ATT&CK Initial Access gồm **"Inbound Blasting","Initial Recon","Component"** | TA0001 thật: Drive-by, **T1190, T1133, T1200, T1566, T1091, T1195, T1199, T1078** | attack.mitre.org/tactics/TA0001/ |
| **sec-082** | CWE-787: cột năm lệch mã, đồng loạt 10.0; **CAPEC-92 = "Out-of-bounds Write"** | **CAPEC-92 = Forced Integer Overflow**; ATT&CK steganography gán bừa | capec.mitre.org/data/definitions/92.html |
| **sec-083** | **CVE-2021-34473/34523 = chuỗi "ProxyLogon"** | 34473/34523/31207 là **ProxyShell**; ProxyLogon = 26855/26857/26858/27065 | threatprotect.qualys.com/2021/08/10/proxyshell |
| **sec-065** | "CVSS cao nhất trong KB = **CVE-2025-36891 = 8.8**" | KB đầy CVE **10.0** (Log4Shell...) → "max 8.8" vô lý | nvd.nist.gov/vuln/detail/CVE-2021-44228 |
| **sec-006** | CVE-2018-13379 CVSS **9.1** | NVD **9.8**, CWE-22 (sản phẩm/KEV thì đúng) | nvd.nist.gov/vuln/detail/CVE-2018-13379 |
| **sec-009** | ProxyLogon CVE-2021-26855 = **"RCE", CVSS 9.1** | NVD: **SSRF, CVSS 9.8** (RCE chỉ khi chain 27065) | github.com/r0xdeadbeef/CVE-2021-26855 |
| **sec-024** | "CVE-2017-5638 **không có** module Metasploit" | Module **struts2_content_type_ognl (S2-045)** có thật → miss truy hồi | rapid7.com/db/modules/.../struts2_content_type_ognl |

**Fact đúng đã xác nhận (ok, 13 câu):** sec-001 (TeamCity 9.8/CWE-288/KEV), sec-003 & sec-014 (CVE-2021-41773 **thật là 9.8 CRITICAL** — bot ĐÚNG), sec-005 (BlueKeep 9.8/CWE-416), sec-016 (Citrix Bleed CWE-119), sec-018 (CurveBall 8.1), sec-020 (PrintNightmare 8.8 HIGH), sec-022 (XZ vá **5.6.2** — ĐÚNG, đối lập sec-042), sec-031 (CVE-2023-3519 RCE 9.8), sec-032 (Jenkins CVE-2024-23897 9.8/KEV), sec-034 (Log4Shell cờ =true — ĐÚNG), sec-039 (Citrix Bleed 9.4), sec-048 (CAPEC-66=SQLi), sec-051 (CAPEC-664=SSRF).

> Mâu thuẫn NỘI BỘ (không cần web, vẫn là lỗi grounding): **sec-060** ("Fortinet chỉ 1 CVE") vs sec-006/sec-080; **sec-058** ("cũ nhất 2007") vs tuyên bố phủ 1999; **sec-022 vs sec-042** (XZ 5.6.2 đúng vs 5.6.1 sai); **sec-034 vs sec-002** (cờ Log4j =true đúng vs =false sai). Cùng sự thật lúc đúng lúc sai → thiếu "nguồn chân lý" thống nhất.

## 5. 20 case TỆ NHẤT

| id | cat | total | bucket | reason (ngắn) |
|---|---|---:|---|---|
| sec-097 | edge_unknown | 12 | not_robust | CVE 9 chữ số → TRẢ RỖNG + http 422, không thông báo (vỡ) |
| sec-057 | aggregate_stats | 22 | tool_leak | Lộ `qdrant_search(query=...)` ra text |
| sec-092 | edge_unknown | 22 | tool_leak | Lộ `qdrant_search(query="CVE-2021-999999")` ra text |
| cust-100 | meta_self | 23 | wrong_refusal | "Bạn có phải người thật?" bị classifier chặn nhầm là injection (FP) |
| cust-005 | system_overview | 28 | wrong_refusal | "Công cụ quét hay chatbot?" bị từ chối "ngoài phạm vi" |
| cust-055 | vague_messy | 46 | wrong_refusal | "cái này là seo" → từ chối vội thay vì hỏi lại |
| cust-023 | capability | 47 | wrong_refusal | "Kiểm tra domain có lỗ hổng" → từ chối "ngoài phạm vi" |
| sec-082 | relation | 62 | hallucination | CWE-787: năm lệch mã, 10.0 đồng loạt, CAPEC/ATT&CK bịa |
| sec-065 | aggregate_stats | 63 | hallucination | "CVSS cao nhất = 8.8" mâu thuẫn KB đầy 10.0 |
| sec-060 | aggregate_stats | 64 | no_grounding | "Fortinet chỉ 1 CVE" mâu thuẫn sec-006/sec-080 |
| sec-055 | cwe_capec | 65 | hallucination | ATT&CK Initial Access: bịa tên kỹ thuật |
| sec-058 | aggregate_stats | 65 | hallucination | "CVE cũ nhất 2007-0061" mâu thuẫn phủ-1999 |
| cust-083 | greeting_chitchat | 66 | deflection | "Cảm ơn lúc nãy" → chỉ tail, bỏ qua lời cảm ơn |
| sec-011 | cve_lookup | 66 | hallucination | Zerologon "5.5 MEDIUM" (thực 10.0 CRITICAL) |
| sec-024 | poc_availability | 68 | no_grounding | "CVE-2017-5638 không có module MSF" (thực có) |
| sec-078 | relation | 73 | hallucination | CWE-89 CVEs: 10.0 đồng loạt + nhiều CVE tương lai |
| sec-064 | aggregate_stats | 73 | other | ">300 CVE có PoC" nêu số như sự thật, không có tool đếm |
| sec-083 | relation | 76 | hallucination | Gán nhầm 34473/34523 là "ProxyLogon" (thực ProxyShell) |
| sec-088 | relation | 76 | other | Jenkins: lẫn CVE tương lai bịa + severity quá tay |
| sec-042 | remediation | 76 | hallucination | Vá XZ "5.6.1" (nguy hiểm, phải 5.6.2) |

## 6. Đề xuất cải thiện (ưu tiên theo tác động)

> Nhãn: **[PROMPT]** sửa system-prompt/skill/rule · **[CODE]** sửa code/pipeline · **[DATA]** sửa KB/retrieval

1. **[DATA/PROMPT] Chuẩn hoá "nguồn chân lý" cho CVSS/severity — hết mâu thuẫn nội tại (tác động cao nhất, secpro).** Zerologon bị gán 5.5 MEDIUM (sec-011/sec-038; thực 10.0), CVE-2018-13379 9.1 (thực 9.8), ProxyLogon 9.1 (thực 9.8). Rule: **chỉ đọc CVSS/severity từ ledger, cấm suy đoán; nếu thiếu → "không rõ CVSS"**. Nếu KB sai thì backfill từ NVD.

2. **[PROMPT] Cấm bịa ánh xạ ATT&CK/CAPEC & danh sách kỹ thuật.** sec-055 (Initial Access bịa tên kỹ thuật), sec-002/sec-082 (ATT&CK/CAPEC gán bừa, CAPEC-92 sai nghĩa). Rule: **chỉ liệt kê T-id/CAPEC-id khi có trong ledger; không "trang trí" bằng ID để trông grounded**; câu "liệt kê kỹ thuật của tactic X" phải trả từ danh mục ATT&CK thật hoặc nói không chắc.

3. **[PROMPT] Rule kiểm chứng "fixed version/cờ vá" — chống khuyến nghị NGUY HIỂM.** sec-042 (XZ "5.6.1" — phải 5.6.2), sec-043 (PaperCut "22.0.6" — phải 20.1.7/21.2.11/22.0.9), sec-002 (Log4j cờ =false — phải =true). Rule: **copy nguyên văn "fixed version/flag" từ ledger/advisory, không tự diễn giải**; lấy sec-022/sec-034 (đang đúng) làm khuôn.

4. **[PROMPT] Chuẩn khuôn relation (sản phẩm→CVE, CWE→CVE).** sec-080 (nhãn NĂM lệch mã CVE), sec-081 (gán "RCE" cho CVE thực chất EoP/bypass), sec-083 (nhầm ProxyShell↔ProxyLogon), sec-078/sec-082 (CVSS 10.0 đồng loạt). Rule: **mỗi CVE kèm năm/loại/CVSS ĐỌC từ ledger; không tự suy loại lỗ hổng/chuỗi tấn công; cấm gán CVSS đồng nhất**.

5. **[PROMPT] Sửa false-positive injection & wrong-refusal câu hợp lệ.** cust-100 ("Bạn có phải người thật?" bị chặn injection), cust-005 ("công cụ quét hay chatbot?"), cust-023 ("kiểm tra domain có lỗ hổng"), cust-055 ("cái này là seo"). Nới classifier + prompt: **câu về bản thân bot/năng lực ATTT là TRONG phạm vi**; câu scan chủ động → "không có scanner chủ động" thay vì "ngoài phạm vi".

6. **[CODE] Chặn leak tool-call & empty-answer (tái phát R1).** sec-057 & sec-092 xuất thẳng `qdrant_search(...)`; sec-097 trả RỖNG + 422. Cần: **parser bắt chuỗi `*_search(...)` → ép chạy tool hoặc fallback**; guard "empty/422" → "không xử lý được" thay vì chuỗi rỗng.

7. **[PROMPT] Xử lý aggregate_stats theo khuôn "thành thật".** sec-058 ("cũ nhất 2007" mâu thuẫn 1999), sec-064 (">300 CVE PoC"), sec-065 ("max CVSS 8.8"), sec-060 ("Fortinet 1 CVE"). Câu đếm/max/min/cũ-mới nhất → **theo mẫu sec-059/sec-063 ("chưa có tool đếm chính xác + gợi ý NVD"), cấm trả số/cực trị như sự thật**.

8. **[DATA] Vá độ nhất quán retrieval theo tên sản phẩm/năm.** sec-024 ("2017-5638 không có MSF" — thực có), sec-060 (Fortinet miss), sec-088/sec-078 (lẫn CVE tương lai 2026-xxxx). Cải thiện recall + lọc CVE hợp lệ (đúng cảnh báo CLAUDE.md: embed/rerank lệch model → tụt BM25-only).

9. **[PROMPT] Dứt điểm deflection social còn sót & làm ấm off-topic.** cust-083 ("cảm ơn lúc nãy" → chỉ tail). Rule: câu cảm ơn → "rất vui được giúp" trước tail; off-topic bare (cust-065/072/075) kèm 1 câu đồng cảm/gợi ý quay lại ATTT.

10. **[PROMPT] Bỏ artefact hội thoại bịa.** sec-025 ("đã tra ở các lượt trước") — bịa ngữ cảnh phiên trước. Rule: **không tham chiếu "các lượt trước" nếu không có trong ngữ cảnh thực**.

11. **[PROMPT] Chuẩn tên CWE khớp số hiệu.** sec-021 (CWE-284 gọi "Improper Authentication" — thực "Improper Access Control"), sec-032 (CWE-27 sai tên). Rule: **đọc tên CWE từ ledger, không tự đặt tên theo số**.

12. **[PROMPT] Nhất quán loại lỗ hổng (SSRF vs RCE).** sec-009/sec-027 gọi ProxyLogon là "RCE" trong khi CWE-918=SSRF. Rule: **loại lỗ hổng = đọc CWE từ ledger; RCE chỉ khi ledger xác nhận hoặc nêu rõ "khi chain với CVE-x"**.
