#!/usr/bin/env python3
"""Bộ eval retrieval RAG mở rộng (~150 query, 8 nhóm), ground-truth CVE thật. → eval_set.jsonl"""
import json
from collections import Counter
Q=[]
def add(cat, query, cves=None, expected=None):
    r={"id":f"{cat}-{sum(1 for x in Q if x['category']==cat)+1:02d}","category":cat,"query":query}
    if cves is not None: r["relevant_cves"]=cves
    if expected is not None: r["expected"]=expected
    Q.append(r)

# 1) ALIAS (30)
for name,cve in [("Log4Shell","CVE-2021-44228"),("EternalBlue","CVE-2017-0144"),("Zerologon","CVE-2020-1472"),
("ProxyShell","CVE-2021-34473"),("ProxyLogon","CVE-2021-26855"),("BlueKeep","CVE-2019-0708"),
("PrintNightmare","CVE-2021-34527"),("Follina","CVE-2022-30190"),("Spring4Shell","CVE-2022-22965"),
("Citrix Bleed","CVE-2023-4966"),("Heartbleed","CVE-2014-0160"),("Shellshock","CVE-2014-6271"),
("Dirty COW","CVE-2016-5195"),("PwnKit","CVE-2021-4034"),("Sudo Baron Samedit","CVE-2021-3156"),
("SMBGhost","CVE-2020-0796"),("SIGRed","CVE-2020-1350"),("Ghostcat","CVE-2020-1938"),
("backdoor XZ Utils","CVE-2024-3094"),("Text4Shell","CVE-2022-42889"),("Meltdown","CVE-2017-5754"),
("Spectre","CVE-2017-5753"),("Dirty Pipe","CVE-2022-0847"),("regreSSHion","CVE-2024-6387"),
("Terrapin","CVE-2023-48795"),("GHOST glibc","CVE-2015-0235"),("Drupalgeddon2","CVE-2018-7600"),
("Looney Tunables","CVE-2023-4911"),("KRACK","CVE-2017-13077"),("Ripple20","CVE-2020-11896")]:
    add("alias",f"Lỗ hổng {name} là gì?",[cve])

# 2) DESCRIPTION (25)
for desc,cve in [("Lỗ hổng cho phép giả mạo là bộ điều khiển miền qua giao thức Netlogon","CVE-2020-1472"),
("Lỗ hổng thực thi mã từ xa trong dịch vụ Windows Print Spooler","CVE-2021-34527"),
("Lỗ hổng RCE không cần xác thực trong giao thức RDP của Windows","CVE-2019-0708"),
("Lỗ hổng tràn bộ đệm đọc lộ nội dung bộ nhớ trong OpenSSL heartbeat","CVE-2014-0160"),
("Lỗ hổng chèn lệnh qua biến môi trường trong Bash","CVE-2014-6271"),
("Lỗ hổng deserialization JNDI trong thư viện logging Java","CVE-2021-44228"),
("Lỗ hổng path traversal và RCE trong Apache HTTP Server 2.4.49","CVE-2021-41773"),
("Lỗ hổng SSRF trên Microsoft Exchange dẫn tới chiếm quyền","CVE-2021-26855"),
("Lỗ hổng leo thang đặc quyền cục bộ trong polkit pkexec","CVE-2021-4034"),
("Lỗ hổng bỏ qua xác thực qua SQL injection trên JetBrains TeamCity","CVE-2024-27198"),
("Lỗ hổng khai thác hàng loạt đánh cắp dữ liệu trên MOVEit Transfer","CVE-2023-34362"),
("Lỗ hổng RCE trong OGNL của Apache Struts2 gây vụ rò rỉ Equifax","CVE-2017-5638"),
("Lỗ hổng đọc trộm session token trên Citrix NetScaler ADC","CVE-2023-4966"),
("Lỗ hổng chèn template MSDT trong tài liệu Office","CVE-2022-30190"),
("Lỗ hổng ghi đè ánh xạ bộ nhớ copy-on-write trong nhân Linux","CVE-2016-5195"),
("Lỗ hổng ghi trái phép vào pipe buffer trong nhân Linux","CVE-2022-0847"),
("Lỗ hổng tràn số nguyên trong glibc gethostbyname","CVE-2015-0235"),
("Lỗ hổng thực thi mã từ xa trước xác thực trong OpenSSH server","CVE-2024-6387"),
("Lỗ hổng chèn OGNL trong Apache Struts2 qua Content-Type","CVE-2017-5638"),
("Lỗ hổng RCE trong Atlassian Confluence qua OGNL injection","CVE-2022-26134"),
("Lỗ hổng đọc file tùy ý không cần xác thực trên Pulse Secure VPN","CVE-2019-11510"),
("Lỗ hổng command injection zero-day trên Palo Alto PAN-OS GlobalProtect","CVE-2024-3400"),
("Lỗ hổng RCE trên VMware vCenter qua plugin vSAN Health","CVE-2021-21985"),
("Lỗ hổng leo thang đặc quyền qua GameOver(lay) OverlayFS Linux","CVE-2023-2640"),
("Lỗ hổng tràn heap trong xử lý DNS SIG record trên Windows DNS Server","CVE-2020-1350")]:
    add("description",desc,[cve])

