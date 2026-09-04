# Round 4 — Opus web-verify grading summary (50 questions)

Grader: Opus (web-verify). Rubric: 7 tiêu chí → 100/câu. Nguồn kiểm chứng: NVD, CISA KEV/advisory, MITRE, vendor (Fortinet/Palo Alto/Citrix/Apache/JetBrains), Exploit-DB. Ngày chấm: 2026-09-04.

> **R4 là vòng ĐẦU TIÊN có fix thực sự chạy** (các vòng trước patch nhầm path → vô hiệu). Fix R4 = value-level guard chặn rỗng/meta/aggregate + cải thiện prompt. Kết quả: **bước nhảy lớn nhất từ trước tới nay.**

## 1. Điểm tổng

| Phạm vi | n | mean_total | Δ vs R3 | Δ vs R1 |
|---|---|---|---|---|
| **Toàn bộ** | 50 | **92.64** | **+8.50** (R3 84.14) | **+11.72** (R1 80.92) |
| customer | 18 | **97.06** | **+16.23** (R3 80.83) | **+22.84** (R1 74.22) |
| secpro | 32 | **90.16** | **+4.16** (R3 86.00) | **+5.47** (R1 84.69) |

> **Customer bứt phá** (+16.2): toàn bộ nhóm meta/data_coverage/usage/vague/model-identity — vốn là nút thắt 3 vòng — nay được trả canonical trực tiếp. Customer từ điểm THẤP nhất (R1 74) vọt lên CAO hơn secpro. Secpro cải thiện vừa (+4.2): 3 câu PoC/remediation RỖNG (q022/q025/q028) nay có fallback lịch sự (≈50đ thay vì 23), q023/q024/q039 sạch, nhưng lỗ retrieval + data tổng hợp vẫn kéo secpro.

## 2. Mean theo tiêu chí (yếu → mạnh, % tối đa) + Δ vs R3

| Tiêu chí | mean | % max | R3 % | Δpp |
|---|---|---|---|---|
| **accuracy_grounded** | 17.56 / 20 | **87.8%** ← yếu nhất | 78.9% | **+8.9** |
| robustness | 9.10 / 10 | 91.0% | 88.4% | +2.6 |
| tool_grounding | 18.42 / 20 | 92.1% | 82.9% | **+9.2** |
| idk_handling | 9.34 / 10 | 93.4% | 83.6% | **+9.8** |
| answer_attitude | 23.52 / 25 | 94.1% | 83.3% | **+10.8** |
| scope_discipline | 9.80 / 10 | 98.0% | 90.8% | +7.2 |
| language_format | 4.90 / 5 | 98.0% | 93.6% | +4.4 |

**Mọi tiêu chí đều tăng.** answer_attitude (+10.8pp) và idk_handling (+9.8pp) nhảy mạnh nhất — trực tiếp phản ánh guard chặn rỗng + canonical meta. accuracy_grounded vẫn là điểm yếu nhất (87.8%) nhưng đã +8.9pp, kéo xuống bởi: 3 câu retrieval-gap fallback (q022/q025/q028), q029 'không tìm thấy', 1 hallucination bền (q036), 2 data tổng hợp (q034/q038), q035 misclass, q017 vector lệch.

## 3. Mean theo category (thấp → cao)

