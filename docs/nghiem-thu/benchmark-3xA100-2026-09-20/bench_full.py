#!/usr/bin/env python3
"""bench_full.py — script đo hiệu năng DUY NHẤT cho cụm serving Cyber-F1/sec-embedding/
sec-rerank trên vast.ai (kiến trúc: 1 replica/GPU, TP=1, nginx least_conn LB — xem
vast/on_start.sh). Chạy TRÊN CHÍNH máy GPU (SSH vào rồi chạy `python3 bench_full.py`,
hoặc `ssh ... python3 - < bench_full.py`) để đo tại-host, tránh sai số WAN/tunnel
(đã xác nhận: đo qua SSH-tunnel làm RPS đọc thấp giả ~6x so với tại-host).

Đo đúng trên TARGET_GPUS bên dưới (mặc định 3 GPU — khớp cấu hình hợp đồng yêu cầu),
bypass nginx (nginx pool cả cụm thuê, không tách được theo số GPU) — round-robin thủ
công qua đúng các cổng nội bộ của TARGET_GPUS, các GPU khác của cụm (nếu có) không
nhận request nào trong lúc đo:
  1. LLM  — TPS decode (300 completion tokens/prompt) + TTFT streaming.
  2. Embedding — latency breakdown (p50/p95/p99, tuần tự + đồng thời) + throughput.
  3. Reranker — như embedding.
  4. Load test @ CCU=30 (đúng mốc hợp đồng yêu cầu) + ramp tìm điểm bão hoà.

Đổi số/danh sách GPU cần đo: sửa TARGET_GPUS bên dưới.

Output: 1 dòng JSON (prefix BENCH_FULL_RESULT).
"""
import json
import time
import http.client
import itertools
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ── Cấu hình cụm (khớp vast/on_start.sh) ────────────────────────────────────
API_KEY = "sk-secai2026"
LLM_MODEL = "DuyTa/Cyber-F1-AWQ"
EMBED_MODEL = "DuyTa/sec-embedding"
RERANK_MODEL = "DuyTa/sec-rerank"

LLM_PORT_BASE = 18101
EMBED_PORT_BASE = 18001
RERANK_PORT_BASE = 18201

TARGET_GPUS = [0, 1, 2]        # đúng số GPU cần đo theo yêu cầu hợp đồng

HEADERS = {"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY}


def pctl(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 3) if f != c else round(s[f], 3)


def post_json(port, path, body, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=HEADERS)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw), (time.perf_counter() - t0) * 1000.0


# ══ 1) LLM: TPS decode + TTFT streaming (round-robin đúng TARGET_GPUS) ══════
_llm_ports = itertools.cycle([LLM_PORT_BASE + g for g in TARGET_GPUS])
_LLM_PROMPTS = [
    "Explain in detail how a SQL injection attack works",
    "Describe the CVE-2021-44228 Log4Shell vulnerability",
    "What are the OWASP Top 10 web application security risks",
    "Explain the difference between symmetric and asymmetric encryption",
    "Describe how a buffer overflow vulnerability can be exploited",
    "What is a Cross-Site Request Forgery (CSRF) attack",
]


def llm_tps_sample(prompt):
    port = next(_llm_ports)
    body = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300, "stream": False}
    resp, ms = post_json(port, "/v1/chat/completions", body, timeout=60)
    ctok = resp.get("usage", {}).get("completion_tokens", 0)
    gen_s = ms / 1000.0
    return {"port": port, "completion_tokens": ctok, "tok_s": round(ctok / gen_s, 2) if gen_s else None}