# 3) PRODUCT (20)
for prod,cves in [("Fortinet FortiOS SSL VPN path traversal",["CVE-2018-13379"]),
("F5 BIG-IP iControl REST cho phép RCE",["CVE-2022-1388","CVE-2021-22986"]),
("Atlassian Confluence OGNL injection RCE",["CVE-2022-26134","CVE-2021-26084"]),
("Citrix ADC/Gateway (NetScaler) path traversal RCE",["CVE-2019-19781"]),
("Ivanti Connect Secure bỏ qua xác thực",["CVE-2023-46805","CVE-2024-21887"]),
("VMware vCenter Server RCE",["CVE-2021-21972","CVE-2021-22005","CVE-2021-21985"]),
("Palo Alto PAN-OS GlobalProtect command injection",["CVE-2024-3400"]),
("Pulse Secure VPN đọc file tùy ý",["CVE-2019-11510"]),
("GitLab RCE không cần xác thực qua ExifTool",["CVE-2021-22205"]),
("Apache Log4j2 RCE",["CVE-2021-44228"]),
("Microsoft SharePoint RCE",["CVE-2019-0604","CVE-2023-29357"]),
("Progress MOVEit Transfer SQL injection",["CVE-2023-34362"]),
("Cisco IOS XE web UI leo thang đặc quyền",["CVE-2023-20198"]),
("SolarWinds Orion supply chain SUNBURST",["CVE-2020-10148"]),
("Zoho ManageEngine ADSelfService Plus RCE",["CVE-2021-40539"]),
("Oracle WebLogic Server RCE qua T3/IIOP",["CVE-2020-14882","CVE-2019-2725"]),
("Apache Struts2 RCE Jakarta Multipart",["CVE-2017-5638"]),
("Microsoft Exchange ProxyNotShell",["CVE-2022-41040","CVE-2022-41082"]),
("PaperCut MF/NG bỏ qua xác thực RCE",["CVE-2023-27350"]),
("Barracuda Email Security Gateway command injection zero-day",["CVE-2023-2868"])]:
    add("product",f"Lỗ hổng {prod}?",cves)

# 4) CWE / lớp lỗi (15) — kỳ vọng graph
for cwe,cves in [("chèn và thực thi lệnh hệ điều hành (CWE-78)",["CVE-2014-6271","CVE-2024-3400","CVE-2023-2868"]),
("deserialization dữ liệu không tin cậy (CWE-502)",["CVE-2021-44228","CVE-2017-9805","CVE-2015-4852"]),
("path traversal (CWE-22)",["CVE-2021-41773","CVE-2018-13379","CVE-2019-19781"]),
("server-side request forgery SSRF (CWE-918)",["CVE-2021-26855","CVE-2021-21972"]),
("SQL injection (CWE-89)",["CVE-2024-27198","CVE-2023-34362"]),
("leo thang đặc quyền cục bộ Linux",["CVE-2021-4034","CVE-2021-3156","CVE-2016-5195","CVE-2022-0847"]),
("use-after-free (CWE-416)",["CVE-2021-21220","CVE-2022-0609"]),
("tràn bộ đệm heap (CWE-122)",["CVE-2015-0235","CVE-2020-0796"]),
("cross-site scripting XSS (CWE-79)",["CVE-2018-6389","CVE-2019-11043"]),
("out-of-bounds write (CWE-787)",["CVE-2019-0708","CVE-2020-1350"]),
("bỏ qua xác thực (CWE-287)",["CVE-2022-1388","CVE-2023-46805","CVE-2020-1472"]),
("OGNL/expression injection",["CVE-2017-5638","CVE-2022-26134","CVE-2022-22965"]),
("XML external entity XXE (CWE-611)",["CVE-2014-3529"]),
("thực thi mã từ xa qua JNDI/LDAP",["CVE-2021-44228"]),
("chèn lệnh qua template (SSTI)",["CVE-2022-30190"])]:
    add("cwe",f"CVE nào là lỗ hổng {cwe}?",cves)

