# Round 6 — Opus web-verify grading summary (50 questions)

Grader: Opus (web-verify). Rubric: 7 tiêu chí → 100/câu. Nguồn kiểm chứng: NVD, CISA KEV/advisory, MITRE/CVE.org, cvedetails, vendor (Fortinet/Palo Alto/Citrix/Apache/JetBrains/Rapid7), Exploit-DB. Ngày chấm: 2026-09-04.

> **R6 áp 2 fix nhắm mục tiêu:** (a) DATA-fix + **re-embed** cho CVE-2006-1547 (hallucination q036 bền 5 vòng), (b) **prompt rule** cho câu PoC-availability.
> **HEADLINE: cả 2 fix ĐỀU KHÔNG LANDING.** Điểm gần như đứng yên **+0.52 → 94.28**. **q036 VẪN hallucination** (đáp án nguyên văn 'Commons Beanutils RCE / thực thi mã từ xa / CAPEC-500' — thực tế là DoS). Prompt-PoC **không cứu** q022/q025 (q022 còn tệ hơn nhẹ: fallback → 'không tìm thấy'). Nguồn tăng điểm DUY NHẤT có ý nghĩa là **q035** (+5, do retrieval trả thêm CVE SSL-VPN thật — không phải nhờ 2 fix của round).

## 1. Điểm tổng

| Phạm vi | n | mean_total | Δ vs R5 | Δ vs R4 |
|---|---|---|---|---|
| **Toàn bộ** | 50 | **94.28** | **+0.52** (R5 93.76) | +1.64 (R4 92.64) |
| customer | 18 | **98.44** | **+0.55** (R5 97.89) | +1.38 (R4 97.06) |
| secpro | 32 | **91.94** | **+0.50** (R5 91.44) | +1.78 (R4 90.16) |

> Δ +0.52 nằm trong biên nhiễu chấm. Customer đã bão hòa gần trần (98.4). Secpro nhích nhờ **q035 (85→90)** — do retrieval trả thêm 3 CVE SSL-VPN Fortinet THẬT (CVE-2020-12812, CVE-2018-13382, CVE-2022-42475), KHÔNG do fix của round này. Hai fix nhắm đích (q036 data, PoC prompt) đóng góp ~0.

## 2. Mean theo tiêu chí (yếu → mạnh, % tối đa) + Δ vs R5

| Tiêu chí | mean | % max | R5 % | Δpp |
|---|---|---|---|---|
| **accuracy_grounded** | 18.22 / 20 | **91.1%** ← yếu nhất | 89.9% | +1.2 |
| idk_handling | 9.36 / 10 | 93.6% | 96.0% | −2.4 |
| answer_attitude | 23.64 / 25 | 94.6% | 94.8% | −0.2 |
| robustness | 8.96 / 10 | 89.6% | 90.0% | −0.4 |
| tool_grounding | 19.24 / 20 | 96.2% | 92.9% | +3.3 |
| language_format | 4.88 / 5 | 97.6% | 98.0% | −0.4 |
| scope_discipline | 9.98 / 10 | 99.8% | 100.0% | −0.2 |

**accuracy_grounded vẫn là điểm yếu nhất (91.1%)** — kéo xuống bởi ĐÚNG bộ vấn đề bền qua 6 vòng: q036 hallucination (chưa sửa), q034/q038 synthetic CVE-2026-*, q035 misplacement, q022/q025/q029 retrieval-gap, q028 thiếu kill-session, q017 vector lệch. idk_handling giảm nhẹ (−2.4pp) do **q022 chuyển từ fallback 'mời nêu lại' → 'không tìm thấy' phẳng** (khẳng định sai hơn cho CVE có PoC tràn lan). tool_grounding tăng (+3.3pp) do q023/q024 grounded rất mạnh (module MSF + EDB thật, đã web-verify).

## 3. Mean theo category (thấp → cao) + Δ vs R5

