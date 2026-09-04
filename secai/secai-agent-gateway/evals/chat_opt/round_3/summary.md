# Round 3 — Kết quả chấm chatbot IAS.AI (giám khảo Opus + WEB VERIFY)

Trọng tâm vòng này: **đo hiệu quả của rule "hard-grounding v3"** (chỉ nêu CVSS/phiên bản/mã khi có trong KB, thiếu → "không rõ").

- Tổng số câu: **200** | **mean_total = 89.66/100**
- Theo nguồn: **customer = 91.31** (n=100) | **secpro = 88.00** (n=100)
- **Δ vs R2 (89.36) = +0.29** · **Δ vs R1 (87.98) = +1.67**
- Δ theo nguồn vs R2: **customer 90.72 → 91.31 = +0.59** · **secpro 88.01 → 88.00 = −0.01 (đứng yên)**
- Web verify: **27 câu** đối chiếu nguồn thẩm quyền (13 lượt WebSearch mới trong vòng này) — **12 contradicted**, 15 ok.

> ⚠️ **Phát hiện then chốt: 162/200 câu trả lời TRÙNG KHÍT TỪNG BYTE với R2.** Rule hard-grounding v3 chỉ làm đổi **38/200** câu — và gần như TẤT CẢ là phía **customer** (thái độ/wrong-refusal). Toàn bộ câu **secpro sai-fact bị R2 bắt vẫn y nguyên** (Zerologon 5.5, XZ 5.6.1, PaperCut 22.0.6, ProxyLogon↔ProxyShell, 18379=9.1, 26855=RCE...). **accuracy_grounded gần như bất động: 82.4% → 82.3%.**

---

## 1. So sánh accuracy_grounded (câu hỏi trung tâm của vòng)

| Tiêu chí | R3 mean | R3 % | R2 % | R1 % | Δ% vs R2 |
|---|---:|---:|---:|---:|---:|
| **accuracy_grounded** | 16.46 | **82.3%** | 82.4% | 82.2% | **−0.1** |
| tool_grounding | 17.61 | 88.0% | 88.5% | 88.8% | −0.5 |
| answer_attitude | 22.54 | 90.2% | 89.0% | 86.4% | +1.1 |
| idk_handling | 9.01 | 90.1% | 90.5% | 90.4% | −0.5 |
| robustness | 9.39 | 93.9% | 93.2% | 87.6% | +0.7 |
| scope_discipline | 9.71 | 97.2% | 96.3% | 94.9% | +0.8 |
| language_format | 4.93 | 98.7% | 98.6% | 97.4% | +0.1 |

**accuracy_grounded đã BÃO HOÀ ở ~82%** qua 3 vòng (82.2 → 82.4 → 82.3). Rule prompt hard-grounding **không dịch chuyển được** con số này. Điểm tăng ròng (+0.29 tổng) hoàn toàn đến từ **answer_attitude/scope_discipline** phía customer (diệt nốt wrong-refusal), KHÔNG phải từ grounding.

## 2. Điểm theo category (R3 vs R2)

| Category | n | R3 | R2 | Δ |
|---|---:|---:|---:|---:|
| aggregate_stats | 11 | 74.55 | 72.82 | +1.73 |
| relation | 11 | 78.82 | 78.55 | +0.27 |
| edge_unknown | 12 | 83.67 | 83.67 | 0.00 |
| meta_self | 12 | 86.33 | 86.42 | −0.08 |
| vague_messy | 13 | 88.23 | 88.15 | +0.08 |
| random_offtopic | 12 | 89.00 | 89.00 | 0.00 |
| remediation | 11 | 89.64 | 90.00 | −0.36 |
| greeting_chitchat | 13 | 90.08 | 90.23 | −0.15 |
| poc_availability | 11 | 90.27 | 89.36 | +0.91 |
| capability | 13 | 91.00 | 91.00 | 0.00 |
| cve_lookup | 11 | 91.36 | 92.73 | −1.36 |
| cwe_capec | 11 | 93.27 | 93.36 | −0.09 |
| data_coverage | 12 | 94.42 | 94.75 | −0.33 |
| usage | 12 | 94.50 | 94.75 | −0.25 |
| concept | 11 | 95.09 | 95.82 | −0.73 |
| cve_detail | 11 | 95.73 | 96.18 | −0.45 |
| **system_overview** | 13 | **96.85** | 91.62 | **+5.23** |

