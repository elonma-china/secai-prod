# Round 5 (FINAL) — Opus web-verify grading summary (50 questions)

Grader: Opus (web-verify). Rubric: 7 tiêu chí → 100/câu. Nguồn kiểm chứng: NVD, CISA KEV/advisory, MITRE/CVE.org, vendor (Fortinet/Palo Alto/Citrix/Apache/JetBrains), Exploit-DB/Rapid7. Ngày chấm: 2026-09-04.

> **R5 thêm 1 data-fix cho CVE-2006-1547 (q036).** KẾT LUẬN HEADLINE: điểm nhích nhẹ **+1.12 → 93.76** nhờ q028 (Citrix Bleed) thoát fallback, NHƯNG **hallucination q036 VẪN CHƯA SỬA** — đáp án R5 vẫn ghi "Commons Beanutils RCE" y hệt R4. Data-fix KHÔNG vào được đáp án này (chưa propagate / answers sinh trước fix).

## 1. Điểm tổng

| Phạm vi | n | mean_total | Δ vs R4 |
|---|---|---|---|
| **Toàn bộ** | 50 | **93.76** | **+1.12** (R4 92.64) |
| customer | 18 | **97.89** | **+0.83** (R4 97.06) |
| secpro | 32 | **91.44** | **+1.28** (R4 90.16) |

> Secpro kéo điểm chính (+1.28) nhờ **q028: 50→82** (fallback → remediation thật). Customer đã bão hòa gần trần (97.9). Không câu nào regress thực chất.

## 2. Mean theo tiêu chí (yếu → mạnh, % tối đa) + Δ vs R4

| Tiêu chí | mean | % max | R4 % | Δpp |
|---|---|---|---|---|
| **accuracy_grounded** | 17.98 / 20 | **89.9%** ← yếu nhất | 87.8% | **+2.1** |
| robustness | 9.00 / 10 | 90.0% | 91.0% | −1.0 |
| tool_grounding | 18.58 / 20 | 92.9% | 92.1% | +0.8 |
| answer_attitude | 23.70 / 25 | 94.8% | 94.1% | +0.7 |
| idk_handling | 9.60 / 10 | 96.0% | 93.4% | +2.6 |
| language_format | 4.90 / 5 | 98.0% | 98.0% | 0 |
| scope_discipline | 10.00 / 10 | 100.0% | 98.0% | +2.0 |

**accuracy_grounded vẫn là điểm yếu nhất (89.9%)** — kéo xuống bởi đúng bộ vấn đề bền: q036 hallucination (chưa sửa), q034/q038 data-synthetic 2026-*, q035 misclass, q022/q025/q029 retrieval-gap, q028 thiếu bước kill-session, q017 vector lệch. robustness giảm nhẹ −1.0 do chấm chặt hơn q047 (cụt) + fallback.

## 3. Mean theo category (thấp → cao) + Δ vs R4

| Category | n | R5 | R4 | ghi chú |
|---|---|---|---|---|
| poc_availability | 4 | 76.0 | 76.0 = | q023/q024=100; q022/q025 fallback (52) — retrieval-gap bền |
| relation | 3 | 78.3 | 77.3 ↑ | q034 data, q035 misclass, q036 hallucination CHƯA sửa |
| remediation | 4 | 82.8 | 74.8 ↑↑ | **q028 50→82**; q026/q027=100; q029 'không tìm thấy' (50) |
| random_offtopic | 2 | 91.0 | 91.0 = | q047 cụt (88), q048 tốt (94) |
| aggregate_stats | 3 | 92.0 | 91.0 ↑ | q037/q039 trung thực tốt; q038 data 2026-* |
| vague_messy | 2 | 96.0 | 93.5 ↑ | q045/q046 hỏi làm rõ có ví dụ |
| edge_unknown | 2 | 96.0 | 97.0 ↓ | CVE giả → 'không tìm thấy' đúng (chấm chặt hơn) |
| meta_self | 2 | 97.0 | 94.5 ↑ | q011 không né oan, q012 canonical |
| greeting_chitchat | 2 | 97.0 | 94.5 ↑ | q049/q050 thân thiện |
| cve_detail | 5 | 98.0 | 97.8 = | q017 vector lệch, q020 hơi cụt |
| cve_lookup | 4 | 99.5 | 98.8 = | q014 depth nhẹ |
| system_overview | 3 | 100.0 | 100.0 = | |
| capability | 2 | 100.0 | 100.0 = | |
| data_coverage | 3 | 100.0 | 100.0 = | canonical |
| usage | 2 | 100.0 | 100.0 = | |
| cwe_capec | 4 | 100.0 | 100.0 = | |
| concept | 3 | 100.0 | 99.7 = | |

## 4. Bảng đếm failure_bucket