def llm_ttft_sample(prompt):
    port = next(_llm_ports)
    body = json.dumps({"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 50, "stream": True}, ensure_ascii=False).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    t0 = time.perf_counter()
    conn.request("POST", "/v1/chat/completions", body=body, headers=HEADERS)
    resp = conn.getresponse()
    ttft_ms = None
    for line in resp:
        if line.strip() and line.startswith(b"data:") and b"[DONE]" not in line:
            ttft_ms = (time.perf_counter() - t0) * 1000.0
            break
    conn.close()
    return {"port": port, "ttft_ms": round(ttft_ms, 1) if ttft_ms else None}


def run_llm_tps_ttft():
    tps_samples = [llm_tps_sample(p) for p in _LLM_PROMPTS]
    tps_vals = [s["tok_s"] for s in tps_samples if s["tok_s"]]
    ttft_samples = [llm_ttft_sample(p) for p in _LLM_PROMPTS]
    ttft_vals = [s["ttft_ms"] for s in ttft_samples if s["ttft_ms"]]
    return {
        "tps": {"n": len(tps_samples), "tok_s_p50": pctl(tps_vals, 50),
                "tok_s_mean": round(statistics.mean(tps_vals), 2) if tps_vals else None,
                "tok_s_min": min(tps_vals) if tps_vals else None, "tok_s_max": max(tps_vals) if tps_vals else None,
                "samples": tps_samples},
        "ttft_raw": {"n": len(ttft_samples), "ttft_p50_ms": pctl(ttft_vals, 50),
                     "ttft_mean_ms": round(statistics.mean(ttft_vals), 1) if ttft_vals else None,
                     "samples": ttft_samples},
    }


# ══ 2) Embedding / Reranker: latency breakdown + throughput (TARGET_GPUS) ══
# Embedding: đúng thực tế production chỉ embed CÂU HỎI NGẮN của user lúc truy vấn
# (retrieval/qdrant.py: embed(client, query)), không embed tài liệu dài — câu hỏi
# ngắn dưới đây là đại diện đúng, không cần sửa.
def _make_embed_call(ports_cycle):
    def call(i):
        port = next(ports_cycle)
        body = {"model": EMBED_MODEL, "input": f"vulnerability test query about CVE-2021-{44000+i} SQL injection"}
        _, ms = post_json(port, "/v1/embeddings", body, timeout=30)
        return ms
    return call


# 60 văn bản THẬT — bản đóng băng gốc nằm ở data/rerank_docs_60.json (cùng thư mục),
# lấy trực tiếp từ Qdrant collection cve_kb_hybrid (mean=405 ký tự, median=394 — khớp
# sát phân bố thật toàn KB mean=432/median=370), THAY vì chuỗi giả ngắn (~70 ký tự)
# trước đây — rerank tính toán nặng theo độ dài văn bản nên text giả ngắn làm
# throughput/latency bị đo lệch (nhanh/thấp giả tạo). Nhúng thẳng (không đọc file)
# vì script này chạy qua SSH pipe trên máy GPU từ xa, không có quyền truy cập file
# cục bộ — nếu data/rerank_docs_60.json đổi, regenerate đoạn REAL_KB_DOCS bên dưới
# từ file đó (đọc + repr() list) rồi dán đè lại.
REAL_KB_DOCS = ['CVE ID: CVE-2026-7673 | Severity: MEDIUM | Weaknesses: CWE-434 | Published: 2026-05-03T02:17:12.537 | Summary: A vulnerability was detected in crmeb_java up to 1.3.4. This vulnerability affects unknown code of the file crmeb/crmeb-service/src/main/java/com/zbkj/service/service/impl/UploadServiceImpl.java of the component Admin Upload. Performing a manipulation of the argument model results in unrestricted upload. Remote exploitation of the attack is possible. The exploit is now public and may be used. The vendor was contacted early about this disclosure but did not respond in any way.', 'CVE ID: CVE-2025-29523 | Severity: HIGH | Affected Products: dsl-7740c_firmware, dsl-7740c | Vendors: dlink | CWE Weaknesses: CWE-77 | CAPEC Attack Patterns: CAPEC-43, CAPEC-183, CAPEC-76, CAPEC-248, CAPEC-136, CAPEC-15, CAPEC-40, CAPEC-75 | Tags: capec, capec_capec-43, capec_capec-76, capec_capec-183', 'CVE ID: CVE-2026-57241 | Title: The application opens the PDF, and JavaScript performs operations on the page and the document, causing the page-related objects within the application to lose | Severity: MEDIUM | CVSS: 6.1 | Vector: CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:L/I:N/A:H | CWE Weaknesses: CWE-125 | Affected Products: foxit_pdf_editor, foxit_reader | Description: The application opens the PDF, and JavaScript performs operations on the page and the document, causing the page-related objects within the application to lose synchronization; however, the renderer still trusts the outdated page count, and eventually the application crashes due to out-of-bounds access.', 'CVE ID: CVE-2020-10248 | BWA DiREX-Pro 1.2181 devices allow remote attackers to discover passwords via a direct request to val_users.php3. | Published: 2020-03-09 | CVSS v3: 7.5 HIGH | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:NONE A:NONE | CVSS v2: 5.0 | AV:N/AC:L/Au:N/C:P/I:N/A:N', 'CVE ID: CVE-2016-1000029 | Tenable Nessus before 6.8 has a stored XSS issue that requires admin-level authentication to the Nessus UI, and would potentially impact other admins (Tenable IDs 5218 and 5269). | Published: 2019-12-27 | CVSS v3: 4.8 MEDIUM | Vector: CVSS:3.1/AV:N/AC:L/PR:H/UI:R/S:C/C:L/I:L/A:N | AV:NETWORK AC:LOW PR:HIGH UI:REQUIRED S:CHANGED | Impact: C:LOW I:LOW A:NONE | CVSS v2: 3.5 | AV:N/AC:M/Au:S/C:N/I:P/A:N', 'CVE ID: CVE-2026-24585 | Missing Authorization vulnerability in Hyyan Abo Fakher Hyyan WooCommerce Polylang Integration woo-poly-integration allows Exploiting Incorrectly Configured Access Control Security Levels.This issue affects Hyyan WooCommerce Polylang Integration: from n/a through <= 1.5.0. | Published: 2026-01-23 | CVSS v3: 6.5 MEDIUM | Vector: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N | AV:NETWORK AC:LOW PR:LOW UI:NONE S:UNCHANGED | Impact: C:HIGH I:NONE A:NONE', 'CVE ID: CVE-2023-27531 | Severity: MEDIUM | CWE Weaknesses: CWE-502 | CAPEC Attack Patterns: CAPEC-586 | Tags: capec, capec_capec-586', 'CVE ID: CVE-2024-8909 | Severity: MEDIUM | Affected Products: chrome, iphone_os | Vendors: apple, google | CWE Weaknesses: CWE-451 | CAPEC Attack Patterns: CAPEC-154, CAPEC-98, CAPEC-163, CAPEC-173, CAPEC-164 | MITRE ATT&CK Techniques: T1566.001, T1566, T1534, T1566.002, T1598, T1598.003, T1566.003, T1598.001, T1598.002 | MITRE ATT&CK Tactics: T1598, T1566 | Tags: technique_t1534, capec_capec-163, operating_system, capec_capec-154, capec_capec-98, mitre_attack, technique_t1566.001, technique_t1566, tactic_t1598, capec, tactic_t1566', 'CVE ID: CVE-2022-22057 | Severity: HIGH | Affected Products: sm7325p, sw5100, wcn6750_firmware, qca8081_firmware, qca6174a, sd439, wcn7850, sdx12_firmware, wcn6850_firmware, wcn3991, qca8337, wcn3660b_firmware, qca6436_firmware, wcn3998_firmware, sd865_5g_firmware, wcn6855_firmware, wsa8815, qrb5165m, wcn3910, sa8155p | Vendors: qualcomm | CWE Weaknesses: CWE-362 | CAPEC Attack Patterns: CAPEC-29, CAPEC-26 | Tags: capec, capec_capec-29, capec_capec-26', 'CVE ID: CVE-2021-28434 | Severity: HIGH | Affected Products: windows_server_2012, windows_10, windows_server_2016, windows_7, windows_rt_8.1, windows_server_2019, windows_server_2008, windows_8.1 | Vendors: microsoft | Tags: web_application', 'CVE ID: CVE-2023-4688 | Sensitive information leak through log files. The following products are affected: Acronis Agent (Linux, macOS, Windows) before build 35433. | Published: 2023-08-31 | CVSS v3: 5.5 MEDIUM | Vector: CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N | AV:LOCAL AC:LOW PR:LOW UI:NONE S:UNCHANGED | Impact: C:HIGH I:NONE A:NONE', 'CVE ID: CVE-2026-10967 | Severity: HIGH | EPSS: 0.00267 | In KEV: False | Vendors/Products: {"google": ["android", "chrome"]} | Weaknesses: CWE-416, CWE-825 | Created: 2026-06-04T23:03:59.716000Z | Updated: 2026-06-06T01:44:36.243000Z | Summary: Use after free in SurfaceCapture in Google Chrome on Android prior to 149.0.7827.53 allowed a remote attacker who had compromised the renderer process to potentially perform a sandbox escape via a crafted HTML page. (Chromium security severity: High)', 'CVE ID: CVE-2021-23490 | Severity: HIGH | Affected Products: parse-link-header | Vendors: parse-link-header_project | CWE Weaknesses: CWE-1333 | CAPEC Attack Patterns: CAPEC-492 | Tags: capec, capec_capec-492', 'CVE ID: CVE-2025-2036 | Severity: MEDIUM | Affected Products: ecommerce-website-using-php | Vendors: s-a-zhd | CWE Weaknesses: CWE-89, CWE-74 | CAPEC Attack Patterns: CAPEC-53, CAPEC-51, CAPEC-9, CAPEC-10, CAPEC-470, CAPEC-42, CAPEC-52, CAPEC-24, CAPEC-135, CAPEC-14 | MITRE ATT&CK Techniques: T1574.006, T1574.007, T1027, T1562.003 | MITRE ATT&CK Tactics: T1562, T1574 | Tags: capec_capec-9, capec_capec-53, sql_injection, web_application, tactic_t1574, technique_t1027, capec_capec-51, technique_t1574.007, mitre_attack, technique_t1574.006, database, tactic_t1562, capec', 'CVE ID: CVE-2019-15363 | The Leagoo Power 5 Android device with a build fingerprint of LEAGOO/Power_5/Power_5:8.1.0/O11019/1532686195:user/release-keys contains a pre-installed app with a package name of com.mediatek.wfo.impl app (versionCode=27, versionName=8.1.0) that allows any app co-located on the device to modify a system property through an exported interface without proper authorization. | Published: 2019-11-14 | CVSS v3: 5.5 MEDIUM | Vector: CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N | AV:LOCAL AC:LOW PR:LOW UI:NONE S:UNCHANGED | Impact: C:NONE I:HIGH A:NONE | CVSS v2: 2.1 | AV:L/AC:L/Au:N/C:N/I:P/A:N', 'CVE ID: CVE-2013-1582 | The dissect_clnp function in epan/dissectors/packet-clnp.c in the CLNP dissector in Wireshark 1.6.x before 1.6.13 and 1.8.x before 1.8.5 does not properly manage an offset variable, which allows remote attackers to cause a denial of service (infinite loop or application crash) via a malformed packet. | Published: 2013-02-03 | CVSS v2: 2.9 | AV:A/AC:M/Au:N/C:N/I:N/A:P', 'CVE ID: CVE-2022-2697 | A vulnerability was found in SourceCodester Simple E-Learning System. It has been classified as critical. Affected is an unknown function of the file comment_frame.php. The manipulation of the argument post_id leads to sql injection. It is possible to launch the attack remotely. The exploit has been disclosed to the public and may be used. VDB-205818 is the identifier assigned to this vulnerability. | Published: 2022-08-08 | CVSS v3: 7.5 HIGH | Vector: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:L/A:L | AV:NETWORK AC:LOW PR:LOW UI:NONE S:UNCHANGED | Impact: C:LOW I:LOW A:LOW', 'CVE ID: CVE-2010-4528 | Affected Products: libpurple, pidgin | Vendors: pidgin | CWE Weaknesses: CWE-20 | CAPEC Attack Patterns: CAPEC-31, CAPEC-53, CAPEC-261, CAPEC-664, CAPEC-9, CAPEC-10, CAPEC-182, CAPEC-42, CAPEC-52, CAPEC-24 | MITRE ATT&CK Techniques: T1574.006, T1553.002, T1574.007, T1539, T1036.001, T1562.003, T1027 | MITRE ATT&CK Tactics: T1562, T1574, T1553, T1036 | Tags: capec_capec-261, capec_capec-53, tactic_t1574, technique_t1553.002, technique_t1574.007, capec_capec-31, tactic_t1553, mitre_attack, technique_t1574.006, tactic_t1562, capec', 'CVE ID: CVE-2025-66595 | Severity: MEDIUM | Affected Products: fast//tools | Vendors: yokogawa | CWE Weaknesses: CWE-352 | CAPEC Attack Patterns: CAPEC-467, CAPEC-111, CAPEC-462, CAPEC-62 | Tags: cross_site_request_forgery, capec_capec-111, capec, capec_capec-462, csrf, capec_capec-467', 'CVE ID: CVE-2020-9777 | An issue existed in the selection of video file by Mail. The issue was fixed by selecting the latest version of a video. This issue is fixed in iOS 13.4 and iPadOS 13.4. Cropped videos may not be shared properly via Mail. | Published: 2020-04-01 | CVSS v3: 5.3 MEDIUM | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:LOW I:NONE A:NONE | CVSS v2: 5.0 | AV:N/AC:L/Au:N/C:P/I:N/A:N', 'CVE ID: CVE-2024-57767 | MSFM before v2025.01.01 was discovered to contain a Server-Side Request Forgery (SSRF) via the component /file/download. | Published: 2025-01-15 | CVSS v3: 8.6 HIGH | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:N/A:N | AV:NETWORK AC:LOW PR:NONE UI:NONE S:CHANGED | Impact: C:HIGH I:NONE A:NONE', 'CVE ID: CVE-2019-10291 | Severity: HIGH | Affected Products: netsparker_cloud_scan | Vendors: jenkins | CWE Weaknesses: CWE-522 | CAPEC Attack Patterns: CAPEC-560, CAPEC-644, CAPEC-653, CAPEC-102, CAPEC-561, CAPEC-645, CAPEC-600, CAPEC-509, CAPEC-551, CAPEC-555 | MITRE ATT&CK Techniques: T1550.003, T1021.002, T1133, T1114.002, T1110.004, T1543, T1552.004, T1558.003, T1558, T1021, T1550.002, T1078 | MITRE ATT&CK Tactics: T1110, T1114, T1558, T1550, T1552, T1021 | Tags: technique_t1133, tactic_t1558, capec_capec-644, capec_capec-560, mitre_attack, technique_t1550.003, capec_capec-653, tactic_t1110, capec, tactic_t1114, technique_t1021.002', "CVE ID: CVE-2026-48266 | Title: Adobe Experience Manager versions 6.5.24, LTS SP1, 2026.04 and earlier are affected by a DOM-based Cross-Site Scripting (XSS) vulnerability. An attacker could e | Severity: MEDIUM | CVSS: 5.4 | Vector: CVSS:3.1/AV:N/AC:L/PR:L/UI:R/S:C/C:L/I:L/A:N | CWE Weaknesses: CWE-79 | Affected Products: adobe_experience_manager, experience_manager | Description: Adobe Experience Manager versions 6.5.24, LTS SP1, 2026.04 and earlier are affected by a DOM-based Cross-Site Scripting (XSS) vulnerability. An attacker could exploit this issue by manipulating the DOM environment to execute malicious JavaScript within the context of the victim's browser. Exploitation of this issue requires user interaction in that a victim must visit a crafted webpage. Scope is changed.", 'CVE ID: CVE-2008-6367 | Unrestricted file upload vulnerability in Photos/create_album.php in Social Groupie allows remote authenticated users to execute arbitrary code by uploading a file with an executable extension, then accessing it via a direct request to the file in Member_images/. | Published: 2009-03-02 | CVSS v2: 8.5 | AV:N/AC:M/Au:S/C:C/I:C/A:C', 'CVE ID: CVE-2016-3977 | Severity: MEDIUM | Affected Products: giflib, opensuse | Vendors: giflib_project, opensuse | CWE Weaknesses: CWE-119 | CAPEC Attack Patterns: CAPEC-14, CAPEC-45, CAPEC-9, CAPEC-123, CAPEC-10, CAPEC-44, CAPEC-8, CAPEC-42, CAPEC-46, CAPEC-47 | Tags: capec_capec-45, capec, capec_capec-14, capec_capec-9', 'CVE ID: CVE-2026-13939 | Severity: LOW | EPSS: 0.00222 | In KEV: False | Vendors/Products: {"google": ["chrome"]} | Weaknesses: CWE-20 | Created: 2026-06-30T22:38:28.760000Z | Updated: 2026-08-04T08:15:06.297286Z | Summary: Insufficient validation of untrusted input in WebShare in Google Chrome on Android prior to 150.0.7871.47 allowed a remote attacker who had compromised the renderer process to perform UI spoofing via a crafted HTML page. (Chromium security severity: Medium)', 'CVE ID: CVE-2014-1347 | Affected Products: itunes, mac_os_x | Vendors: apple | CWE Weaknesses: CWE-264 | Tags: operating_system', 'CVE ID: CVE-2017-0767 | Severity: HIGH | Affected Products: android | Vendors: google | CWE Weaknesses: CWE-120 | CAPEC Attack Patterns: CAPEC-14, CAPEC-92, CAPEC-45, CAPEC-9, CAPEC-10, CAPEC-44, CAPEC-8, CAPEC-42, CAPEC-46, CAPEC-47 | Tags: capec_capec-45, capec, capec_capec-14, capec_capec-92', 'CVE ID: CVE-2021-47577 | In the Linux kernel, the following vulnerability has been resolved: | Published: 2024-06-19 | CVSS v3: 4.7 MEDIUM | Vector: CVSS:3.1/AV:L/AC:H/PR:L/UI:N/S:U/C:N/I:N/A:H | AV:LOCAL AC:HIGH PR:LOW UI:NONE S:UNCHANGED | Impact: C:NONE I:NONE A:HIGH', "CVE ID: CVE-2025-4219 | The DPEPress plugin for WordPress is vulnerable to Stored Cross-Site Scripting via the plugin's 'dpe' shortcode in all versions up to, and including, 0.3 due to insufficient input sanitization and output escaping on user supplied attributes. This makes it possible for authenticated attackers, with contributor-level access and above, to inject arbitrary web scripts in pages that will execute whenever a user accesses an injected page. | Published: 2025-05-21 | CVSS v3: 6.4 MEDIUM | Vector: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N | AV:NETWORK AC:LOW PR:LOW UI:NONE S:CHANGED | Impact: C:LOW I:LOW A:NONE", "CVE ID: CVE-2023-3651 | Improper Neutralization of Special Elements used in an SQL Command ('SQL Injection') vulnerability in Digital Ant E-Commerce Software allows SQL Injection.This issue affects E-Commerce Software: before 11. | Published: 2023-08-08 | CVSS v3: 9.8 CRITICAL | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:HIGH A:HIGH", 'CVE ID: CVE-2016-2209 | Severity: HIGH | Affected Products: message_gateway_for_service_providers, norton_360, norton_security_with_backup, message_gateway, mail_security_for_domino, advanced_threat_protection, norton_power_eraser, norton_internet_security, norton_bootable_removal_tool, mail_security_for_microsoft_exchange, protection_for_sharepoint_servers, norton_security, protection_engine, ngc, data_center_security_server, endpoint_protection, csapi, macos, norton_antivirus, linux_kernel | Vendors: apple, linux, symantec | CWE Weaknesses: CWE-119 | CAPEC Attack Patterns: CAPEC-14, CAPEC-45, CAPEC-9, CAPEC-123, CAPEC-10, CAPEC-44, CAPEC-8, CAPEC-42, CAPEC-46, CAPEC-47 | Tags: capec_capec-9, capec_capec-14, web_application, operating_system, capec_capec-45, capec', "CVE ID: CVE-2019-0774 | An information disclosure vulnerability exists when the Windows GDI component improperly discloses the contents of its memory, aka 'Windows GDI Information Disclosure Vulnerability'. This CVE ID is unique from CVE-2019-0614. | Published: 2019-04-09 | CVSS v3: 6.5 MEDIUM | Vector: CVSS:3.0/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N | AV:NETWORK AC:LOW PR:NONE UI:REQUIRED S:UNCHANGED | Impact: C:HIGH I:NONE A:NONE | CVSS v2: 4.3 | AV:N/AC:M/Au:N/C:P/I:N/A:N", 'CVE ID: CVE-2026-19983 | Severity: HIGH | Weaknesses: CWE-77, CWE-78 | Published: 2026-08-17T05:17:09.947 | Summary: A vulnerability was detected in GL.iNet A1300, AX1800, AXT1800, MT2500, MT3000, MT6000, X3000 and XE3000 4.8.x. This issue affects some unknown processing of the file /usr/bin/gl_nas_sys of the component NAS Command Service. The manipulation results in os command injection. The attack may be launched remotely. Upgrading to version 4.9.0 is capable of addressing this issue. It is suggested to upgrade the affected component. The vendor explains: "After our investigation, we have confirmed that the', 'CVE ID: CVE-2017-17973 | Severity: HIGH | Affected Products: libtiff | Vendors: libtiff | CWE Weaknesses: CWE-416', 'CVE ID: CVE-2022-37098 | H3C H200 H200V100R004 was discovered to contain a stack overflow via the function UpdateIpv6Params. | Published: 2022-08-25 | CVSS v3: 9.8 CRITICAL | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:HIGH A:HIGH', 'CVE ID: CVE-2004-2249 | Unknown vulnerability in the "access code" in SecureEditor before 0.1.2 has unknown impact and attack vectors, possibly involving a bypass of IP address restrictions. | Published: 2004-12-31 | CVSS v2: 7.5 | AV:N/AC:L/Au:N/C:P/I:P/A:P', 'CVE ID: CVE-2025-61654 | CWE Weaknesses: CWE-22 | CAPEC Attack Patterns: CAPEC-78, CAPEC-126, CAPEC-76, CAPEC-79, CAPEC-64 | Tags: capec_capec-126, capec, capec_capec-78, capec_capec-76', 'CVE ID: CVE-2023-37223 | Cross Site Scripting (XSS) vulnerability in Archer Platform before v.6.13 and fixed in v.6.12.0.6 and v.6.13.0 allows a remote authenticated attacker to execute arbitrary code via a crafted malicious script. | Published: 2023-07-14 | CVSS v3: 5.4 MEDIUM | Vector: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:L/A:N | AV:NETWORK AC:LOW PR:LOW UI:NONE S:UNCHANGED | Impact: C:LOW I:LOW A:NONE', 'CVE ID: CVE-2023-27963 | The issue was addressed with additional permissions checks. This issue is fixed in macOS Ventura 13.3, iOS 16.4 and iPadOS 16.4, iOS 15.7.4 and iPadOS 15.7.4, macOS Monterey 12.6.4, tvOS 16.4, watchOS 9.4. A shortcut may be able to use sensitive data with certain actions without prompting the user. | Published: 2023-05-08 | CVSS v3: 7.5 HIGH | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:NONE A:NONE', 'CVE ID: CVE-2017-12194 | A flaw was found in the way spice-client processed certain messages sent from the server. An attacker, having control of malicious spice-server, could use this flaw to crash the client or execute arbitrary code with permissions of the user running the client. spice-gtk versions through 0.34 are believed to be vulnerable. | Published: 2018-03-14 | CVSS v3: 9.8 CRITICAL | Vector: CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:HIGH A:HIGH | CVSS v2: 10.0 | AV:N/AC:L/Au:N/C:C/I:C/A:C', 'CVE ID: CVE-2005-0878 | Cross-site scripting (XSS) vulnerability in MercuryBoard before 1.1.3 allows remote attackers to inject arbitrary web script or HTML via the title field of a PM (private message). | Published: 2005-03-23 | CVSS v2: 4.3 | AV:N/AC:M/Au:N/C:N/I:P/A:N', 'CVE ID: CVE-2023-29003 | SvelteKit is a web development framework. The SvelteKit framework offers developers an option to create simple REST APIs. This is done by defining a `+server.js` file, containing endpoint handlers for different HTTP methods. | Published: 2023-04-04 | CVSS v3: 8.8 HIGH | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H | AV:NETWORK AC:LOW PR:NONE UI:REQUIRED S:UNCHANGED | Impact: C:HIGH I:HIGH A:HIGH', 'CVE ID: CVE-2025-30785 | CWE Weaknesses: CWE-98 | CAPEC Attack Patterns: CAPEC-193 | Tags: capec, capec_capec-193', 'CVE ID: CVE-2018-13798 | Severity: HIGH | Affected Products: sicam_a8000_cp-8000, sicam_a8000_cp-8000_firmware, sicam_a8000_cp-8050, sicam_a8000_cp-8050_firmware, sicam_a8000_cp-802x_firmware, sicam_a8000_cp-802x | Vendors: siemens | CWE Weaknesses: CWE-20 | CAPEC Attack Patterns: CAPEC-31, CAPEC-53, CAPEC-261, CAPEC-664, CAPEC-9, CAPEC-10, CAPEC-182, CAPEC-42, CAPEC-52, CAPEC-24 | MITRE ATT&CK Techniques: T1574.006, T1553.002, T1574.007, T1539, T1036.001, T1562.003, T1027 | MITRE ATT&CK Tactics: T1562, T1574, T1553, T1036 | Tags: capec_capec-261, capec_capec-53, tactic_t1574, technique_t1553.002, technique_t1574.007, capec_capec-31, tactic_t1553, mitre_attack, technique_t1574.006, tactic_t1562, capec', 'CVE ID: CVE-2017-0808 | An information disclosure vulnerability in the Android framework (file system). Product: Android. Versions: 7.0, 7.1.1, 7.1.2, 8.0. Android ID: A-62301183. | Published: 2017-10-04 | CVSS v3: 7.5 HIGH | Vector: CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:NONE A:NONE | CVSS v2: 5.0 | AV:N/AC:L/Au:N/C:P/I:N/A:N', 'CVE ID: CVE-2018-17590 | AirTies Air 5442 devices with software 1.0.0.18 have XSS via the top.html productboardtype parameter. | Published: 2018-10-02 | CVSS v3: 6.1 MEDIUM | Vector: CVSS:3.0/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N | AV:NETWORK AC:LOW PR:NONE UI:REQUIRED S:CHANGED | Impact: C:LOW I:LOW A:NONE | CVSS v2: 4.3 | AV:N/AC:M/Au:N/C:N/I:P/A:N', 'CVE ID: CVE-2005-1173 | Affected Products: simple_web_server | Vendors: pmsoftware | Tags: web_application', 'CVE ID: CVE-2023-0469 | A use-after-free flaw was found in io_uring/filetable.c in io_install_fixed_file in the io_uring subcomponent in the Linux Kernel during call cleanup. This flaw may lead to a denial of service. | Published: 2023-01-26 | CVSS v3: 5.5 MEDIUM | Vector: CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H | AV:LOCAL AC:LOW PR:LOW UI:NONE S:UNCHANGED | Impact: C:NONE I:NONE A:HIGH', 'CVE ID: CVE-2025-61806 | Substance3D - Stager versions 3.1.4 and earlier are affected by an out-of-bounds read vulnerability when parsing a crafted file, which could result in a read past the end of an allocated memory structure. An attacker could leverage this vulnerability to execute code in the context of the current user. Exploitation of this issue requires user interaction in that a victim must open a malicious file. | Published: 2025-10-14 | CVSS v3: 7.8 HIGH | Vector: CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H | AV:LOCAL AC:LOW PR:NONE UI:REQUIRED S:UNCHANGED | Impact: C:HIGH I:HIGH A:HIGH', "CVE ID: CVE-2019-19340 | A flaw was found in Ansible Tower, versions 3.6.x before 3.6.2 and 3.5.x before 3.5.3, where enabling RabbitMQ manager by setting it with '-e rabbitmq_enable_manager=true' exposes the RabbitMQ management interface publicly, as expected. If the default admin user is still active, an attacker could guess the password and gain access to the system. | Published: 2019-12-19 | CVSS v3: 8.2 HIGH | Vector: CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:L | AV:NETWORK AC:LOW PR:NONE UI:NONE S:UNCHANGED | Impact: C:HIGH I:NONE A:LOW | CVSS v2: 6.4 | AV:N/AC:L/Au:N/C:P/I:N/A:P", 'CVE ID: CVE-2024-8734 | Severity: MEDIUM | Affected Products: lucas_string_replace | Vendors: lucasstad | CWE Weaknesses: CWE-79 | CAPEC Attack Patterns: CAPEC-209, CAPEC-591, CAPEC-592, CAPEC-85, CAPEC-63, CAPEC-588 | Tags: xss, cross_site_scripting, capec_capec-209, capec_capec-592, capec_capec-591, capec', 'CVE ID: CVE-2007-1198 | Cross-site scripting (XSS) vulnerability in TaskFreak! before 0.5.7 allows remote attackers to inject arbitrary web script or HTML via unspecified vectors, possibly a variant of CVE-2007-0982. | Published: 2007-03-02 | CVSS v2: 4.3 | AV:N/AC:M/Au:N/C:N/I:P/A:N', 'CVE ID: CVE-2005-0480 | Cross-site scripting (XSS) vulnerability in TrackerCam 5.12 and earlier allows remote attackers to inject arbitrary HTML or web script via the login request, which is recorded in a log file but not properly handled when the administrator views the log file. | Published: 2005-03-30 | CVSS v2: 4.3 | AV:N/AC:M/Au:N/C:N/I:P/A:N', 'CVE ID: CVE-2021-33755 | Severity: MEDIUM | Affected Products: windows_server_2019, windows_10, windows_server_2016 | Vendors: microsoft | Tags: web_application', 'CVE ID: CVE-2026-30048 | A stored cross-site scripting (XSS) vulnerability exists in the NotChatbot WebChat widget thru 1.4.4. User-supplied input is not properly sanitized before being stored and rendered in the chat conversation history. This allows an attacker to inject arbitrary JavaScript code which is executed when the chat history is reloaded. The issue is reproducible across multiple independent implementations of the widget, indicating that the vulnerability resides in the product itself rather than in a specific website configuration. | Published: 2026-03-18 | CVSS v3: 5.4 MEDIUM | Vector: CVSS:3.1/AV:N/AC:L/PR:L/UI:R/S:C/C:L/I:L/A:N | AV:NETWORK AC:LOW PR:LOW UI:REQUIRED S:CHANGED | Impact: C:LOW I:LOW A:NONE', 'CVE ID: CVE-2016-0683 | Severity: MEDIUM | Affected Products: peoplesoft_enterprise_peopletools | Vendors: oracle', 'CVE ID: CVE-2026-23256 | In the Linux kernel, the following vulnerability has been resolved: | Published: 2026-03-18', 'CVE ID: CVE-2015-9019 | Severity: MEDIUM | Affected Products: libxslt | Vendors: xmlsoft | CWE Weaknesses: CWE-330 | CAPEC Attack Patterns: CAPEC-485, CAPEC-59, CAPEC-112 | MITRE ATT&CK Techniques: T1110, T1552.004 | MITRE ATT&CK Tactics: T1552 | Tags: tactic_t1552, capec_capec-112, capec_capec-485, mitre_attack, technique_t1110, capec, capec_capec-59, technique_t1552.004', 'CVE ID: CVE-2006-2179 | Multiple SQL injection vulnerabilities in CyberBuild allow remote attackers to execute arbitrary SQL commands via the (1) SessionID parameter to login.asp or (2) ProductIndex parameter to browse0.htm. | Published: 2006-05-04 | CVSS v2: 7.5 | AV:N/AC:L/Au:N/C:P/I:P/A:P']


def _make_rerank_call(ports_cycle):
    def call(i):
        port = next(ports_cycle)
        # đúng hành vi production (qdrant.py: candidates = merged[:20]) — cắt cửa sổ
        # 20 tài liệu trượt dần qua pool 60 văn bản thật để có biến thiên nội dung
        start = (i * 7) % (len(REAL_KB_DOCS) - 20)
        docs = REAL_KB_DOCS[start:start + 20]
        body = {"model": RERANK_MODEL, "query": f"injection vulnerability {i}", "documents": docs}
        _, ms = post_json(port, "/v1/rerank", body, timeout=30)
        return ms
    return call


def run_latency_and_throughput(call_fn, seq_n, concs):
    seq_lat = [call_fn(i) for i in range(seq_n)]
    seq = {"n": seq_n, "p50_ms": pctl(seq_lat, 50), "p95_ms": pctl(seq_lat, 95), "p99_ms": pctl(seq_lat, 99),
           "mean_ms": round(statistics.mean(seq_lat), 2), "min_ms": round(min(seq_lat), 2), "max_ms": round(max(seq_lat), 2)}
    throughput, conc_latency = {}, {}
    for conc in concs:
        n = conc * 3
        lats = []
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=conc) as ex:
            for ms in ex.map(call_fn, range(n)):
                lats.append(ms)
        dt = time.time() - t0
        throughput[str(conc)] = {"n": n, "seconds": round(dt, 3), "per_s": round(n / dt, 1) if dt else None}
        if conc == concs[-1]:
            conc_latency = {"concurrency": conc, "n": n, "p50_ms": pctl(lats, 50), "p95_ms": pctl(lats, 95),
                             "p99_ms": pctl(lats, 99), "mean_ms": round(statistics.mean(lats), 2)}
    return {"sequential_latency": seq, "throughput_by_concurrency": throughput, "latency_under_load": conc_latency}


def run_embed_rerank():
    embed_ports = itertools.cycle([EMBED_PORT_BASE + g for g in TARGET_GPUS])
    rerank_ports = itertools.cycle([RERANK_PORT_BASE + g for g in TARGET_GPUS])
    return {
        "embedding": run_latency_and_throughput(_make_embed_call(embed_ports), seq_n=30, concs=[4, 8, 16]),
        "reranker": run_latency_and_throughput(_make_rerank_call(rerank_ports), seq_n=30, concs=[2, 4, 8]),
    }


# Load test / CCU ramp KHÔNG đo ở đây nữa — xem bench_fullpipeline.py (đo qua toàn bộ
# hệ thống thật /ai/chat, 90 câu hỏi khác nhau từ gold-set độc lập, đúng 30 CCU).
# Trước đây file này có 1 bản raw-LLM-bypass ramp riêng (6 câu xoay vòng) — đã bỏ vì
# trùng lặp với bench_fullpipeline.py và dùng workload kém đại diện hơn, gây nhầm lẫn
# khi có 2 con số "load test" khác nhau trong cùng bộ bằng chứng.


# ══ main ═════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    out = {
        "meta": {"generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "target_gpus": TARGET_GPUS,
                 "note": "Đo TẠI-HOST (loopback trong instance) — không qua SSH-tunnel/WAN. Load test/CCU ramp xem bench_fullpipeline.py."},
        "llm": run_llm_tps_ttft(),
        **run_embed_rerank(),
    }
    print("BENCH_FULL_RESULT " + json.dumps(out, ensure_ascii=False))