Tăng lớn duy nhất là **system_overview (+5.23)** — nhờ cust-005 ("công cụ quét hay chatbot?") từ 28 (từ chối oan) → 96 (trả lời trực tiếp). Nhóm fact secpro (`cve_lookup`, `cve_detail`, `remediation`, `relation`) đứng yên hoặc tụt nhẹ.

## 3. Bảng đếm failure_bucket

| failure_bucket | R3 | R2 | R1 |
|---|---:|---:|---:|
| none | 171 | 170 | 166 |
| **hallucination** | **12** | 11 | 11 |
| other | 8 | 9 | — |
| wrong_refusal | 3 | 4 | 3 |
| tool_leak | 2 | 2 | 1 |
| no_grounding | 2 | 2 | 8 |
| not_robust | 1 | 1 | 2 |
| deflection | 1 | 1 | 9 |

**hallucination KHÔNG giảm** (11 → 12; sec-088 Jenkins CVE-tương-lai được xếp lại từ `other`→`hallucination`). wrong_refusal 4→3 (cust-005 được fix).

## 4. FACT-CHECK (WEB VERIFY) — 12 câu web-contradicted

**Đếm: 12 hallucination/sai-fact bắt qua web (bằng đúng R2=12). ĐÁNG CHÚ Ý: 0/12 claim R2-sai được sửa** — mọi câu sai của R2 hoặc trùng byte, hoặc đổi văn nhưng vẫn contradicted.

| id | claim SAI (R3) | fact ĐÚNG (nguồn thẩm quyền) | vs R2 |
|---|---|---|---|
| sec-011 | Zerologon CVE-2020-1472 = **5.5 MEDIUM, AV:L** | **10.0 CRITICAL** (unauth→domain admin) | **Y NGUYÊN** (byte-identical) |
| sec-038 | Vá Zerologon lặp "**5.5 MEDIUM**", "vá T10/2020" | 10.0 CRITICAL; bản vá **Aug 2020** | **Y NGUYÊN** |
| sec-042 | Vá XZ CVE-2024-3094 → "**5.6.1**" | 5.6.1 **VẪN dính**; phải **5.6.2** (hoặc <5.6.0) | **Y NGUYÊN** (nguy hiểm) |
| sec-043 | Vá PaperCut CVE-2023-27350 → "**22.0.6**" | **20.1.7 / 21.2.11 / 22.0.9** | **Y NGUYÊN** |
| sec-002 | Log4Shell ATT&CK **T1574.006/T1553.002**; cờ "**=false**" | ATT&CK ~**T1190/T1203/T1059**; cờ **=true** | **Y NGUYÊN** |
| sec-083 | 34473/34523 = chuỗi "**ProxyLogon**" | là **ProxyShell** (+31207); ProxyLogon=26855/26857/26858/27065 | **Y NGUYÊN** |
| sec-082 | CWE-787: cột năm lệch mã, đồng loạt 10.0; **CAPEC-92="Out-of-bounds Write"** | CAPEC-92 = **Forced Integer Overflow**; ATT&CK bịa | **Y NGUYÊN** |
| sec-055 | ATT&CK Initial Access #6–9 = **"Inbound Blasting","Initial Recon","Component"** | TA0001 thật: T1190/T1133/T1566/T1091/T1195/T1199/T1078... | **Y NGUYÊN** |
| sec-006 | CVE-2018-13379 CVSS **9.1** | NVD **9.8** (sản phẩm/KEV/CWE-22 đúng) | đổi văn, CVSS **vẫn 9.1 sai** |
| sec-009 | CVE-2021-26855 = "**RCE, CVSS 9.1**" | NVD: **SSRF, 9.8** (RCE chỉ khi chain 27065) | **Y NGUYÊN** |
| sec-024 | "CVE-2017-5638 **không có** module Metasploit" | Có **struts2_content_type_ognl (S2-045)** → miss truy hồi | **Y NGUYÊN** (no_grounding) |
| sec-065 | "CVSS cao nhất KB = **9.8, không có 10.0**" | KB đầy CVE **10.0** (Log4Shell...) → vô lý | đổi văn (bỏ số "8.8" của R2) **nhưng VẪN sai** |