# 5) MULTI_HOP / quan hệ (15) — tra ngược/đa-hop, cần graph
for q,cves in [("CVE nào của Apache Struts2 liên quan OGNL bị khai thác thực tế?",["CVE-2017-5638","CVE-2018-11776"]),
("Các CVE path traversal của Fortinet FortiOS?",["CVE-2018-13379"]),
("Những CVE nào trên Microsoft Exchange tạo thành chuỗi ProxyLogon?",["CVE-2021-26855","CVE-2021-27065","CVE-2021-26857","CVE-2021-26858"]),
("CVE nào của Citrix cho phép chiếm phiên đăng nhập?",["CVE-2023-4966"]),
("Các lỗ hổng RCE của Apache HTTP Server 2.4.49/2.4.50?",["CVE-2021-41773","CVE-2021-42013"]),
("CVE deserialization nào trong Oracle WebLogic?",["CVE-2019-2725","CVE-2020-14882"]),
("Lỗ hổng nào của SonicWall SMA bị khai thác zero-day?",["CVE-2021-20016"]),
("CVE nào của VMware vCenter cho phép RCE không cần xác thực?",["CVE-2021-21972","CVE-2021-22005"]),
("Chuỗi CVE ProxyNotShell trên Exchange gồm những gì?",["CVE-2022-41040","CVE-2022-41082"]),
("Lỗ hổng leo thang đặc quyền nhân Linux OverlayFS?",["CVE-2021-3493","CVE-2023-0386","CVE-2023-2640"]),
("CVE nào trên Ivanti Connect Secure bị chuỗi để RCE?",["CVE-2023-46805","CVE-2024-21887"]),
("Lỗ hổng nào của Zimbra bị khai thác để RCE?",["CVE-2022-27925","CVE-2022-41352"]),
("CVE nào của Cisco ASA/FTD bị khai thác VPN?",["CVE-2020-3452","CVE-2018-0101"]),
("Lỗ hổng RCE của Spring Framework/Spring Cloud?",["CVE-2022-22965","CVE-2022-22963"]),
("CVE nào của Windows SMBv3 cho phép RCE wormable?",["CVE-2020-0796"])]:
    add("multi_hop",q,cves)

# 6) EXACT_ID (12)
for cve in ["CVE-2021-44228","CVE-2017-0144","CVE-2020-1472","CVE-2019-0708","CVE-2018-13379",
"CVE-2023-4966","CVE-2024-3094","CVE-2022-30190","CVE-2021-34473","CVE-2014-0160","CVE-2024-3400","CVE-2023-34362"]:
    add("exact_id",f"{cve} là lỗ hổng gì?",[cve])

# 7) AGGREGATE (14) — answer-level (đếm/cũ-mới nhất/lọc), không tính recall
for q,exp in [("Cơ sở tri thức có tổng cộng bao nhiêu CVE?","count_total"),
("CVE cũ nhất trong hệ thống là năm nào?","min_year~1999"),
("CVE mới nhất được ghi nhận là gì?","max_recent"),
("Có bao nhiêu CVE năm 2024?","count_year_2024"),
("Có bao nhiêu CVE mức CRITICAL?","count_severity_critical"),
("Dữ liệu bao phủ từ năm nào đến năm nào?","range_1999_2026"),
("Có bao nhiêu CVE nằm trong danh sách KEV của CISA?","count_kev"),
("Bao nhiêu CVE của năm 2021 có mức nghiêm trọng cao?","count_year_severity"),
("Sản phẩm nào có nhiều CVE nhất trong KB?","top_product"),
("Có bao nhiêu CVE liên quan CWE-79?","count_cwe79"),
("CVE nào có điểm CVSS cao nhất (10.0)?","list_cvss10"),
("Trung bình mỗi năm có bao nhiêu CVE?","avg_per_year"),
("Bao nhiêu CVE được cập nhật trong 30 ngày qua?","count_recent_update"),
("Năm nào có nhiều CVE nhất?","peak_year")]:
    add("aggregate",q,expected=exp)

# 8) EDGE (8) — không tồn tại → đúng = không tìm thấy / không bịa
for q in ["CVE-2030-99999 là gì?","CVE-2099-00001 mô tả lỗ hổng nào?","CVE-ABCD-1234 là gì?",
"Lỗ hổng CVE-2045-55555 nghiêm trọng ra sao?","CVE-2027-12345 ảnh hưởng sản phẩm nào?",
"CVE-2020-9999999 có PoC không?","Lỗ hổng FooBarShell năm 2035 là gì?","CVE-1990-0001 là gì?"]:
    add("edge",q,[])

open("eval_set.jsonl","w").write("\n".join(json.dumps(x,ensure_ascii=False) for x in Q))
print(f"eval_set.jsonl: {len(Q)} query | nhóm:",dict(Counter(x['category'] for x in Q)))
