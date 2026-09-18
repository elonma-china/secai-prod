# Ground truth — questions_50.jsonl (fixed 50-question benchmark)

Nguồn: NVD (nvd.nist.gov, REST API `services.nvd.nist.gov/rest/json/cves/2.0`, tra cứu 2026-09-04) +
vendor advisories (Microsoft/Fortinet/Citrix/Palo Alto/JetBrains) + cwe.mitre.org / attack.mitre.org.
Đã đối chiếu 4 giá trị trùng với `cvss_corrections.json` sẵn có trong repo — khớp 100%
(CVE-2020-1472=10.0, CVE-2021-26855=9.8, CVE-2023-4966=7.5, CVE-2018-13379=9.8).

Ghi chú chung: một số CVE có **điểm CVSS khác nhau giữa NVD và vendor** (do vector khác nhau) —
cả hai giá trị đều "hợp lý", grader không nên trừ điểm nếu bot nêu đúng MỘT trong hai và có nguồn/ngữ cảnh rõ.

## CVE facts theo câu hỏi

- **q013 — CVE-2021-44228 (Log4Shell, Apache Log4j2)**: JNDI injection qua log message → tải class từ xa → RCE.
  CVSS v3.1 **10.0 CRITICAL** (NVD). CWE-502 (Deserialization of Untrusted Data); Apache cũng gắn CWE-20/CWE-400;
  NVD còn gắn CWE-917. Ảnh hưởng Log4j2 2.0-beta9 → 2.15.0. Vá: 2.15.0 giảm thiểu (tắt lookup mặc định),
  **khắc phục triệt để ở 2.16.0+** (và bản backport 2.12.2/2.12.3/2.3.1).

- **q014 — CVE-2020-1472 (Zerologon, Windows Netlogon)**: lỗi mật mã (IV = 0 cố định trong AES-CFB8) cho phép
  giả mạo xác thực Netlogon → chiếm quyền Domain Admin. CVSS v3.1 **10.0 CRITICAL** (NVD tự tính lại;
  điểm Microsoft tự khai trong cùng record là 5.5 MEDIUM — được xem là lỗi nhập liệu/vector sai, **không dùng**).
  Không có CWE được NVD gán (NVD-CWE-noinfo). Vá: 2 giai đoạn — bản vá ban đầu 11/08/2020, bắt buộc enforcement
  mode từ 09/02/2021.

- **q015 — CVE-2017-0144 (EternalBlue, Windows SMBv1)**: buffer overflow trong SMBv1 qua gói tin dựng sẵn → RCE
  (nền tảng của WannaCry/NotPetya). Giao thức: **SMBv1 (Server Message Block v1)**. CVSS v3.1 **8.8 HIGH** (NVD;
  CVSS v2 9.3 HIGH cũng có trên record). Không có CWE được gán. Ảnh hưởng nhiều bản Windows (Vista SP2 → Server 2016).
  Vá: **MS17-010** (14/03/2017).

- **q016 — CVE-2023-34362 (Progress MOVEit Transfer)**: SQL injection không cần xác thực → cài webshell → RCE
  (chiến dịch khai thác hàng loạt của nhóm Cl0p). CVSS v3.1 **9.8 CRITICAL** (NVD). CWE-89 (SQL Injection).
  Ảnh hưởng MOVEit Transfer trước các bản 2021.0.6, 2021.1.4, 2022.0.4, 2022.1.5, 2023.0.1 (và mọi bản cũ hơn không hỗ trợ).

- **q017 — CVE-2021-26855 (ProxyLogon, Microsoft Exchange)**: CVSS v3.1 **9.8 CRITICAL theo NVD tự tính lại**
  (khác với điểm Microsoft tự khai là **9.1 CRITICAL** — NVD nâng Availability impact lên High). Cả hai đều
  được trích dẫn rộng rãi. CWE-918 (SSRF). Ảnh hưởng Exchange Server 2013 (CU21-23), 2016 (CU8-19), 2019 (RTM-CU8).
  Vá: bản cập nhật khẩn cấp tháng 03/2021.