**Đã xác nhận ĐÚNG (ok) trong nhóm câu đổi văn:** sec-001 (TeamCity 9.8/CWE-288/KEV), sec-007 (F5-1388 9.8/CWE-306 auth-bypass), sec-014 (41773 9.8/CWE-22 + trung thực thiếu version), sec-020 (PrintNightmare **8.8 HIGH** — chuẩn NVD), sec-025 (MOVEit **có** module Metasploit), sec-027 (26855=SSRF + 27065=RCE, PoC thật), sec-036 (BlueKeep mitigation).

> **Mâu thuẫn NỘI BỘ vẫn nguyên:** sec-060 ("Fortinet 1 CVE") vs sec-006/sec-080; sec-058 ("cũ nhất 2007") vs phủ-1999; sec-065 ("KB không có 10.0") vs sec-002/sec-082 ("nhiều 10.0"); sec-042 ("XZ 5.6.1") vs sec-022 ("XZ 5.6.2"). Cùng một sự thật khi đúng khi sai → thiếu **nguồn chân lý thống nhất** (KB ledger).

## 5. 20 case TỆ NHẤT (R3)

| id | cat | total | bucket | reason |
|---|---|---:|---|---|
| sec-097 | edge_unknown | 12 | not_robust | CVE 9 chữ số → TRẢ RỖNG + http 422 (vỡ) |
| sec-057 | aggregate_stats | 22 | tool_leak | Lộ `qdrant_search(query=...)` ra text |
| sec-092 | edge_unknown | 22 | tool_leak | Lộ `qdrant_search(query="CVE-2021-999999")` |
| cust-100 | meta_self | 23 | wrong_refusal | "Bạn có phải người thật?" bị chặn injection (FP) |
| cust-055 | vague_messy | 46 | wrong_refusal | "cái này là seo" → từ chối vội |
| cust-023 | capability | 47 | wrong_refusal | "Kiểm tra domain có lỗ hổng" → từ chối "ngoài phạm vi" |
| sec-082 | relation | 62 | hallucination | CWE-787: năm lệch mã, 10.0 đồng loạt, CAPEC/ATT&CK bịa |
| sec-060 | aggregate_stats | 64 | no_grounding | "Fortinet chỉ 1 CVE" mâu thuẫn sec-006/sec-080 |
| sec-055 | cwe_capec | 65 | hallucination | ATT&CK Initial Access #6–9 bịa tên kỹ thuật |
| cust-083 | greeting_chitchat | 66 | deflection | "Cảm ơn lúc nãy" → chỉ tail |
| sec-011 | cve_lookup | 66 | hallucination | Zerologon "5.5 MEDIUM" (thực 10.0 CRITICAL) |
| sec-024 | poc_availability | 68 | no_grounding | "2017-5638 không có module MSF" (thực có) |
| sec-058 | aggregate_stats | 69 | hallucination | "CVE cũ nhất 2007-0061" mâu thuẫn phủ-1999 |
| sec-065 | aggregate_stats | 72 | hallucination | "KB không có CVE 10.0" mâu thuẫn KB đầy 10.0 |
| sec-064 | aggregate_stats | 73 | other | ">300 CVE có PoC" nêu số như sự thật |
| sec-078 | relation | 73 | hallucination | CWE-89 CVEs: 10.0 đồng loạt + nhiều CVE tương lai |
| sec-042 | remediation | 76 | hallucination | Vá XZ "5.6.1" (nguy hiểm, phải 5.6.2) |
| sec-083 | relation | 76 | hallucination | Gán nhầm 34473/34523 là "ProxyLogon" (thực ProxyShell) |
| sec-088 | relation | 76 | hallucination | Jenkins lẫn CVE tương lai bịa (2026-53435/2025-67635) |
| sec-002 | cve_lookup | 77 | hallucination | Log4Shell CVSS/version đúng nhưng ánh xạ ATT&CK + cờ =false sai |

## 6. KẾT LUẬN — Rule hard-grounding v3 có giảm hallucination không?