| bucket | count | id |
|---|---|---|
| none | 43 | (các câu đạt) |
| no_grounding | 3 | q022, q025, q029 |
| other (DATA/misclass) | 3 | q034, q035, q038 |
| hallucination | 1 | q036 |

**So R4:** no_grounding 4→3 (**q028 thoát nhóm** nhờ có nội dung remediation); none 42→43; other 3=3 (q034/q035/q038); **hallucination 1→1 (q036 BỀN, chưa sửa).**

## 5. VẤN ĐỀ RỖNG — vẫn 0

**Đếm câu RỖNG (chỉ closer, không nội dung): 0** (giữ nguyên R4). q022/q025 là fallback lịch sự; q029/q043/q044 là 'không tìm thấy' trung thực — đều CÓ nội dung.

## 6. FACT-CHECK (web-verified)

**Web-verify thực hiện R5: ~20 lượt** (NVD/MITRE/CVE-details/Rapid7/CISA/vendor/Exploit-DB) + kế thừa xác minh R4 cho đáp án không đổi.
**Tổng hallucination BẮT QUA WEB (fact TRÁI nguồn thẩm quyền): 1 — q036 (CVE-2006-1547), CHƯA sửa.** Ngoài ra: 1 misclass (q035), 1 data + sai năm (q034), 1 data (q038), 3 retrieval-gap contradicted (q022/q025/q029), 1 partial-miss (q028 thiếu kill-session), 1 vector-lệch (q017).

| id | claim của bot | Sự thật (nguồn) | Loại |
|---|---|---|---|
| **q036** | CVE-2006-1547 = **"Commons Beanutils RCE" / thực thi mã từ xa / CAPEC-500**, HIGH 7.5 | **DoS** trong Struts ActionForm `getMultipartRequestHandler` (Struts <1.2.9); CWE-400/749; KHÔNG phải Beanutils RCE. https://www.cvedetails.com/cve/CVE-2006-1547/ | **hallucination (CHƯA sửa)** |
| q035 | CVE-2024-47571 nằm trong "Fortinet SSL VPN", CWE-672 | **FortiManager** relative path-traversal **CWE-23**, KHÔNG thuộc SSL VPN. https://www.cvedetails.com/cve/CVE-2024-47571/ | misclass |
| q034 | CVE-2018-14721 dưới "Năm 2019"; 16× CVE-2026-* đều 10.0 SSRF | CVE-2018-14721 SSRF **10.0 nhưng năm 2018** (jackson-databind); loạt 2026-* 10.0 là synthetic. https://nvd.nist.gov/vuln/detail/CVE-2018-14721 | data |
| q038 | "CVE mới nhất = CVE-2026-22017/34308/34303" | Cũ nhất CVE-2007-0061 (VMware 2007, 10.0) **đúng**; 'mới nhất' 2026-* là synthetic KB. https://nvd.nist.gov/vuln/detail/CVE-2007-0061 | data |
| q028 | Remediation Citrix Bleed (vá+giám sát+ACL), KHÔNG nêu kill-session | Khuyến cáo chính thức = vá **+ kill aaa/ica/pcoip session** (token sống sót sau vá). https://www.cisa.gov/guidance-addressing-citrix-netscaler-adc-and-gateway-vulnerability-cve-2023-4966-citrix-bleed | partial-miss |
| q029 | "Không tìm thấy mitigation PAN-OS" | Mitigation **CÓ**: tắt Device Telemetry + Threat ID 95187/95189/95191. https://security.paloaltonetworks.com/CVE-2024-3400 | no_grounding |
| q022 | (fallback, không nêu PoC) | PoC Log4Shell **CÓ** rất nhiều. https://github.com/kozmer/log4j-shell-poc | no_grounding |
| q025 | (fallback, không nêu script) | Python PoC Zerologon **CÓ** (SecuraBV/Impacket). https://github.com/SecuraBV/CVE-2020-1472 | no_grounding |
| q017 | 9.8 kèm vector `...A:N` | NVD 9.8 dùng **A:H**; A:N là vector điểm MS 9.1 — số đúng, vector lệch. https://nvd.nist.gov/vuln/detail/CVE-2021-26855 | minor |