| Category | n | mean | R3 | ghi chú |
|---|---|---|---|---|
| remediation | 4 | 74.8 | 66.5 ↑ | q026/q027 hoàn hảo; q028 fallback (50), q029 'không tìm thấy' (50) |
| poc_availability | 4 | 76.0 | 61.5 ↑ | q023/q024 = 100; q022/q025 fallback (52) |
| relation | 3 | 77.3 | 73.7 ↑ | q034 data, q035 misclass, q036 hallucination bền |
| random_offtopic | 2 | 91.0 | 92.0 | q047 cụt (88), q048 tốt |
| aggregate_stats | 3 | 91.0 | 77.7 ↑↑ | q039 SỬA (95), q037 tốt, q038 data 2026-* |
| vague_messy | 2 | 93.5 | 36.0 ↑↑↑ | q045/q046 nay hỏi làm rõ (từ RỖNG) |
| meta_self | 2 | 94.5 | 48.0 ↑↑↑ | q011 hết từ chối oan, q012 canonical privacy |
| greeting_chitchat | 2 | 94.5 | 97.0 | ổn |
| edge_unknown | 2 | 97.0 | 97.0 | CVE giả → 'không tìm thấy' đúng |
| cve_detail | 5 | 97.8 | 98.8 | q017 vector lệch, q020 hơi cụt |
| cve_lookup | 4 | 98.8 | 99.5 | q013/q014 depth nhẹ |
| concept | 3 | 99.7 | 100.0 | |
| data_coverage | 3 | 100.0 | 70.0 ↑↑↑ | q006/q008 nay canonical |
| system_overview | 3 | 100.0 | 100.0 | |
| cwe_capec | 4 | 100.0 | 100.0 | |
| usage | 2 | 100.0 | 100.0 | q010 nay có câu mẫu |
| capability | 2 | 100.0 | 99.5 | |

## 4. Bảng đếm failure_bucket

| bucket | count | id |
|---|---|---|
| none | 42 | (các câu đạt) |
| no_grounding | 4 | q022, q025, q028, q029 |
| other (DATA/misclass) | 3 | q034, q035, q038 |
| hallucination | 1 | q036 |

**So R3:** no_grounding 5→4 (q039 thoát nhóm nhờ fix); bad_idk 3→0 (q006/q008/q012 canonical); wrong_refusal 1→0 (q011 hết); not_robust 2→0 (q045/q046 hỏi làm rõ); hallucination 1→1 (q036 bền); other 2→3 (thêm q038 data 2026-*).

## 5. VẤN ĐỀ RỖNG (câu hỏi trọng tâm của loop) — GIẢI QUYẾT

**Đếm câu RỖNG (chỉ closer, không nội dung): 0** — giảm từ **5 ở R3** (q022, q025, q028, q045, q046).

- q045/q046 (vague): RỖNG → **hỏi làm rõ có ví dụ** (94/93đ).
- q022/q025/q028 (PoC/remediation): RỖNG → **fallback lịch sự** 'chưa tổng hợp được, nêu cụ thể hơn' (50-52đ). Guard chặn rỗng HOẠT ĐỘNG: không còn chuỗi rỗng, nhưng **KHÔNG vá được lỗ retrieval gốc** — 3 câu này lẽ ra trả được (PoC/patch công khai có). Fallback = thắng thái độ, chưa thắng nội dung.

## 6. FACT-CHECK (web-verified)

**Web-verify thực hiện: ~18 lượt** (NVD/MITRE/Exploit-DB/vendor/GitHub advisory).
**Tổng hallucination BẮT QUA WEB (fact TRÁI nguồn thẩm quyền): 1** — q036 (CVE-2006-1547). Ngoài ra: 1 misclass (q035 CVE-2024-47571), 4 retrieval-gap contradicted (q022/q025/q028/q029 — nội dung công khai có nhưng bot không trả), 2 data tổng hợp (q034/q038 CVE-2026-*).