| Category | n | R6 | R5 | ghi chú |
|---|---|---|---|---|
| poc_availability | 4 | 75.5 | 76.0 ↓ | q023/q024=100 (verified thật); q022=50 ('không tìm thấy'), q025=52 (fallback) — prompt-PoC KHÔNG cứu |
| relation | 3 | 80.0 | 78.3 ↑ | q034 synthetic, q035 misplacement (cải thiện), **q036 hallucination CHƯA sửa** |
| remediation | 4 | 83.2 | 82.8 = | q026/q027=100; q028 thiếu kill-session (83); q029 'không tìm thấy' (50) |
| random_offtopic | 2 | 91.5 | 91.0 = | q047 cụt (88), q048 tốt (95) |
| aggregate_stats | 3 | 93.0 | 92.0 ↑ | q037/q039 trung thực; q038 synthetic 2026-* |
| vague_messy | 2 | 97.5 | 96.0 ↑ | q045/q046 hỏi làm rõ có ví dụ |
| edge_unknown | 2 | 98.0 | 96.0 ↑ | CVE giả → 'không tìm thấy' đúng |
| greeting_chitchat | 2 | 98.0 | 97.0 ↑ | q049/q050 thân thiện |
| cve_detail | 5 | 98.6 | 98.0 = | q017 vector lệch (95), q020 cụt (96) |
| meta_self | 2 | 99.0 | 97.0 ↑ | q011 model-identity, q012 privacy canonical |
| cve_lookup | 4 | 99.8 | 99.5 = | q014 depth nhẹ |
| system_overview | 3 | 100.0 | 100.0 = | |
| capability | 2 | 100.0 | 100.0 = | |
| data_coverage | 3 | 100.0 | 100.0 = | canonical |
| usage | 2 | 100.0 | 100.0 = | |
| cwe_capec | 4 | 100.0 | 100.0 = | |
| concept | 3 | 100.0 | 100.0 = | |

## 4. Bảng đếm failure_bucket

| bucket | count | id |
|---|---|---|
| none | 43 | (các câu đạt) |
| no_grounding | 3 | q022, q025, q029 |
| other (DATA/misclass) | 3 | q034, q035, q038 |
| hallucination | 1 | q036 |

**So R5: PHÂN BỐ BUCKET Y HỆT** (none 43, no_grounding 3, other 3, hallucination 1) — CÙNG các id. Đây là bằng chứng mạnh: **các fix của round 6 KHÔNG thay đổi bức tranh lỗi.** q022 đổi câu chữ (fallback → 'không tìm thấy') nhưng vẫn no_grounding.

## 5. VẤN ĐỀ RỖNG — vẫn 0

**Đếm câu RỖNG (chỉ closer, không nội dung): 0.** q022/q029/q043/q044 là 'không tìm thấy' trung thực; q025 là fallback lịch sự — đều có nội dung.

## 6. FACT-CHECK (web-verified)

**Web-verify thực hiện R6: 5 lượt truy vấn mới** (cvedetails/Tenable q036; NVD/Wiz/Rapid7 Fortinet q035×3; Rapid7/EDB q024) + kế thừa xác minh R4/R5 cho các đáp án fact không đổi.
**Tổng hallucination BẮT QUA WEB (fact TRÁI nguồn thẩm quyền): 1 — q036 (CVE-2006-1547), CHƯA sửa (vòng 6).** Ngoài ra: 6 câu contradicted khác thuộc loại DATA/retrieval (không phải bịa-từ-trí-nhớ): q022/q025/q029 (retrieval-gap), q028 (partial-miss), q034/q038 (synthetic KB).

| id | claim của bot | Sự thật (nguồn) | Loại |
|---|---|---|---|
| **q036** | CVE-2006-1547 = **'Commons Beanutils RCE' / thực thi mã từ xa / CAPEC-500**, HIGH 7.5 | **Denial of Service** trong Struts ActionForm `getMultipartRequestHandler` (Struts <1.2.9); KHÔNG phải Beanutils RCE. https://www.cvedetails.com/cve/CVE-2006-1547/ | **hallucination (CHƯA sửa 6/6 vòng)** |
| q022 | 'Không tìm thấy thông tin xác thực trong dữ liệu nội bộ' | PoC Log4Shell công khai **rất nhiều** (log4j-shell-poc…). https://github.com/kozmer/log4j-shell-poc | no_grounding (retrieval) |
| q025 | (fallback 'chưa tổng hợp được…') | Python PoC Zerologon **CÓ** (SecuraBV/Impacket). https://github.com/SecuraBV/CVE-2020-1472 | no_grounding (retrieval) |
| q029 | 'Không tìm thấy… mitigation PAN-OS' | Mitigation **CÓ**: tắt Device Telemetry + Threat ID 95187/95189. https://security.paloaltonetworks.com/CVE-2024-3400 | no_grounding (retrieval) |
| q028 | Remediation Citrix Bleed (vá+giám sát+ACL), KHÔNG nêu kill-session | Khuyến cáo chính thức = vá **+ kill aaa/ica/pcoip session**. https://www.cisa.gov/news-events/cybersecurity-advisories/aa23-325a | partial-miss |
| q034 | CVE-2018-14721 dưới 'Năm 2019'; 16× CVE-2026-* đều 10.0 SSRF | CVE-2018-14721 SSRF 10.0 **năm 2018** (jackson-databind); loạt 2026-* là synthetic KB. https://nvd.nist.gov/vuln/detail/CVE-2018-14721 | data |
| q038 | 'CVE mới nhất' = CVE-2026-22017/34308/34303 | Cũ nhất CVE-2007-0061 (VMware 10.0) **đúng**; 'mới nhất' 2026-* synthetic. https://nvd.nist.gov/vuln/detail/CVE-2007-0061 | data |
| q017 | 9.8 kèm vector `…A:N` | NVD 9.8 dùng **A:H**; A:N là của điểm MS 9.1 — số đúng, vector lệch. https://nvd.nist.gov/vuln/detail/CVE-2021-26855 | minor |