**Fact ĐÚNG đã xác minh (verdict ok):** q013 Log4Shell 10.0/CWE-502-917 · q014 Zerologon 10.0/Netlogon · q015 EternalBlue SMBv1/8.8 · q016 MOVEit SQLi CWE-89/9.8 · q017 ProxyLogon 9.8 (số) · q018 Citrix Bleed CWE-119 (Citrix) · q019 PAN-OS 10.0/CWE-77 · q020 CVE-2018-13379 FortiOS/FortiProxy SSL-VPN · q021 Heartbleed v3 7.5 (C:H/I:N/A:N)/CWE-125 · q023 MSF struts2_content_type_ognl/CWE-755/9.8 · q024 TeamCity 9.8/CWE-288 + PoC/MSF/EDB · q026 Log4j ≥2.16/2.17 remediation · q027 MS17-010 + tắt SMBv1 · q034 anchor CVE-2018-14721/CVE-2017-11291 SSRF thật · q036 (phần đúng) CVE-2016-3087 DMI RCE 9.8 + CVE-2018-11776 OGNL 8.1 · q038 oldest CVE-2007-0061 · q039 nhiều CVE 10.0 thật · q043/q044 CVE giả → 'không tìm thấy' đúng.

## 7. COMPARISON vs R4

| id | R4 | R5 | trạng thái |
|---|---|---|---|
| **q028** | 50 | **82** | ✅ **IMPROVED** — fallback → remediation thật (nhưng thiếu kill-session) |
| **q036** | 76 | **76** | ❌ **CHƯA SỬA** — vẫn 'Commons Beanutils RCE' (thực là DoS); data-fix không vào đáp án |
| q022 | 52 | 52 | = (fallback, retrieval-gap) |
| q025 | 52 | 52 | = (fallback, retrieval-gap) |
| q029 | 50 | 50 | = ('không tìm thấy', mitigation công khai có) |
| q034 | 72 | 74 | ~ (data 2026-* + sai năm — bền) |
| q035 | 84 | 85 | ~ (misclass CVE-2024-47571 — bền) |
| q038 | 84 | 84 | = (newest synthetic; oldest đúng) |
| q017 | ~95 | 95 | = (vector A:N lệch) |
| q011/q012/q037/q039/q045/q046 | cao | cao | = ổn định |

- **Δ mean_total: +1.12** (92.64 → 93.76). Nguồn tăng DUY NHẤT có ý nghĩa: **q028 +32**.
- **q036 hallucination: CHƯA fixed.** Đáp án R5 nguyên văn: "CVE-2006-1547 – Commons Beanutils RCE … cho phép thực thi mã từ xa thông qua kỹ thuật CAPEC-500". Đây là hallucination web-bắt DUY NHẤT, bền qua **cả 5 vòng**. Data-fix của round này chưa propagate tới câu trả lời (KB đã sửa nhưng retrieval/serving trả bản cũ, hoặc answers sinh trước khi fix chạy).
- **Regressions: KHÔNG có regression thực chất.** edge_unknown/robustness giảm nhẹ chỉ do chấm chặt hơn (q047 cụt), không phải chất lượng tụt.

## 8. 15 case tệ nhất

| id | total | bucket | q (rút gọn) | vấn đề |
|---|---|---|---|---|
| q029 | 50 | no_grounding | "Mitigation PAN-OS trước vá?" | 'Không tìm thấy' dù công khai có (tắt Device Telemetry + Threat ID) |
| q022 | 52 | no_grounding | "Log4Shell có PoC GitHub?" | Fallback; thực tế hàng trăm PoC |
| q025 | 52 | no_grounding | "Zerologon có script Python?" | Fallback (50s); có zerologon_tester.py |
| q034 | 74 | other/DATA | "CVE liên quan CWE-918?" | Ngập 16 CVE-2026-* 10.0 synthetic + sai nhãn năm (2018→'2019') |
| q036 | 76 | hallucination | "Struts2 CVE nổi bật?" | **CVE-2006-1547 vẫn 'Beanutils RCE' (thực là DoS) — CHƯA sửa** |
| q028 | 82 | none | "Xử lý Citrix Bleed?" | Có remediation nhưng THIẾU kill phiên ICA/PCoIP (bước then chốt) |
| q038 | 84 | other/DATA | "CVE mới/cũ nhất?" | 'Mới nhất' = CVE-2026-* synthetic (oldest đúng) |
| q035 | 85 | other/misclass | "Fortinet SSL VPN CVE?" | CVE-2024-47571 (FortiManager, CWE-23) lọt vào danh sách SSL VPN |
| q047 | 88 | none | "Hôm nay trời mưa?" | Từ chối đúng nhưng cụt, thiếu closer/gợi ý ATTT |
| q048 | 94 | none | "Công thức phở?" | Từ chối lịch sự + closer — tốt |
| q050 | 94 | none | "Cảm ơn" | Thân thiện nhưng hơi generic; latency 45.9s |
| q017 | 95 | none | "CVSS ProxyLogon?" | 9.8 (NVD) đúng nhưng kèm vector A:N (của 9.1) |
| q011 | 96 | none | "Bạn chạy model AI nào?" | Không công bố model (hợp lý) nhưng hơi né; latency 46s |
| q020 | 96 | none | "CVE-2018-13379 sản phẩm nào?" | Đúng FortiOS/FortiProxy nhưng cụt (thiếu SSL-VPN/CWE) |
| q037 | 96 | none | "Đếm CVE CRITICAL 2024?" | Trung thực 'chưa có tool' — đúng chuẩn |

