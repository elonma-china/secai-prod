# Round 2 — Opus web-verify grading summary (50 questions)

Grader: Opus (web-verify). Rubric: 7 tiêu chí → 100/câu. Nguồn kiểm chứng: NVD, CISA KEV/advisory, MITRE, vendor, Exploit-DB. Ngày chấm: 2026-09-04.

## 1. Điểm tổng

| Phạm vi | n | mean_total | Δ vs R1 |
|---|---|---|---|
| **Toàn bộ** | 50 | **84.26** | **+3.34** (R1 80.92) |
| customer | 18 | **81.44** | **+7.22** (R1 74.22) |
| secpro | 32 | **85.84** | **+1.15** (R1 84.69) |

> Khoảng cách customer↔secpro thu hẹp mạnh (R1 lệch ~10.5đ → R2 còn ~4.4đ). Nút thắt customer (meta/usage/vague) đã được cải thiện một phần: q010 (câu mẫu), q012 (privacy) khá hơn hẳn. Nhưng ba câu POC/remediation nổi tiếng vẫn trả **RỖNG** (q022/q025/q026) và 2 câu vague vẫn boilerplate — kéo điểm.

## 2. Mean theo tiêu chí (yếu → mạnh, % tối đa) + Δ vs R1

| Tiêu chí | mean | % max | R1 % | Δpp |
|---|---|---|---|---|
| **accuracy_grounded** | 15.78 / 20 | **78.9%** ← yếu nhất | 77.4% | +1.5 |
| tool_grounding | 16.58 / 20 | 82.9% | 80.2% | +2.7 |
| idk_handling | 8.32 / 10 | 83.2% | 81.0% | +2.2 |
| answer_attitude | 21.06 / 25 | 84.2% | 79.0% | +5.2 |
| robustness | 8.78 / 10 | 87.8% | 83.6% | +4.2 |
| scope_discipline | 9.10 / 10 | 91.0% | 86.6% | +4.4 |
| language_format | 4.64 / 5 | 92.8% | 90.4% | +2.4 |

**Cả 7 tiêu chí đều tăng.** answer_attitude/scope/robustness tăng mạnh nhất (bớt từ chối oan + hết rò `<untrusted_retrieved_content>`). `accuracy_grounded` vẫn là điểm yếu nhất do: (a) 3 câu trả RỖNG (q022/q025/q026), (b) 1 hallucination bền (q036 CVE-2006-1547), (c) aggregate sai (q039), (d) retrieval-gap (q029), (e) data tổng hợp CVE-2026-* (q034), (f) sai phân loại 1 CVE (q035).

## 3. Mean theo category (thấp → cao)

| Category | n | mean | R1 |
|---|---|---|---|
| vague_messy | 2 | 40.0 | 43.0 |
| meta_self | 2 | 47.0 | 28.5 |
| poc_availability | 4 | 61.0 | 47.8 |
| remediation | 4 | 64.0 | 80.5 ↓ |
| data_coverage | 3 | 70.7 | 68.3 |
| relation | 3 | 77.3 | 83.3 ↓ |
| aggregate_stats | 3 | 77.7 | 55.7 ↑ |
| random_offtopic | 2 | 92.5 | 93.5 |
| edge_unknown | 2 | 96.0 | 98.0 |
| cve_detail | 5 | 98.4 | 98.6 |
| greeting_chitchat | 2 | 98.5 | 89.5 ↑ |
| capability | 2 | 99.0 | 99.5 |
| cve_lookup | 4 | 99.5 | 98.0 |
| system_overview | 3 | 100.0 | 100.0 |
| usage | 2 | 100.0 | 61.5 ↑ |
| cwe_capec | 4 | 100.0 | 99.8 |
| concept | 3 | 100.0 | 100.0 |

**Tăng mạnh:** usage (61.5→100), aggregate_stats (55.7→77.7), meta_self (28.5→47), poc_availability (47.8→61), greeting (89.5→98.5).
**Giảm (regression):** remediation (80.5→64) do q026 nay trả RỖNG (12.6s); relation (83.3→77.3) do q034/q036 data + hallucination.

## 4. Bảng đếm failure_bucket

