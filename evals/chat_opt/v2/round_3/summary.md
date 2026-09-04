# Round 3 — Opus web-verify grading summary (50 questions)

Grader: Opus (web-verify). Rubric: 7 tiêu chí → 100/câu. Nguồn kiểm chứng: NVD, CISA KEV/advisory, MITRE, vendor (Fortinet/Palo Alto/Citrix/Apache), Exploit-DB. Ngày chấm: 2026-09-04.

## 1. Điểm tổng

| Phạm vi | n | mean_total | Δ vs R2 | Δ vs R1 |
|---|---|---|---|---|
| **Toàn bộ** | 50 | **84.14** | **-0.12** (R2 84.26) | **+3.22** (R1 80.92) |
| customer | 18 | **80.83** | **-0.61** (R2 81.44) | **+6.61** (R1 74.22) |
| secpro | 32 | **86.00** | **+0.16** (R2 85.84) | **+1.31** (R1 84.69) |

> **Đứng yên so với R2.** Đợt "hooks fix" R2 gần như KHÔNG chuyển kim: 1 câu được vá lớn (q026 remediation Log4Shell 23→95) bị **triệt tiêu** bởi 1 regression mới cùng nhóm (q028 Citrix Bleed 85→23 RỖNG) + 2 câu vague tụt nhẹ (q045/q046 40→36). Nút thắt vẫn nguyên: câu POC/remediation nổi tiếng trả **RỖNG**, meta/data_coverage bị **né**, aggregate "cao nhất" **sai**, 1 hallucination bền (q036).

## 2. Mean theo tiêu chí (yếu → mạnh, % tối đa) + Δ vs R2

| Tiêu chí | mean | % max | R2 % | Δpp |
|---|---|---|---|---|
| **accuracy_grounded** | 15.78 / 20 | **78.9%** ← yếu nhất | 78.9% | 0.0 |
| tool_grounding | 16.58 / 20 | 82.9% | 82.9% | 0.0 |
| answer_attitude | 20.82 / 25 | 83.3% | 84.2% | **-0.9** |
| idk_handling | 8.36 / 10 | 83.6% | 83.2% | +0.4 |
| robustness | 8.84 / 10 | 88.4% | 87.8% | +0.6 |
| scope_discipline | 9.08 / 10 | 90.8% | 91.0% | -0.2 |
| language_format | 4.68 / 5 | 93.6% | 92.8% | +0.8 |

**accuracy_grounded đóng băng ở 78.9%** — vẫn là điểm yếu nhất, kéo bởi: (a) 4 câu RỖNG/không-grounded (q022/q025/q028/q029), (b) 1 hallucination bền (q036 CVE-2006-1547), (c) aggregate sai (q039), (d) data tổng hợp CVE-2026-* (q034), (e) misclass + CVSS vống (q035). **answer_attitude GIẢM** vì q028 nay RỖNG và q045/q046 mất luôn phần boilerplate.

## 3. Mean theo category (thấp → cao)

| Category | n | mean | R2 | ghi chú |
|---|---|---|---|---|
| vague_messy | 2 | 36.0 | 40.0 ↓ | q045/q046 nay RỖNG hoàn toàn |
| meta_self | 2 | 48.0 | 47.0 | q011 vẫn từ chối oan; q012 partial |
| poc_availability | 4 | 61.5 | 61.0 | q022/q025 RỖNG; q023/q024 tốt |
| remediation | 4 | 66.5 | 64.0 | q026 SỬA (95) nhưng q028 VỠ (23) |
| data_coverage | 3 | 70.0 | 70.7 | q006/q008 bad_idk |
| relation | 3 | 73.7 | 77.3 ↓ | q034 data, q035 misclass, q036 hallucination |
| aggregate_stats | 3 | 77.7 | 77.7 | q037/q038 tốt, q039 sai |
| random_offtopic | 2 | 92.0 | 92.5 | q047 hơi cụt |
| edge_unknown | 2 | 97.0 | 96.0 | CVE giả → 'không tìm thấy' đúng |
| cve_detail | 5 | 98.8 | 98.4 | q017 lệch vector nhẹ |
| capability | 2 | 99.5 | 99.0 | |
| greeting_chitchat | 2 | 97.0 | 98.5 | |
| cve_lookup | 4 | 99.5 | 99.5 | |
| system_overview | 3 | 100.0 | 100.0 | |
| usage | 2 | 100.0 | 100.0 | |
| cwe_capec | 4 | 100.0 | 100.0 | |
| concept | 3 | 100.0 | 100.0 | |