- **q018 — CVE-2023-4966 (Citrix Bleed, NetScaler ADC/Gateway)**: CVSS **KHÔNG THỐNG NHẤT giữa nguồn** —
  NVD chính thức ghi **7.5 HIGH** (C:H/I:N/A:N), trong khi Citrix tự công bố **9.4 CRITICAL** (C:H/I:H/A:L) —
  đây là con số được báo chí/cộng đồng trích dẫn phổ biến hơn. Cả hai giá trị đều chấp nhận được nếu bot nêu rõ nguồn.
  CWE: NVD không gán CWE cụ thể; Citrix ghi CWE-119 (Buffer Overflow) — về bản chất là lỗi đọc-tràn-bộ-đệm rò rỉ
  session token. Ảnh hưởng NetScaler ADC/Gateway nhiều nhánh bản (12.1 < 55.300, 13.0 < 92.19, 13.1 < 49.15
  [FIPS: < 37.164], 14.1 < 8.50) khi cấu hình làm Gateway/AAA vserver.

- **q019 — CVE-2024-3400 (Palo Alto PAN-OS GlobalProtect)**: command injection không cần xác thực → tạo file
  tùy ý → RCE với quyền root trên firewall. CVSS v3.1 **10.0 CRITICAL** (NVD khớp điểm vendor). CWE-77
  (Command Injection; vendor còn ghi thêm CWE-20). Sản phẩm ảnh hưởng: **PAN-OS** (10.2 < 10.2.9-h1,
  11.0 < 11.0.4-h1, 11.1 < 11.1.2-h3), chỉ khi cấu hình GlobalProtect gateway/portal (9.0/9.1/10.0/10.1 không bị ảnh hưởng).

- **q020 — CVE-2018-13379 (Fortinet)**: sản phẩm cụ thể là **FortiOS SSL-VPN web portal** (và FortiProxy).
  Ảnh hưởng FortiOS 6.0.0-6.0.4, 5.6.3-5.6.7, 5.4.6-5.4.12; FortiProxy 2.0.0, 1.2.0-1.2.8, 1.1.0-1.1.6, 1.0.0-1.0.7.
  CVSS v3.1 NVD **9.8 CRITICAL** (điểm vendor tự khai là 9.1 CRITICAL — NVD chấm Integrity impact cao hơn).
  CWE-22 (Path Traversal). Vá: FortiOS 5.4.13/5.6.8/6.0.5 trở lên.

- **q021 — CVE-2014-0160 (Heartbleed, OpenSSL)**: CVSS **phụ thuộc phiên bản chấm điểm** — ban đầu (2014, thời
  điểm CVSS v3 chưa tồn tại) chỉ có **CVSS v2.0 5.0 MEDIUM**; NVD sau này bổ sung **CVSS v3.1 7.5 HIGH**. Cả
  hai đều đúng tùy ngữ cảnh câu trả lời nêu rõ phiên bản CVSS nào. CWE-125 (Out-of-bounds Read). Ảnh hưởng
  OpenSSL 1.0.1 trước 1.0.1g. Vá: OpenSSL 1.0.1g.

- **q022 — CVE-2021-44228 PoC**: CÓ, rất nhiều PoC công khai trên GitHub ngay từ ngày công bố (vd.
  `log4j-shell-poc`, hàng trăm repo khác) — đây là một trong những lỗ hổng có PoC phổ biến nhất từng ghi nhận.

- **q023 — CVE-2017-5638 (Struts2/Equifax) Metasploit**: CÓ — module Metasploit
  `exploit/multi/http/struts2_content_type_ognl` khai thác lỗi này đã tồn tại từ 2017. CWE thực tế của CVE
  này là **CWE-755** (Improper Handling of Exceptional Conditions) — KHÔNG phải nhóm injection dù hệ quả là RCE
  (lỗi OGNL injection xảy ra qua thông báo lỗi khi parser Jakarta Multipart xử lý header Content-Type sai định dạng).
  CVSS v3.1 9.8 CRITICAL (NVD; v2 10.0 HIGH cũng có). Ảnh hưởng Struts 2.3.x < 2.3.32, 2.5.x < 2.5.10.1.
  Vá: 2.3.32 / 2.5.10.1. Đây là nguyên nhân gốc của vụ rò rỉ dữ liệu Equifax 2017.