## 9. Đề xuất cải thiện cuối (ưu tiên theo tác động) — HANDOVER

1. **[DATA] Đẩy lại data-fix q036 CVE-2006-1547 tới serving/retrieval — CHƯA có tác dụng.** KB record đã sửa nhưng đáp án R5 vẫn trả bản cũ ('Beanutils RCE'). Cần: re-embed/re-ingest bản ghi đã sửa vào Qdrant + xóa cache, verify bằng câu q036 trực tiếp. Đây là hallucination BỀN DUY NHẤT còn lại (5/5 vòng).
2. **[RETRIEVAL] Vá lỗ hổng PoC/patch treo→fallback (đòn bẩy #1).** q022/q025 (fallback 52) + q029 ('không tìm thấy' 50) đều có nội dung công khai. Cần timeout ngắn + **fallback tĩnh sang mô tả PoC/remediation KB** thay vì 'hỏi lại'/'không tìm thấy'. Tiềm năng ~+130đ secpro (3 câu × ~45đ).
3. **[DATA/RAG] Lọc/gắn cờ loạt CVE-2026-* synthetic CVSS 10.0.** q034 (16 entries) + q038 ('mới nhất') phơi CVE giả tương lai. Lọc năm > hôm nay HOẶC gắn nhãn 'chưa xác minh' trước khi trả; sửa nhãn năm sai (CVE-2018-14721 gắn '2019').
4. **[PROMPT] Checklist remediation KEV bắt buộc bước bổ sung.** q028 phải kèm **kill aaa/ica/pcoip session**; q029 (khi vá được retrieval) phải kèm **tắt Device Telemetry + Threat ID 95187/95189/95191**. Nhúng template 'patch + hành động bổ sung' cho CVE KEV lớn.
5. **[RETRIEVAL] Bộ lọc product/feature-relevance cho danh sách theo chủ đề.** q035 vẫn để lọt CVE-2024-47571 (FortiManager) vào "SSL VPN"; thêm kiểm tra sản phẩm/feature.
6. **[PROMPT] Nhất quán điểm+vector CVSS.** q017 in 9.8 kèm vector A:N (của 9.1). Khi nêu cả điểm lẫn vector phải khớp; nếu 2 nguồn (NVD 9.8 vs MS 9.1) thì ghi rõ cả hai + vector tương ứng.
7. **[PROMPT] Chuẩn hóa mẫu từ chối off-topic = 1 câu lịch sự + 1 gợi ý ATTT.** q047 ('trời mưa') cụt, thiếu closer như q048 — đồng bộ giọng, rẻ, +vài đ.
8. **[PROMPT] Giữ độ sâu root-cause + CWE cho CVE đặc thù.** q014 Zerologon nên nêu gốc mật mã (IV=0 AES-CFB8); q020 nên kèm SSL-VPN web portal + CWE-22 thay vì chỉ tên sản phẩm.
9. **[PERF/RETRIEVAL] Điều tra latency lẻ tẻ cao (46-50s).** q019 48.7s, q015 47s, q031 55s, q025 50s, q011/q046/q050 ~46s — đi kèm nguy cơ retrieval treo. Đặt timeout + fallback tĩnh (liên quan #2).
10. **[DATA] Rà soát toàn bộ record KB có nhãn 'RCE/CAPEC-500' áp sai cho CVE DoS/khác.** q036 cho thấy pattern gán nhầm CAPEC/loại tấn công — quét các record tương tự để tránh hallucination lớp CWE/CAPEC.

---
### Ghi chú divergence hợp lệ (không trừ điểm)
ProxyLogon NVD 9.8 vs MS 9.1; Citrix Bleed NVD 7.5 vs Citrix 9.4 (CWE-119); Fortinet CVE-2018-13379 NVD 9.8 vs 9.1; Heartbleed v3 7.5 vs v2 5.0; EternalBlue NVD 8.8 vs MS 8.1; CVE-2020-12812 9.8 vs 5.2; CVE-2018-11776 NVD 8.1 vs Apache 9.8 — bot nêu MỘT giá trị hợp lệ đều được chấp nhận. Zerologon 10.0 đúng. q026 khuyến nghị Log4j ≥2.17 (cao hơn mốc 2.16 — an toàn hơn, chấp nhận). CVE-2006-1547 CVSS 7.5 (NVD v3 rescored A:H = DoS) trùng số bot nêu, NHƯNG bot mô tả là RCE → vẫn hallucination về BẢN CHẤT.