## 4. Bảng đếm failure_bucket

| bucket | count | id |
|---|---|---|
| none | 36 | (các câu đạt) |
| no_grounding | 5 | q022, q025, **q028**, q029, q039 |
| bad_idk | 3 | q006, q008, q012 |
| other (DATA/misclass) | 2 | q034, q035 |
| not_robust | 2 | q045, q046 |
| wrong_refusal | 1 | q011 |
| hallucination | 1 | q036 |

## 5. VẤN ĐỀ RỖNG / CLOSER-ONLY (câu hỏi trọng tâm của loop)

**Đếm câu RỖNG (chỉ "Bạn cần IAS.AI hỗ trợ thêm gì nữa không?" không nội dung): 5** — q022, q025, **q028**, q045, q046.

**KHÔNG cải thiện — chỉ dịch chuyển hotspot:**
- R2 có 5 câu RỖNG/boilerplate: q022, q025, q026 (RỖNG) + q045, q046 (boilerplate).
- R3 vẫn 5 câu RỖNG: **q026 đã SỬA** (nay câu vá đầy đủ) nhưng **q028 MỚI VỠ** (R2=85 → R3=23), và q045/q046 tụt từ boilerplate xuống RỖNG thuần.
- => Fix R2 chỉ vá đúng q026; lỗ hổng retrieval PoC/patch treo→rỗng **vẫn còn**, chỉ đổi câu trúng (q026→q028). Latency dài đi kèm: q025 50.4s (RỖNG), q046 32.9s (RỖNG), q028 9.4s.

## 6. FACT-CHECK (web-verified)

**Tổng hallucination BẮT QUA WEB (fact TRÁI nguồn thẩm quyền): 1** — q036 (CVE-2006-1547). Giống R2. Ngoài ra: 1 misclass + 1 CVSS vống (q035), 1 data tổng hợp (q034), 3 retrieval-gap/RỖNG (q022/q025/q028) + 1 retrieval-gap 'không tìm thấy' (q029), 1 aggregate sai (q039) — lỗ hổng truy hồi/dữ liệu, không phải bịa "nghe hợp lý".