- **q024 — CVE-2024-27198 (JetBrains TeamCity) PoC**: CÓ — PoC/exploit công khai xuất hiện nhanh sau khi
  Rapid7 công bố lỗi (nhiều repo GitHub + writeup kỹ thuật). CVSS v3.1 9.8 CRITICAL. CWE: NVD gắn nhãn chung
  "NVD-CWE-Other" (chưa map); JetBrains tự ghi CWE-288 (Authentication Bypass Using an Alternate Path/Channel).
  Ảnh hưởng TeamCity On-Premises trước 2023.11.4. Vá: 2023.11.4 (có patch-plugin cho bản cũ không còn hỗ trợ).

- **q025 — CVE-2020-1472 (Zerologon) Python script**: CÓ — script khai thác Python nổi tiếng
  `zerologon_tester.py` (Secura BV) và mô-đun trong Impacket đều công khai trên GitHub từ 2020.

- **q026 — Vá CVE-2021-44228 (Log4Shell)**: nâng cấp Log4j2 lên **≥ 2.16.0** (khắc phục triệt để; 2.15.0 chỉ
  giảm thiểu bằng cách tắt JNDI lookup mặc định) hoặc bản backport 2.12.2/2.12.3/2.3.1 cho Java 7/6. Biện pháp
  tạm nếu chưa vá được ngay: set `log4j2.formatMsgNoLookups=true` / biến môi trường
  `LOG4J_FORMAT_MSG_NO_LOOKUPS=true`, xóa class `JndiLookup` khỏi classpath, chặn outbound LDAP/RMI/DNS bất thường.

- **q027 — Khắc phục CVE-2017-0144 (EternalBlue)**: cài bản vá **MS17-010**; nếu chưa vá kịp thì **tắt SMBv1**
  (disable SMBv1 protocol) và chặn cổng 445 ở firewall biên.

- **q028 — Xử lý CVE-2023-4966 (Citrix Bleed)**: nâng cấp NetScaler ADC/Gateway lên bản đã vá
  (12.1-55.300+ / 13.0-92.19+ / 13.1-49.15+ [FIPS: 13.1-37.164+] / 14.1-8.50+) **VÀ** phải chủ động chấm dứt
  (kill) toàn bộ phiên ICA/PCoIP đang hoạt động — chỉ vá thôi không vô hiệu hóa token phiên đã bị đánh cắp
  trước đó (đây là điểm mấu chốt của khuyến cáo chính thức Citrix/CISA).

- **q029 — Giảm thiểu CVE-2024-3400 (PAN-OS) trước khi vá**: theo khuyến cáo Palo Alto — nếu chưa có giấy phép
  Device Telemetry thì **tắt tính năng Device Telemetry** trên thiết bị bị ảnh hưởng (chặn được vector khai
  thác cụ thể này); đồng thời bật chữ ký Threat Prevention liên quan (Threat ID 95187, 95189) nếu đã mua
  license Threat Prevention. Về sau vẫn phải nâng cấp lên bản đã vá (10.2.9-h1/11.0.4-h1/11.1.2-h3).

- **q030 — CWE-79**: Cross-Site Scripting (XSS) — sản phẩm không trung hòa input do người dùng kiểm soát
  trước khi đưa vào output trang web, cho phép script của kẻ tấn công chạy trong phiên trình duyệt của nạn nhân.

- **q031 — CWE-89 vs CWE-78**: CWE-89 (SQL Injection) — dựng câu lệnh SQL từ input không được kiểm soát, kẻ
  tấn công thay đổi logic/cấu trúc truy vấn CSDL. CWE-78 (OS Command Injection) — dựng lệnh hệ điều hành từ
  input không được kiểm soát, kẻ tấn công thực thi lệnh hệ thống ngoài ý muốn. Khác nhau ở **tầng bị tấn công**
  (CSDL vs OS shell) dù cùng là lớp lỗi "improper neutralization of special elements".

- **q032 — CWE-918 (SSRF)**: Server-Side Request Forgery — server gửi request tới một URL do thành phần
  upstream (thường kẻ tấn công có thể ảnh hưởng) cung cấp mà không xác minh đầy đủ đích đến, cho phép request
  bị chuyển hướng tới tài nguyên không mong muốn (thường là mạng nội bộ).