**Fact ĐÚNG đã xác minh MỚI trong R6 (verdict ok):**
- **q035 (cải thiện):** CVE-2020-12812 = FortiOS SSL-VPN 2FA-bypass (improper auth, NVD 9.8, đang bị khai thác/KEV); CVE-2018-13382 = FortiOS SSL-VPN password-mod (7.5, CWE-863); CVE-2022-42475 = FortiOS SSL-VPN heap overflow RCE 9.8 KEV. **CVE-2024-47571 = FortiManager (CWE-672 Operation-on-Resource-after-Expiration), KHÔNG thuộc SSL-VPN** → bị đặt sai chỗ trong danh sách (misplacement, không phải bịa). https://nvd.nist.gov/vuln/detail/CVE-2024-47571
- **q024:** MSF module `exploit/multi/http/jetbrains_teamcity_rce_cve_2024_27198.rb` + Exploit-DB **#52411** + Python `CVE-2024-27198-RCE.py` — **TẤT CẢ có thật**, grounded chính xác. https://www.exploit-db.com/exploits/52411
- **q023:** MSF `struts2_content_type_ognl` + CWE-755 + 9.8 — đúng.

**Fact ĐÚNG kế thừa (verdict ok):** q013 Log4Shell 10.0/CWE-502-917 · q014 Zerologon 10.0/Netlogon · q015 EternalBlue SMBv1 · q016 MOVEit SQLi CWE-89/9.8 · q018 Citrix Bleed CWE-119 (Citrix) · q019 PAN-OS 10.0/CWE-77 · q020 CVE-2018-13379 FortiOS/FortiProxy · q021 Heartbleed v3 7.5/CWE-125 · q026 Log4j ≥2.16/2.17 · q027 MS17-010+tắt SMBv1 · q033 T1190 · q037/q039 trung thực aggregate · q043/q044 CVE giả → 'không tìm thấy' đúng.

## 7. COMPARISON vs R5 — 2 fix có landing không?

### (a) DATA-fix + re-embed CVE-2006-1547 (q036) → **KHÔNG landing (thất bại 6/6 vòng)**
| id | R4 | R5 | R6 | trạng thái |
|---|---|---|---|---|
| **q036** | 76 | 76 | **76** | ❌ **VẪN hallucination** — đáp án R6 nguyên văn: "**CVE-2006-1547 – Commons Beanutils RCE … cho phép thực thi mã từ xa thông qua kỹ thuật CAPEC-500**". Data đã sửa + re-embed nhưng câu trả lời KHÔNG đổi so với R4/R5. |

**Kết luận q036:** re-embed **chưa tác động tới đường trả lời**. Nhiều khả năng: (i) đáp án sinh trước khi re-embed hoàn tất, HOẶC (ii) record cũ vẫn tồn tại song song (không xóa/không ghi đè) và retrieval/reranker vẫn kéo bản cũ 'Beanutils RCE', HOẶC (iii) BM25/lexical vẫn khớp text cũ (mục CLAUDE.md cảnh báo tụt-BM25 khi embed lệch). **Cần verify bằng chính câu q036 SAU khi re-embed + xóa record cũ + xóa cache**, không chỉ kiểm tra KB tĩnh.