| id | claim của bot | Sự thật (nguồn) | Loại |
|---|---|---|---|
| q036 | CVE-2006-1547 = "Commons Beanutils RCE (CAPEC-500)", HIGH | **DoS** trong ActionForm (getMultipartRequestHandler), Struts <1.2.9 — KHÔNG phải Beanutils RCE. https://www.cvedetails.com/cve/CVE-2006-1547/ | hallucination |
| q035 | CVE-2024-47571 nằm trong "SSL VPN Fortinet" | **FortiManager** priv-esc (CWE-672), KHÔNG thuộc SSL VPN. https://fortiguard.fortinet.com/psirt/FG-IR-24-239 | misclass |
| q035 | CVE-2020-12812 CVSS 9.8 CRITICAL | Là SSL VPN 2FA bypass THẬT nhưng CVSS ~**5.2** (không phải 9.8). https://community.fortinet.com/t5/FortiGate/Technical-Tip-Description-of-CVE-2020-12812 | minor |
| q034 | 16+ `CVE-2026-*` đều 10.0 SSRF; CVE-2018-14721 dưới "Năm 2019" | Anchor thật đúng (CVE-2018-14721 SSRF **10.0**), nhưng loạt 2026-* 10.0 là **data tổng hợp** + gắn sai năm (2018≠2019). https://github.com/advisories/GHSA-9mxf-g3x6-wv74 | data |
| q039 | "CVSS cao nhất = 9.8 (CVE-2018-6809)" | KB có nhiều CVE **10.0** (Log4Shell q013, CVE-2018-14721 q034, CVE-2007-0061 q038) → sai/tự mâu thuẫn. https://nvd.nist.gov/vuln/detail/CVE-2021-44228 | no_grounding |
| q017 | điểm 9.8 kèm vector `...A:N` | NVD 9.8 dùng **A:H**; A:N là điểm MS 9.1 — số đúng, vector lệch. https://nvd.nist.gov/vuln/detail/CVE-2021-26855 | minor |
| q022 | (RỖNG) | PoC Log4Shell **CÓ** rất nhiều. https://github.com/kozmer/log4j-shell-poc | no_grounding |
| q025 | (RỖNG) | Python PoC Zerologon **CÓ** (Secura, Impacket). https://github.com/SecuraBV/CVE-2020-1472 | no_grounding |
| q028 | (RỖNG) | Remediation Citrix Bleed **CÓ**: patch + **kill phiên ICA/PCoIP**. https://support.citrix.com/article/CTX579459 | no_grounding |
| q029 | "Không tìm thấy mitigation PAN-OS" | Mitigation **CÓ**: tắt Device Telemetry + Threat ID 95187/95189. https://security.paloaltonetworks.com/CVE-2024-3400 | no_grounding |

**Fact ĐÚNG đã xác minh (verdict ok):** q013 Log4Shell 10.0/CWE-502-917 · q014 Zerologon 10.0/Netlogon · q015 EternalBlue SMBv1 8.8 · q016 MOVEit SQLi CWE-89 9.8 · q017 ProxyLogon 9.8 (số) · q018 Citrix Bleed CWE-119 · q019 PAN-OS 10.0 · q020 CVE-2018-13379 FortiOS/FortiProxy · q021 Heartbleed v3 7.5 · q023 MSF struts2_content_type_ognl/CWE-755 · q024 TeamCity MSF + EDB 52411 · q027 MS17-010 8.8 · q032 CWE-918/CAPEC-664 · q033 T1190 · q035 CVE-2020-12812 là SSL VPN thật · **q038 oldest CVE-2007-0061 VMware 10.0 (2007) — có thật** · q043/q044 CVE giả → 'không tìm thấy' đúng.

## 7. So sánh 9 câu MỤC TIÊU của fix R2 (q006,q008,q011,q022,q025,q026,q039,q045,q046)

| id | R2 | R3 | trạng thái |
|---|---|---|---|
| q026 | 23 | **95** | ✅ **IMPROVED** (nay trả cách vá đầy đủ) |
| q006 | 56 | 55 | ❌ flat — vẫn né 'không có thông tin ngày cập nhật' |
| q008 | 56 | 55 | ❌ flat — vẫn né khoảng năm ~1999→nay |
| q011 | 30 | 32 | ❌ flat — vẫn từ chối oan câu model AI |
| q022 | 23 | 23 | ❌ flat — vẫn RỖNG |
| q025 | 23 | 23 | ❌ flat — vẫn RỖNG |
| q039 | 57 | 55 | ❌ flat — vẫn sai "cao nhất 9.8" |
| q045 | 40 | 36 | ❌ **REGRESSED** — nay RỖNG thuần (mất boilerplate) |
| q046 | 40 | 36 | ❌ **REGRESSED** — nay RỖNG thuần |

**Chỉ 1/9 mục tiêu cải thiện (q026); 6 flat, 2 regressed.** Ngoài danh sách mục tiêu còn 1 **regression mới nghiêm trọng: q028** (R2 85 → R3 23 RỖNG) — cùng lỗ hổng nhóm q022/q025 (retrieval remediation treo→rỗng), đúng chỗ mà fix q026 vừa "vá" xong lại rơi sang câu bên cạnh.