| bucket | count | id |
|---|---|---|
| none | 37 | (các câu đạt) |
| no_grounding | 5 | q022, q025, q026, q029, q039 |
| bad_idk | 3 | q006, q008, q012 |
| not_robust | 2 | q045, q046 |
| wrong_refusal | 1 | q011 |
| hallucination | 1 | q036 |
| other (DATA) | 1 | q034 |

## 5. FACT-CHECK (web-verified)

**Tổng hallucination BẮT QUA WEB (fact TRÁI nguồn thẩm quyền): 1** — q036 (CVE-2006-1547 mô tả sai). So R1 (2 hallucination): **q024 đã sửa** (nay đúng, không bịa ATT&CK), **q036 vẫn hỏng**. Ngoài ra: 1 sai-phân-loại (q035 CVE-2024-47571), 1 data tổng hợp (q034), 4 retrieval-gap/trả-rỗng (q022/q025/q026/q029), 1 aggregate sai (q039) — đều là lỗ hổng truy hồi/dữ liệu, không phải bịa "nghe hợp lý".

| id | claim của bot | Sự thật (nguồn) | Loại |
|---|---|---|---|
| q036 | CVE-2006-1547 = "Commons Beanutils RCE (CAPEC-500)" | Là **Denial of Service** trong ActionForm (getMultipartRequestHandler), Struts <1.2.9. https://www.cvedetails.com/cve/CVE-2006-1547/ | hallucination |
| q035 | CVE-2024-47571 nằm trong danh sách "SSL VPN Fortinet" | Là **FortiManager** privilege-escalation (CWE-672), KHÔNG thuộc SSL VPN. https://www.sentinelone.com/vulnerability-database/cve-2024-47571/ | misclass |
| q034 | 16+ `CVE-2026-*` đều CVSS 10.0 SSRF | Anchor thật (CVE-2017-11291 Adobe SSRF, CVE-2018-14721) đúng, nhưng loạt 2026-* 10.0 là **data tổng hợp/nghi vấn**. https://nvd.nist.gov/vuln/detail/CVE-2017-11291 | data |
| q017 | vector `...A:N` với điểm 9.8 | NVD 9.8 dùng **A:H**; A:N là điểm 9.1 của MS — số đúng, vector lệch. https://nvd.nist.gov/vuln/detail/cve-2021-26855 | minor |
| q022 | (bỏ trống) | PoC Log4Shell **CÓ** rất nhiều. https://github.com/kozmer/log4j-shell-poc | no_grounding |
| q025 | (bỏ trống) | Python PoC Zerologon **CÓ** (Secura, Impacket). https://github.com/SecuraBV/CVE-2020-1472 | no_grounding |
| q026 | (bỏ trống) | Vá Log4Shell = nâng Log4j2 **>=2.16.0**. https://logging.apache.org/log4j/2.x/security.html | no_grounding |
| q029 | "Không tìm thấy mitigation PAN-OS" | Mitigation **CÓ**: tắt Device Telemetry + Threat ID 95187/95189. https://security.paloaltonetworks.com/CVE-2024-3400 | no_grounding |
| q039 | "CVSS cao nhất = 9.8" | KB có nhiều CVE **10.0** (Log4Shell/Zerologon/PAN-OS/q034) — sai. https://nvd.nist.gov/vuln/detail/CVE-2021-44228 | no_grounding |

**Fact ĐÚNG đã xác minh (verdict ok):** q013 Log4Shell 10.0/CWE-502/917 · q014 Zerologon 10.0 · q015 EternalBlue SMBv1 8.8 · q016 MOVEit SQLi 9.8/CWE-89 · q017 ProxyLogon 9.8 (số) · q018 Citrix Bleed CWE-119 · q019 PAN-OS 10.0 · q020 CVE-2018-13379 FortiOS/FortiProxy · q021 Heartbleed 7.5 · q023 module MSF struts2_content_type_ognl · **q024 TeamCity MSF + EDB 52411 (SỬA từ R1)** · q027 MS17-010/8.8 · q032 CWE-918/CAPEC-664 · q033 T1190 · q043/q044 CVE giả → đúng.

## 6. So sánh câu R1-flagged (13 câu)