| id | claim của bot | Sự thật (nguồn) | Loại |
|---|---|---|---|
| q036 | CVE-2006-1547 = "Commons Beanutils RCE (CAPEC-500)", HIGH | **DoS** trong ActionForm (getMultipartRequestHandler), Struts <1.2.9 — KHÔNG phải Beanutils RCE. https://www.cvedetails.com/cve/CVE-2006-1547/ | hallucination |
| q035 | CVE-2024-47571 nằm trong "Fortinet SSL VPN" | **FortiManager** path-traversal (CWE-23), KHÔNG thuộc SSL VPN. https://www.sentinelone.com/vulnerability-database/cve-2024-47571/ | misclass |
| q034 | 16+ `CVE-2026-*` đều 10.0 SSRF; CVE-2018-14721 dưới "Năm 2019" | Neo THẬT (CVE-2018-14721 SSRF **10.0**; CVE-2017-11291 Adobe Connect SSRF), nhưng loạt 2026-* 10.0 là **data tổng hợp** + gắn sai năm (2018≠2019). https://vulert.com/vuln-db/CVE-2018-14721 | data |
| q038 | "CVE mới nhất = CVE-2026-22017/34308/34303 (2026)" | Cũ nhất CVE-2007-0061 (VMware 2007, 10.0) **đúng**; 'mới nhất' 2026-* là data KB tổng hợp (có caveat). https://nvd.nist.gov/vuln/detail/CVE-2007-0061 | data |
| q017 | điểm 9.8 kèm vector `...A:N` | NVD 9.8 dùng **A:H**; A:N là điểm MS 9.1 — số đúng, vector lệch. https://nvd.nist.gov/vuln/detail/CVE-2021-26855 | minor |
| q022 | (fallback, không nêu PoC) | PoC Log4Shell **CÓ** rất nhiều. https://github.com/kozmer/log4j-shell-poc | no_grounding |
| q025 | (fallback, không nêu script) | Python PoC Zerologon **CÓ** (SecuraBV, Impacket). https://github.com/SecuraBV/CVE-2020-1472 | no_grounding |
| q028 | (fallback, không nêu cách xử lý) | Remediation Citrix Bleed **CÓ**: vá + **kill phiên ICA/PCoIP**. https://support.citrix.com/article/CTX579459 | no_grounding |
| q029 | "Không tìm thấy mitigation PAN-OS" | Mitigation **CÓ**: tắt Device Telemetry + Threat ID 95187/95189. https://security.paloaltonetworks.com/CVE-2024-3400 | no_grounding |

**Fact ĐÚNG đã xác minh (verdict ok):** q013 Log4Shell 10.0/CWE-502-917 · q014 Zerologon 10.0/Netlogon · q015 EternalBlue SMBv1 8.8 · q016 MOVEit SQLi CWE-89 9.8 · q017 ProxyLogon 9.8 (số) · q018 Citrix Bleed CWE-119 (Citrix) · q019 PAN-OS 10.0 · q020 CVE-2018-13379 FortiOS/FortiProxy · q021 Heartbleed v3 7.5 · q023 MSF struts2_content_type_ognl/CWE-755 · q024 **TeamCity MSF + EDB 52411 + 9.8** (đủ, không bịa) · q034 anchor CVE-2018-14721/CVE-2017-11291 SSRF thật · q035 CVE-2020-12812 là SSL VPN 2FA-bypass thật (9.8/5.2 tùy nguồn — chấp nhận) · q036 CVE-2016-3087 DMI RCE 9.8 + CVE-2018-11776 OGNL 8.1 thật · q038 oldest CVE-2007-0061 VMware 2007 10.0 · q043/q044 CVE giả → 'không tìm thấy' đúng.

## 7. So sánh 10 câu MỤC TIÊU của fix R4

| id | R3 | R4 | trạng thái |
|---|---|---|---|
| q006 | 55 | **100** | ✅ IMPROVED — canonical ~1999→nay + nightly |
| q008 | 55 | **100** | ✅ IMPROVED — canonical khoảng năm + nightly |
| q011 | 32 | **93** | ✅ IMPROVED — hết từ chối oan, nêu 'không công bố model' + mời hỏi |
| q012 | 64 | **96** | ✅ IMPROVED — canonical privacy |
| q039 | 55 | **95** | ✅ IMPROVED — trung thực 'chưa có tool xếp hạng' + đúng 10.0 |
| q045 | 36 | **94** | ✅ IMPROVED — hỏi làm rõ (từ RỖNG) |
| q046 | 36 | **93** | ✅ IMPROVED — hỏi làm rõ (từ RỖNG) |
| q022 | 23 | **52** | ⚠️ PARTIAL — fallback lịch sự thay rỗng, vẫn no_grounding |
| q025 | 23 | **52** | ⚠️ PARTIAL — fallback thay rỗng, vẫn no_grounding |
| q028 | 23 | **50** | ⚠️ PARTIAL — fallback thay rỗng, vẫn no_grounding |