- **q033 — MITRE ATT&CK T1190 (Exploit Public-Facing Application)**: kỹ thuật Initial Access — kẻ tấn công
  khai thác lỗi phần mềm, glitch hoặc cấu hình sai trên hệ thống hướng-internet (web server, database, thiết
  bị mạng...) để giành chỗ đứng ban đầu trong hệ thống mục tiêu.

- **q034 — CVE liên quan CWE-918 (SSRF)**: KB nên trả về các CVE thuộc lớp SSRF; ví dụ thực tế nổi bật nhất
  là **CVE-2021-26855 (ProxyLogon)**. Câu trả lời phụ thuộc dữ liệu KB có gì — chấp nhận nếu KB liệt kê đúng
  các CVE thật sự có CWE-918, không chấp nhận nếu bịa CVE không liên quan SSRF.

- **q035 — Fortinet SSL-VPN CVE đáng chú ý**: ngoài CVE-2018-13379 (path traversal, xem q020), các CVE
  SSL-VPN Fortinet nổi bật khác gồm **CVE-2022-42475** (heap buffer overflow FortiOS SSL-VPN, CVSS ~9.3
  CRITICAL, bị khai thác 0-day) và **CVE-2023-27997 "Xortigate"** (heap buffer overflow FortiOS/FortiProxy
  SSL-VPN, CVSS 9.8 CRITICAL). Câu trả lời tốt nên nhắc ít nhất 1-2 CVE thật, không bịa số CVE không tồn tại.

- **q036 — Apache Struts2 CVE nổi bật**: **CVE-2017-5638** (S2-045, Equifax — xem q023), **CVE-2017-9805**
  (S2-052, REST plugin XStream deserialization RCE, CVSS 8.1), **CVE-2018-11776** (S2-057, RCE qua namespace
  không xác định, CVSS 8.1), **CVE-2020-17530** (S2-061, OGNL injection RCE, CVSS 9.8). Câu trả lời tốt nên
  nêu ít nhất vài CVE thật trong nhóm này.

- **q037-039 (aggregate_stats)**: KHÔNG có ground-truth cố định — phụ thuộc hoàn toàn nội dung KB hiện có tại
  thời điểm test. Theo rubric: chấp nhận câu trả lời ước lượng có nêu rõ là ước lượng, hoặc nói chưa truy vấn
  được số chính xác; KHÔNG chấp nhận bịa số cụ thể như thể là số thật.

- **q040 — SSRF là gì**: xem định nghĩa CWE-918 ở q032 (bản diễn giải phổ thông, không cần trích CWE).

- **q041 — SQL Injection là gì**: kẻ tấn công chèn/sửa đổi câu lệnh SQL qua input không được kiểm soát (không
  dùng parameterized query/prepared statement), khiến CSDL thực thi lệnh ngoài ý muốn của ứng dụng (đọc/sửa/xóa
  dữ liệu trái phép, bypass xác thực...).

- **q042 — Zero-day vs N-day**: zero-day = lỗ hổng bị khai thác/công bố **trước khi** có bản vá chính thức
  (nhà phát triển có "0 ngày" để chuẩn bị); N-day = lỗ hổng **đã có bản vá công khai** nhưng hệ thống chưa vá
  kịp — N là số ngày kể từ khi bản vá/thông tin công khai.

- **q043 — CVE-2035-99999**: KHÔNG TỒN TẠI (năm 2035 chưa xảy ra, hôm nay là 2026-09-04) — bot phải trả lời
  "không tìm thấy", TUYỆT ĐỐI không bịa mô tả lỗ hổng.

- **q044 — CVE-2028-00001**: KHÔNG TỒN TẠI (năm 2028 là tương lai so với hôm nay 2026-09-04) — bot phải trả
  lời "không tìm thấy/không có PoC vì CVE không tồn tại", không bịa.

## Câu không có ground-truth cố định (chấm theo rubric, không theo đáp án fix)
q001-012 (system/meta), q045-050 (edge/robustness còn lại: vague_messy, random_offtopic, greeting_chitchat)
— chấm theo tiêu chí "đúng thái độ" trong `rubric.md`, không có đáp án đúng/sai cố định.