**KHÔNG. Đã BÃO HOÀ ở tầng prompt.** Bằng chứng:

1. **162/200 câu byte-identical với R2** → rule mới thực tế không chạm tới các câu fact secpro. Model không kích hoạt fallback "không rõ" vì nó **vẫn có "một nguồn"** để đọc CVSS/version (KB ledger sai, hoặc trí nhớ model) — rule "chỉ nêu khi có trong KB" bị thoả mãn bởi chính giá trị SAI đang nằm trong KB.
2. **accuracy_grounded 82.4% → 82.3% (bất động)**; **12/12 claim web-contradicted của R2 KHÔNG cái nào được sửa** (sec-065/sec-006 đổi văn nhưng vẫn sai).
3. Gain +0.29 tổng **hoàn toàn ở customer** (answer_attitude/scope): cust-005 28→96, system_overview +5.23.

➡️ **Điểm nghẽn KHÔNG còn ở prompt mà ở [DATA/CODE]:** (a) KB ledger chứa **giá trị sai** (Zerologon 5.5, 18379=9.1, PaperCut fix=22.0.6) và **CVE tổng hợp/tương lai** (2026-xxxx) → model "grounded vào rác"; (b) **không có tool aggregate/verify** để bắt mâu thuẫn nội bộ. Muốn hạ hallucination phải **sửa dữ liệu + thêm lớp kiểm chứng**, prompt đã hết dư địa.

## 7. Đề xuất tiếp theo (ưu tiên theo tác động)

1. **[DATA] Backfill/hiệu đính CVSS·severity·fixed-version từ NVD vào KB ledger** — tác động cao nhất. Sửa trực tiếp: Zerologon 10.0, CVE-2018-13379 9.8, CVE-2021-26855 SSRF/9.8, PaperCut fix 20.1.7/21.2.11/22.0.9, XZ 5.6.2. Đây là gốc rễ của ≥8/12 hallucination — prompt vô hiệu vì KB sai.
2. **[DATA] Lọc CVE tổng hợp/tương lai (2026-xxxx) và bảng CVSS đồng loạt 10.0 khỏi KB** — nguồn của sec-082/sec-078/sec-088/sec-080. Thêm validator: CVE năm > năm hiện tại hoặc CVSS đồng nhất bất thường → loại/gắn cờ.
3. **[CODE] Lớp verify-sau-sinh cho fact số (CVSS/fixed-version/CWE-name)** — so field bot xuất với ledger; lệch → thay bằng "không rõ" HOẶC giá trị ledger. Đây là cách DUY NHẤT bắt được lỗi mà prompt bỏ qua (vì lỗi nằm trong chính KB, cần cross-check NVD/nguồn thứ 2).
4. **[CODE] Tool aggregate + "single source of truth"** cho aggregate_stats (max CVSS, CVE cũ nhất, count theo năm/sản phẩm) — dứt điểm sec-058/sec-060/sec-064/sec-065 (nhóm điểm thấp nhất, mâu thuẫn nội bộ). Mẫu tốt đã có: sec-063 ("chưa có tool đếm + gợi ý NVD").
5. **[CODE] Bắt leak tool-call & empty/422** — parser bắt `*_search(...)` xuất ra text (sec-057/sec-092) → ép chạy tool; guard "empty/422" → "không xử lý được" (sec-097). Tồn tại từ R1, chưa sửa.
6. **[PROMPT] Nới false-positive injection & câu scan chủ động** — cust-100 ("Bạn có phải người thật?" bị chặn), cust-023/cust-055. Đây là dư địa PROMPT còn lại thật sự (nhưng tác động nhỏ, ~3 case).
7. **[PROMPT] Cấm ATT&CK/CAPEC "trang trí"** — sec-001/sec-037/sec-002 đính kèm T-id/CAPEC-id không grounded. Rule: chỉ liệt kê mapping có trong ledger. (Lưu ý: R2 đã đề xuất nhưng answers không đổi → cần đi kèm [CODE] chặn, không chỉ prompt.)
8. **[PROMPT] Câu cảm ơn xã giao** — cust-083 ("cảm ơn lúc nãy") chỉ trả tail → thêm 1 câu "rất vui được giúp".