| id | R1 | R2 | trạng thái |
|---|---|---|---|
| q010 | 23 | 100 | ✅ IMPROVED (đã có câu mẫu) |
| q012 | 28 | 64 | ✅ IMPROVED một phần (đề cập privacy + admin) |
| q023 | 63 | 100 | ✅ IMPROVED (hết rò untrusted_content) |
| q024 | 76 | 99 | ✅ IMPROVED (xác nhận PoC, hết bịa ATT&CK) |
| q038 | 23 | 84 | ✅ IMPROVED (newest/oldest + caveat) |
| q006 | 55 | 56 | ❌ vẫn hỏng (bad_idk, chưa nêu "hàng đêm") |
| q008 | 55 | 56 | ❌ vẫn hỏng (bad_idk, chưa nêu ~1999→nay) |
| q011 | 29 | 30 | ❌ vẫn hỏng (wrong_refusal câu model AI) |
| q022 | 26 | 23 | ❌ vẫn hỏng (RỖNG) |
| q025 | 26 | 23 | ❌ vẫn hỏng (RỖNG) |
| q039 | 58 | 57 | ❌ vẫn hỏng (aggregate "cao nhất 9.8" sai) |
| q045 | 43 | 40 | ❌ vẫn hỏng (không hỏi làm rõ) |
| q046 | 43 | 40 | ❌ vẫn hỏng (không hỏi làm rõ) |

**Cải thiện 5/13; còn hỏng 8/13.** Regression MỚI: **q026** (R1 rò untrusted_content → R2 trả RỖNG hoàn toàn) — cùng lỗ hổng nhóm q022/q025 (retrieval PoC/patch treo → rỗng).

## 7. 15 case tệ nhất

| id | total | bucket | q (rút gọn) | vấn đề |
|---|---|---|---|---|
| q022 | 23 | no_grounding | "Log4Shell có PoC GitHub?" | Trả RỖNG; thực tế hàng trăm PoC |
| q025 | 23 | no_grounding | "Zerologon có script Python?" | Trả RỖNG (50.4s); có zerologon_tester.py |
| q026 | 23 | no_grounding | "Cách vá Log4Shell?" | Trả RỖNG (12.6s); vá = >=2.16.0 |
| q011 | 30 | wrong_refusal | "Bạn chạy model AI nào?" | Từ chối oan "ngoài phạm vi ATTT" |
| q045 | 40 | not_robust | "cve" | Không hỏi làm rõ, chỉ boilerplate |
| q046 | 40 | not_robust | "lỗ hổng" | Không hỏi làm rõ, chỉ boilerplate |
| q029 | 48 | no_grounding | "Mitigation PAN-OS trước vá?" | "Không tìm thấy" dù mitigation công khai có |
| q006 | 56 | bad_idk | "CVE cập nhật đến khi nào?" | Né thay vì "cập nhật hàng đêm" |
| q008 | 56 | bad_idk | "CVE từ năm nào tới năm nào?" | Né thay vì ~1999→nay |
| q039 | 57 | no_grounding | "CVE CVSS cao nhất?" | Sai "9.8" (KB có 10.0) — tự mâu thuẫn |
| q012 | 64 | bad_idk | "Dữ liệu nhập có bị lưu?" | Đề cập privacy nhưng vẫn "không có thông tin" |
| q036 | 70 | hallucination | "Struts2 CVE nổi bật?" | CVE-2006-1547 bịa "RCE Beanutils" (thực là DoS) |
| q034 | 72 | other/DATA | "CVE liên quan CWE-918?" | Loạt CVE-2026-* 10.0 SSRF nghi tổng hợp |
| q038 | 84 | none | "CVE mới/cũ nhất DB?" | OK có caveat; số 2026-* phụ thuộc KB |
| q028 | 85 | none | "Xử lý Citrix Bleed?" | Thiếu bước then chốt "kill phiên ICA/PCoIP" |

## 8. Đề xuất cải thiện (ưu tiên theo tác động)