### (b) Prompt rule cho PoC-availability (q022/q023/q024/q025) → **KHÔNG cứu 2 câu gãy**
| id | R5 | R6 | trạng thái |
|---|---|---|---|
| q022 (Log4Shell PoC?) | 52 (fallback) | **50** | ⚠️ **REGRESS nhẹ** — chuyển 'mời nêu lại' → 'Không tìm thấy thông tin xác thực trong dữ liệu nội bộ' (khẳng định-sai mạnh hơn cho CVE có hàng trăm PoC). |
| q023 (Struts2 MSF?) | 100 | 100 | = đúng, grounded (đã 100 từ R5) |
| q024 (TeamCity PoC?) | 100 | 100 | = đúng, grounded, chi tiết hơn (verified module+EDB) — nhưng đã 100 từ R5 |
| q025 (Zerologon py?) | 52 (fallback) | 52 | = fallback, chưa cải thiện |

**Kết luận PoC-prompt:** rule chỉ tác động lên câu ĐÃ đúng (q023/q024). Hai câu gãy (q022/q025) vẫn gãy vì **gốc là RETRIEVAL/timeout, không phải prompt** — q025 elapsed **51.2s** (treo→fallback). Prompt không thể ép ra nội dung retrieval không trả về.

### Δ tổng & regressions
- **Δ mean_total: +0.52** (93.76 → 94.28) — trong biên nhiễu. Nguồn tăng thực: **q035 +5** (retrieval trả thêm CVE thật) + phân bổ tool_grounding cao hơn cho q023/q024.
- **q036 hallucination: CHƯA fixed** (bền cả 6 vòng).
- **PoC q022/q023/q024/q025: KHÔNG cải thiện** (q023/q024 vốn đã 100; q022 regress nhẹ 52→50; q025 giữ 52).
- **Regressions:** chỉ q022 (−2, câu chữ 'không tìm thấy' phẳng). Không regression chất lượng nghiêm trọng nào khác.

## 8. 15 case tệ nhất

| id | total | bucket | q (rút gọn) | vấn đề |
|---|---|---|---|---|
| q022 | 50 | no_grounding | "Log4Shell có PoC GitHub?" | 'Không tìm thấy' — thực tế hàng trăm PoC; regress nhẹ vs R5 |
| q029 | 50 | no_grounding | "Mitigation PAN-OS trước vá?" | 'Không tìm thấy' dù công khai có (tắt Device Telemetry + Threat ID) |
| q025 | 52 | no_grounding | "Zerologon có script Python?" | Fallback (elapsed 51.2s→treo); có zerologon_tester.py |
| q034 | 74 | other/DATA | "CVE liên quan CWE-918?" | Ngập 16 CVE-2026-* 10.0 synthetic + sai nhãn năm (2018→'2019') |
| q036 | 76 | hallucination | "Struts2 CVE nổi bật?" | **CVE-2006-1547 vẫn 'Beanutils RCE' (thực là DoS) — CHƯA sửa 6/6 vòng** |
| q028 | 83 | none | "Xử lý Citrix Bleed?" | Có remediation nhưng THIẾU kill phiên ICA/PCoIP (bước then chốt) |
| q038 | 84 | other/DATA | "CVE mới/cũ nhất?" | 'Mới nhất' = CVE-2026-* synthetic (oldest CVE-2007-0061 đúng) |
| q047 | 88 | none | "Hôm nay trời mưa?" | Từ chối đúng nhưng CỤT, thiếu closer/gợi ý ATTT (lệch q048) |
| q035 | 90 | other/misclass | "Fortinet SSL VPN CVE?" | CẢI THIỆN (4 CVE thật) nhưng CVE-2024-47571 (FortiManager) lọt vào SSL-VPN |
| q017 | 95 | none | "CVSS ProxyLogon?" | 9.8 (số đúng) nhưng kèm vector A:N (của 9.1) |
| q048 | 95 | none | "Công thức phở?" | Từ chối lịch sự + closer — tốt |
| q050 | 95 | none | "Cảm ơn" | Redirect thay 'không có gì' (hơi generic); latency 46.2s |
| q020 | 96 | none | "CVE-2018-13379 sản phẩm nào?" | Đúng FortiOS/FortiProxy nhưng cụt (thiếu SSL-VPN/CWE-22) |
| q037 | 96 | none | "Đếm CVE CRITICAL 2024?" | Trung thực 'chưa có tool' — đúng chuẩn |
| q039 | 96 | none | "CVE CVSS cao nhất?" | Trung thực + ví dụ 10.0 thật — đúng chuẩn |

## 9. Đề xuất cải thiện cuối (ưu tiên theo tác động) — [PROMPT]/[CODE]/[DATA]