## 8. 15 case tệ nhất

| id | total | bucket | q (rút gọn) | vấn đề |
|---|---|---|---|---|
| q022 | 23 | no_grounding | "Log4Shell có PoC GitHub?" | RỖNG; thực tế hàng trăm PoC |
| q025 | 23 | no_grounding | "Zerologon có script Python?" | RỖNG (50.4s); có zerologon_tester.py |
| q028 | 23 | no_grounding | "Xử lý Citrix Bleed?" | **RỖNG (REGRESSION R2=85)**; thiếu patch + kill ICA/PCoIP |
| q011 | 32 | wrong_refusal | "Bạn chạy model AI nào?" | Từ chối oan 'ngoài phạm vi ATTT' |
| q045 | 36 | not_robust | "cve" | RỖNG (regressed); không hỏi làm rõ |
| q046 | 36 | not_robust | "lỗ hổng" | RỖNG (32.9s); không hỏi làm rõ |
| q029 | 48 | no_grounding | "Mitigation PAN-OS trước vá?" | 'Không tìm thấy' dù mitigation công khai có |
| q006 | 55 | bad_idk | "CVE cập nhật đến khi nào?" | Né thay vì 'cập nhật hàng đêm' |
| q008 | 55 | bad_idk | "CVE từ năm nào tới năm nào?" | Né thay vì ~1999→nay |
| q039 | 55 | no_grounding | "CVE CVSS cao nhất?" | Sai '9.8' (KB có 10.0) — tự mâu thuẫn |
| q012 | 64 | bad_idk | "Dữ liệu nhập có bị lưu?" | Đề cập privacy nhưng vẫn 'không có thông tin' |
| q034 | 70 | other/DATA | "CVE liên quan CWE-918?" | Ngập CVE-2026-* 10.0 tổng hợp + sai năm |
| q036 | 71 | hallucination | "Struts2 CVE nổi bật?" | CVE-2006-1547 bịa 'Beanutils RCE' (thực là DoS) |
| q035 | 80 | other/misclass | "Fortinet SSL VPN CVE?" | CVE-2024-47571 FortiManager lọt vào; CVE-2020-12812 CVSS vống |
| q047 | 88 | none | "Hôm nay trời mưa không?" | Từ chối đúng nhưng cụt, thiếu gợi ý ATTT |

## 9. Đề xuất cải thiện (ưu tiên theo tác động)