**7/10 mục tiêu cải thiện HOÀN TOÀN; 3/10 cải thiện MỘT PHẦN (thái độ, chưa nội dung).** Không câu nào regressed. So R2→R3 (chỉ 1/9 cải thiện, 2 regressed) đây là **bước nhảy chất lượng thật đầu tiên** — khớp với việc fix nay chạy đúng path.

## 8. 15 case tệ nhất

| id | total | bucket | q (rút gọn) | vấn đề |
|---|---|---|---|---|
| q028 | 50 | no_grounding | "Xử lý Citrix Bleed?" | Fallback lịch sự; thiếu vá + kill phiên ICA/PCoIP |
| q029 | 50 | no_grounding | "Mitigation PAN-OS trước vá?" | 'Không tìm thấy' dù công khai có (tắt Device Telemetry) |
| q022 | 52 | no_grounding | "Log4Shell có PoC GitHub?" | Fallback; thực tế hàng trăm PoC |
| q025 | 52 | no_grounding | "Zerologon có script Python?" | Fallback (50.8s); có zerologon_tester.py |
| q034 | 72 | other/DATA | "CVE liên quan CWE-918?" | Ngập 16 CVE-2026-* 10.0 tổng hợp + sai nhãn năm |
| q036 | 76 | hallucination | "Struts2 CVE nổi bật?" | CVE-2006-1547 bịa 'Beanutils RCE' (thực là DoS) |
| q035 | 84 | other/misclass | "Fortinet SSL VPN CVE?" | CVE-2024-47571 (FortiManager) lọt vào danh sách SSL VPN |
| q038 | 84 | other/DATA | "CVE mới/cũ nhất?" | 'Mới nhất' là CVE-2026-* tổng hợp (oldest đúng) |
| q047 | 88 | none | "Hôm nay trời mưa?" | Từ chối đúng nhưng cụt, thiếu gợi ý ATTT |
| q011 | 93 | none | "Bạn chạy model AI nào?" | Không công bố model (hợp lý) nhưng hơi né |
| q046 | 93 | none | "lỗ hổng" | Hỏi làm rõ đúng (latency 45.8s) |
| q037 | 94 | none | "Đếm CVE CRITICAL 2024?" | Trung thực 'chưa có tool' — đúng chuẩn |
| q045 | 94 | none | "cve" | Hỏi làm rõ đúng |
| q048 | 94 | none | "Công thức phở?" | Từ chối lịch sự + closer — tốt |
| q050 | 94 | none | "Cảm ơn" | Đáp thân thiện (latency 45.8s) |

## 9. Đề xuất cải thiện (ưu tiên theo tác động)