1. **[DATA/CODE] q036 re-embed CHƯA có tác dụng — điều tra ĐƯỜNG TRẢ LỜI, không chỉ KB.** Bền 6/6 vòng. Việc cần: (i) XÓA record CVE-2006-1547 cũ khỏi Qdrant (không để bản 'Beanutils RCE' song song), (ii) rebuild BM25/`cve_kb_hybrid` (lexical vẫn khớp text cũ → CLAUDE.md cảnh báo tụt-BM25), (iii) xóa cache serving, (iv) **verify bằng chính câu q036** trả về 'DoS ActionForm', không kiểm KB tĩnh. Đây là hallucination web-bắt DUY NHẤT còn lại.
2. **[CODE/RETRIEVAL] Gốc q022/q025/q029 là RETRIEVAL/TIMEOUT, không phải prompt.** q025 treo 51.2s→fallback. Cần: timeout ngắn + **fallback TĨNH sang mô tả PoC/mitigation từ KB** (không 'hỏi lại'/'không tìm thấy'). Prompt-PoC của round này bất lực vì nội dung không được retrieve về. Tiềm năng ~+140đ (3 câu × ~47đ).
3. **[PROMPT] Cấm 'không tìm thấy' phẳng cho CVE nổi tiếng.** q022 regress do đổi sang 'không tìm thấy' — với CVE nổi tiếng (Log4Shell/Zerologon) đây là khẳng-định-sai. Rule: nếu là CVE nổi tiếng mà retrieval rỗng → nói 'chưa truy hồi được, nhưng theo hiểu biết chung PoC công khai tồn tại' thay vì phủ nhận.
4. **[DATA/RAG] Lọc/gắn cờ loạt CVE-2026-* synthetic 10.0.** q034 (16 entries) + q038 ('mới nhất') phơi CVE giả tương lai. Lọc năm > hôm nay (2026-09-04) HOẶC gắn nhãn 'chưa xác minh'; sửa nhãn năm sai (CVE-2018-14721 gắn '2019').
5. **[PROMPT] Checklist remediation KEV bắt buộc bước bổ sung.** q028 phải kèm **kill aaa/ica/pcoip session**; q029 (khi vá retrieval) kèm **tắt Device Telemetry + Threat ID 95187/95189**. Template 'patch + hành động bổ sung' cho CVE KEV lớn.
6. **[RETRIEVAL] Bộ lọc product/feature-relevance cho danh sách theo chủ đề.** q035 để lọt CVE-2024-47571 (FortiManager) vào 'SSL VPN' — thêm kiểm tra sản phẩm/feature khớp chủ đề trước khi liệt kê.
7. **[PROMPT] Nhất quán điểm+vector CVSS.** q017 in 9.8 kèm vector A:N (của 9.1). Khi nêu cả điểm lẫn vector phải khớp; nếu 2 nguồn (NVD 9.8 vs MS 9.1) ghi rõ cả hai + vector tương ứng.
8. **[PROMPT] Chuẩn hóa từ chối off-topic = 1 câu lịch sự + 1 gợi ý ATTT.** q047 ('trời mưa') cụt, thiếu closer như q048 — đồng bộ giọng.
9. **[PERF/RETRIEVAL] Điều tra latency cao lẻ tẻ (46-51s).** q025 51.2s, q019 49.4s, q004/q011/q050 ~46s, q031 41.8s — đi kèm nguy cơ retrieval treo→fallback (liên quan #2). Đặt timeout cứng + fallback tĩnh.
10. **[PROMPT] Giữ độ sâu root-cause + CWE cho CVE đặc thù.** q014 Zerologon nên nêu gốc mật mã (IV=0 AES-CFB8); q020 nên kèm SSL-VPN web portal + CWE-22 thay vì chỉ tên sản phẩm.

---
### Ghi chú divergence hợp lệ (không trừ điểm)
ProxyLogon NVD 9.8 vs MS 9.1; Citrix Bleed NVD 7.5 vs Citrix 9.4 (CWE-119); Fortinet CVE-2018-13379 NVD 9.8 vs 9.1; CVE-2020-12812 NVD 9.8 vs vendor 5.2; Heartbleed v3 7.5 vs v2 5.0; EternalBlue NVD 8.8 vs MS 8.1 — bot nêu MỘT giá trị hợp lệ đều chấp nhận. q026 khuyến nghị Log4j ≥2.17 (cao hơn mốc 2.16 — an toàn hơn, chấp nhận). CVE-2006-1547 CVSS 7.5 (số bot nêu ~khớp bản v3 rescored) NHƯNG bot mô tả BẢN CHẤT là RCE → vẫn hallucination.