1. **[RETRIEVAL] Vá triệt để lỗ hổng PoC/patch treo→RỖNG (đòn bẩy #1).** q022/q025/q028 (3 câu 23đ) và tiền lệ q026/q045/q046 cho thấy khi poc_search/qdrant treo (9-50s) thì trả chuỗi rỗng. Fix R2 chỉ vá 1 câu rồi lỗi nhảy sang câu khác (q026→q028). Cần: timeout ngắn + **fallback tĩnh** sang mô tả KB/remediation khi retrieval trống, áp cho MỌI câu chứ không patch từng CVE.
2. **[PROMPT] Cấm tuyệt đối câu trả lời RỖNG (guard ở tầng compose).** Nếu nội dung thực chất trống sau retrieval → BẮT BUỘC (a) hỏi làm rõ, hoặc (b) nêu 'chưa truy vấn được' + gợi ý. Chặn cứng q022/q025/q028/q045/q046. Đây là guard tổng quát, không phụ thuộc retrieval.
3. **[PROMPT] Nạp "known facts" cho meta/data_coverage/model-identity.** KB cập nhật **hàng đêm**, dữ liệu CVE **~1999→nay**, câu model AI/privacy trả lời trực tiếp (không chặn). Sửa q006/q008/q011/q012 — 4 câu customer đang 32-64đ, đòn bẩy lớn nhất phía customer (đã 2 vòng chưa đụng tới).
4. **[PROMPT] vague_messy → hỏi làm rõ (đang REGRESSED).** "cve"/"lỗ hổng" (q045/q046) nay còn tệ hơn R2 (RỖNG thuần). Route bắt buộc hỏi "Bạn muốn tra CVE/lỗ hổng nào cụ thể?". Rẻ, dễ, +~50đ/câu.
5. **[RAG] aggregate 'cao nhất/nhiều nhất' → truy vấn sort thực hoặc trả thận trọng.** q039 kết luận '9.8 cao nhất' mâu thuẫn KB (có 10.0). Chuẩn hóa theo q037 ('chưa có tool thống kê') hoặc thêm sort-by-cvss thật.
6. **[DATA] Dọn/gắn cờ loạt CVE-2026-* tổng hợp CVSS 10.0.** q034 (kéo cả q038/q039) ngập CVE-2026-xxxxx 10.0 SSRF nghi synthetic → hỏng relation + aggregate. Lọc bỏ hoặc gắn nhãn nguồn trước khi trả người dùng; sửa cả nhãn năm sai (2018-14721 gắn '2019').
7. **[DATA/PROMPT] Sửa hallucination bền q036 CVE-2006-1547.** Bản ghi KB gán 'Commons Beanutils RCE/CAPEC-500' SAI → sự thật **DoS ActionForm** (Struts <1.2.9). Sửa record; đồng thời bổ sung S2-061 **CVE-2020-17530** và CVE-2017-9805 vào nhóm Struts nổi bật.
8. **[RETRIEVAL] Bộ lọc product/feature-relevance cho danh sách theo chủ đề.** q035 để lọt CVE-2024-47571 (FortiManager) vào danh sách "SSL VPN" (persistent R2); thêm kiểm tra liên quan sản phẩm để CVE lệch chủ đề không lẫn vào; kiểm CVSS list-item (CVE-2020-12812 in 9.8 sai, thực ~5.2).
9. **[RETRIEVAL] Vá retrieval mitigation q029 (PAN-OS).** Mitigation công khai (tắt Device Telemetry + Threat ID 95187/95189) tồn tại nhưng báo 'không tìm thấy' — index remediation cho CVE KEV lớn (đã persistent 3 vòng).
10. **[PROMPT] Bổ sung bước then chốt cho remediation KEV.** Khi q028 được vá RỖNG, phải kèm 'kill phiên ICA/PCoIP' cho Citrix Bleed. Nhúng checklist remediation chuẩn (patch + hành động bổ sung) cho các CVE KEV lớn.
11. **[PROMPT] Nhất quán điểm/vector CVSS.** q017 in 9.8 kèm vector A:N (của 9.1). Khi nêu cả điểm+vector phải khớp; nếu 2 nguồn (NVD vs vendor) thì ghi rõ cả hai.
12. **[PROMPT] Chuẩn hóa mẫu từ chối off-topic = 1 câu lịch sự + 1 gợi ý ATTT.** q047 hơi cụt (không có closer/gợi ý như q048). Đồng bộ giọng.

---
### Ghi chú divergence hợp lệ (không trừ điểm)
ProxyLogon NVD 9.8 vs MS 9.1; Citrix Bleed NVD 7.5 vs Citrix 9.4; Fortinet CVE-2018-13379 NVD 9.8 vs 9.1; Heartbleed v3 7.5 vs v2 5.0; EternalBlue NVD 8.8 vs MS 8.1 — bot nêu MỘT giá trị hợp lệ đều được chấp nhận. Zerologon MS-submitted 5.5 là lỗi nhập liệu; đúng là 10.0 (bot dùng 10.0 — đúng). q026 bot khuyến nghị Log4j >=2.17.0 (cao hơn mốc tối thiểu 2.16.0 — an toàn hơn, chấp nhận).