1. **[RETRIEVAL] Vá lỗ hổng PoC/patch treo→fallback (đòn bẩy #1 còn lại).** q022/q025/q028 (≈50đ) và q029 ('không tìm thấy') đều có nội dung công khai nhưng poc_search/qdrant trả trống → guard chỉ chuyển thành fallback lịch sự. Cần: timeout ngắn + **fallback tĩnh sang mô tả KB/remediation** thay vì fallback 'hỏi lại'. Đây là ~180đ tiềm năng cho secpro (4 câu × ~45đ).
2. **[DATA] Dọn/gắn cờ loạt CVE-2026-* tổng hợp CVSS 10.0.** q034 (16 entries) + q038 ('mới nhất') + tiềm ẩn q039 đều nhiễm CVE-2026-xxxxx 10.0 synthetic. Lọc bỏ hoặc gắn nhãn nguồn trước khi trả người dùng; sửa nhãn năm sai (CVE-2018-14721 gắn '2019'). Là nguồn accuracy loss lớn nhất còn lại của relation/aggregate.
3. **[DATA/PROMPT] Sửa hallucination bền q036 CVE-2006-1547.** Bản ghi KB gán 'Commons Beanutils RCE/CAPEC-500' SAI → thực là **DoS ActionForm** (Struts <1.2.9). Sửa record ở nguồn; đây là hallucination web-bắt DUY NHẤT còn lại, bền qua 4 vòng.
4. **[RETRIEVAL] Bộ lọc product/feature-relevance cho danh sách theo chủ đề.** q035 vẫn để lọt CVE-2024-47571 (FortiManager) vào danh sách "SSL VPN". Thêm kiểm tra sản phẩm/feature để CVE lệch chủ đề không lẫn vào danh sách.
5. **[PROMPT] Bổ sung bước then chốt cho remediation KEV.** Khi q028 (Citrix Bleed) được vá nội dung, PHẢI kèm 'kill phiên ICA/PCoIP'; q029 (PAN-OS) kèm 'tắt Device Telemetry + Threat ID 95187/95189'. Nhúng checklist remediation chuẩn (patch + hành động bổ sung) cho CVE KEV lớn.
6. **[PROMPT] Chuẩn hóa mẫu từ chối off-topic = 1 câu lịch sự + 1 gợi ý ATTT.** q047 ('trời mưa') cụt, thiếu closer/gợi ý như q048. Đồng bộ giọng — rẻ, +vài đ.
7. **[PROMPT] Nhất quán điểm/vector CVSS.** q017 in 9.8 kèm vector A:N (của 9.1). Khi nêu cả điểm+vector phải khớp; nếu 2 nguồn (NVD 9.8 vs MS 9.1) thì ghi rõ cả hai + vector tương ứng.
8. **[PROMPT] Model-identity: canonical hơn nữa.** q011 nay không từ chối (tốt) nhưng vẫn hơi né 'không được công bố'. Có thể chuẩn hóa 1 câu canonical thân thiện ('IAS.AI là trợ lý ATTT chuyên dụng; chi tiết hạ tầng không công bố vì lý do an toàn — tôi tập trung giúp bạn về CVE/CWE/pentest').
9. **[RAG] aggregate 'mới nhất/cũ nhất' lọc CVE synthetic.** q038 nên bỏ CVE-2026-* tổng hợp khỏi kết quả 'mới nhất' hoặc gắn nhãn rõ; oldest đã đúng.
10. **[PROMPT] Giữ độ sâu root-cause cho CVE đặc thù.** q014 Zerologon nên nêu gốc mật mã (IV=0 AES-CFB8) thay vì chỉ 'auth bypass'; q020 nên kèm version/CWE ngắn thay vì chỉ tên sản phẩm.
11. **[PERF] Điều tra latency dài lẻ tẻ.** q019 49s, q015 47s, q031 56s, q017 35s, q046/q050 45.8s — dù đa số trả đúng, latency cao đi kèm nguy cơ retrieval treo (từng gây rỗng ở R3). Đặt timeout + fallback tĩnh.
12. **[PROMPT] Chuẩn hóa closer.** Câu 'Bạn cần IAS.AI hỗ trợ thêm gì nữa không?' xuất hiện cả sau lời chào/cảm ơn (q049/q050) — hợp lý nhưng có thể biến tấu nhẹ để tự nhiên hơn.

---
### Ghi chú divergence hợp lệ (không trừ điểm)
ProxyLogon NVD 9.8 vs MS 9.1; Citrix Bleed NVD 7.5 vs Citrix 9.4 (CWE-119); Fortinet CVE-2018-13379 NVD 9.8 vs 9.1; Heartbleed v3 7.5 vs v2 5.0; EternalBlue NVD 8.8 vs MS 8.1; CVE-2020-12812 9.8 (một số nguồn) vs 5.2 — bot nêu MỘT giá trị hợp lệ đều được chấp nhận. Zerologon 10.0 đúng (MS-submitted 5.5 là lỗi nhập liệu). q026 khuyến nghị Log4j >=2.17.0 (cao hơn mốc 2.16.0 — an toàn hơn, chấp nhận).