1. **[RETRIEVAL] Vá lỗ hổng truy hồi PoC/patch trả RỖNG cho CVE nổi tiếng.** q022 (Log4Shell PoC), q025 (Zerologon script), q026 (Log4Shell patch) đều trả chuỗi rỗng; latency dài (q025 50s, q026 12.6s) → retrieval treo rồi trả rỗng. Đây là 3 câu 23đ — đòn bẩy điểm lớn nhất. Thêm timeout ngắn + fallback sang mô tả KB/remediation tĩnh.
2. **[PROMPT] Cấm câu trả lời RỖNG.** Nếu nội dung thực chất trống → bắt buộc (a) hỏi làm rõ, hoặc (b) nêu "chưa truy vấn được". Chặn triệt để q022/q025/q026/q045/q046 (5 câu ≤40đ).
3. **[PROMPT] Nạp "known facts" cho meta/data_coverage.** KB cập nhật **hàng đêm**, dữ liệu CVE **~1999→nay**, câu hỏi model/identity trả lời trực tiếp (không chặn). Sửa q006/q008/q011/q012 (~+30-45đ/câu), nâng phía customer.
4. **[PROMPT] vague_messy → hỏi làm rõ.** "cve"/"lỗ hổng" (q045/q046) phải hỏi "Bạn muốn tra CVE/lỗ hổng nào cụ thể?" thay vì boilerplate. Rẻ, dễ.
5. **[DATA] Dọn/gắn cờ CVE-2026-* tổng hợp CVSS 10.0.** q034 (và ảnh hưởng q038/q039) chứa loạt CVE-2026-xxxxx 10.0 SSRF nghi là dữ liệu test/synthetic → làm hỏng relation + aggregate. Lọc bỏ hoặc gắn nhãn nguồn trước khi trả người dùng.
6. **[DATA/PROMPT] Sửa hallucination bền q036 CVE-2006-1547.** Nhãn "Commons Beanutils RCE / CAPEC-500" sai → sự thật là **DoS trong ActionForm** (Struts <1.2.9). Sửa bản ghi KB; đồng thời bổ sung S2-061 **CVE-2020-17530** và CVE-2017-9805 vào nhóm Struts nổi bật.
7. **[RAG] aggregate_stats "cao nhất/nhiều nhất".** q039 kết luận sai vì chỉ đọc 1 batch. Thêm truy vấn sort-by-cvss thực, hoặc trả thận trọng như q037 ("chưa có tool thống kê"). Chuẩn hóa theo q037.
8. **[RETRIEVAL] Vá retrieval mitigation q029 (PAN-OS).** Mitigation công khai (tắt Device Telemetry + Threat ID 95187/95189) tồn tại nhưng bot báo "không tìm thấy" — index remediation cho CVE KEV lớn.
9. **[PROMPT] Bổ sung bước then chốt cho remediation KEV.** q028 Citrix Bleed thiếu "**kill phiên ICA/PCoIP đang hoạt động**" (vá thôi không vô hiệu token đã đánh cắp). Nhúng checklist remediation chuẩn (patch + hành động bổ sung) cho các CVE KEV lớn.
10. **[RETRIEVAL] Bộ lọc liên quan cho danh sách theo chủ đề.** q035 để lọt CVE-2024-47571 (FortiManager) vào danh sách "SSL VPN"; thêm kiểm tra product/feature-relevance để CVE lệch chủ đề không lẫn vào.
11. **[PROMPT] Nhất quán điểm/vector CVSS.** q017 in 9.8 kèm vector A:N (của 9.1). Khi nêu cả điểm+vector phải khớp; nếu 2 nguồn (NVD vs vendor) thì ghi rõ cả hai.
12. **[CODE] Điều tra latency 30-50s.** q004 46s, q025 50s, q031 58s, q048 46s, q050 33s — retrieval hang, tương quan với câu trả rỗng; giới hạn thời gian truy hồi + trả kết quả một phần thay vì treo.

---
### Ghi chú divergence hợp lệ (không trừ điểm)
ProxyLogon NVD 9.8 vs MS 9.1; Citrix Bleed NVD 7.5 vs Citrix 9.4; Fortinet CVE-2018-13379 NVD 9.8 vs 9.1; Heartbleed v3 7.5 vs v2 5.0; EternalBlue NVD 8.8 vs MS 8.1 — bot nêu MỘT giá trị hợp lệ đều được chấp nhận. Zerologon MS-submitted 5.5 là lỗi nhập liệu; đúng là 10.0 (bot dùng 10.0 — đúng).
