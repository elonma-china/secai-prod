#!/usr/bin/env python3
"""Sinh sample eval cho từng chức năng của secai-agent-gateway. Deterministic (seed cố
định) để tái lập được. Chạy: python3 gen_samples.py [--n 100]

exploit.jsonl là quan trọng nhất: CVE lấy THẬT từ poc-catalog.db (không bịa), chia theo
category để biết kỳ vọng gì (anti-hallucination check dựa trên category, không phải đoán).
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent
CATALOG_DB = Path("/home/ict/AIEngines/secai/secai-data/poc/catalog.db")
SEED = 20260823

# CVE nổi tiếng, gần như chắc chắn có PoC công khai — kỳ vọng tier real/real_consolidated.
CURATED_KNOWN_CVES = [
    "CVE-2021-44228",  # Log4Shell
    "CVE-2014-0160",   # Heartbleed
    "CVE-2014-6271",   # Shellshock
    "CVE-2017-0144",   # EternalBlue
    "CVE-2019-0708",   # BlueKeep
    "CVE-2020-1472",   # ZeroLogon
    "CVE-2021-34527",  # PrintNightmare
    "CVE-2021-4034",   # PwnKit
    "CVE-2022-0847",   # Dirty Pipe
    "CVE-2022-22965",  # Spring4Shell
    "CVE-2022-30190",  # Follina
    "CVE-2022-26134",  # Confluence OGNL
    "CVE-2023-22515",  # Confluence broken access
    "CVE-2023-34362",  # MOVEit
    "CVE-2023-38408",  # OpenSSH
    "CVE-2017-5638",   # Struts2
    "CVE-2018-7600",   # Drupalgeddon2
    "CVE-2021-34473",  # ProxyShell
    "CVE-2020-0796",   # SMBGhost
    "CVE-2019-19781",  # Citrix ADC
    "CVE-2021-21972",  # vCenter
    "CVE-2022-1388",   # F5 BIG-IP
    "CVE-2023-27997",  # FortiOS
    "CVE-2024-3094",   # xz backdoor
    "CVE-2024-27198",  # JetBrains TeamCity
]

# CVE hợp lệ về cú pháp nhưng KHÔNG tồn tại thật — must-not-hallucinate.
FAKE_CVES = [
    "CVE-2026-99999", "CVE-2020-99998", "CVE-2015-99997", "CVE-2019-88888",
    "CVE-2023-77777", "CVE-2010-99999", "CVE-2026-00001", "CVE-2018-99123",
    "CVE-2005-99999", "CVE-2027-00042",
]

# _CVE_ANY (routes.py) = r"CVE-\d{4}-\d{3,7}" -- CHỈ trigger validate khi tìm thấy đúng hình
# dạng đó trước (năm 4 số + 3-7 số). "CVE-24-1234" (năm 2 số) hay "CVE-2024-abcde" (không có
# digit sau năm) KHÔNG match _CVE_ANY -> app coi như "không có CVE", KHÔNG phải bug -- nên mọi
# sample dưới đây phải có đủ 4-số-năm + >=3-số-id để app THẤY được rồi mới đánh giá là sai định
# dạng (hậu tố chữ ngay sau chuỗi số).
INVALID_FORMAT = [
    "CVE-2024-1234zz cho tôi PoC",
    "CVE-2026-4253x là lỗ hổng gì, cho payload",
    "cho tôi exploit CVE-2022-1234extra",
    "Tìm giúp tôi PoC của CVE-2025-9999x",
    "CVE-2027-10000q có payload gì không",
]

CHITCHAT = [
    "xin chào", "chào bạn", "hello", "bạn là ai", "bạn tên gì",
    "bạn khoẻ không", "giúp tôi với", "hướng dẫn sử dụng",
    "cảm ơn bạn", "hi there",
]

NATURAL_SENTENCE_TEMPLATES = [
    "Tôi cần PoC khai thác lỗ hổng {cve} trên hệ thống Java của công ty, có payload mẫu không?",
    "Hệ thống chúng tôi có thể dính {cve}, bạn phân tích và cho exploit demo được không?",
    "Cho tôi cách khai thác {cve} để pentest nội bộ (đã được ủy quyền).",
    "{cve} là lỗ hổng gì, có script khai thác public không?",
]

MULTI_CVE_TEMPLATES = [
    "So sánh {a} và {b}, cái nào dễ khai thác hơn và cho PoC của {a}.",
    "Tôi nghi ngờ server dính cả {a} lẫn {b}, ưu tiên phân tích {b} trước.",
]

ENGLISH_INPUT_TEMPLATE = "Give me a working proof-of-concept exploit for {cve}, I need the payload."


def fetch_catalog_cves(kind: str, n: int, rng: random.Random) -> list[str]:
    """kind: 'artifact' (has downloaded PoC code -> T1 candidate) or 'desc_only'
    (description/README only, no code artifact -> T2/T3 candidate)."""
    con = sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True)
    cur = con.cursor()
    if kind == "artifact":
        rows = cur.execute("""
            SELECT DISTINCT pc.cve_id FROM poc_cve pc JOIN poc p ON p.id = pc.poc_id
            WHERE p.artifact_path IS NOT NULL AND p.artifact_path != ''
              AND pc.cve_id LIKE 'CVE-20%'
        """).fetchall()
    else:
        rows = cur.execute("""
            SELECT DISTINCT pc.cve_id FROM poc_cve pc JOIN poc p ON p.id = pc.poc_id
            WHERE (p.artifact_path IS NULL OR p.artifact_path = '')
              AND p.description IS NOT NULL AND length(p.description) > 50
              AND pc.cve_id LIKE 'CVE-20%'
        """).fetchall()
    con.close()
    ids = sorted({r[0] for r in rows})
    rng.shuffle(ids)
    return ids[:n]


def gen_exploit_samples(n: int, rng: random.Random) -> list[dict]:
    samples: list[dict] = []
    curated = list(CURATED_KNOWN_CVES)
    rng.shuffle(curated)
    n_curated = min(25, len(curated))
    n_fake = min(10, len(FAKE_CVES))
    n_invalid = min(5, len(INVALID_FORMAT))
    n_chitchat = min(5, len(CHITCHAT))
    n_natural = 10
    n_multi = 5
    n_english = 5
    reserved = n_curated + n_fake + n_invalid + n_chitchat + n_natural + n_multi + n_english
    remaining = max(0, n - reserved)
    n_artifact = remaining // 2 + remaining % 2
    n_desc = remaining // 2

    for cve in curated[:n_curated]:
        samples.append({"category": "curated_known", "cve_id": cve,
                         "message": f"Cho tôi PoC khai thác {cve}", "expected_tier": "real_or_synth"})
    for cve in fetch_catalog_cves("artifact", n_artifact, rng):
        samples.append({"category": "catalog_with_artifact", "cve_id": cve,
                         "message": f"Cho tôi PoC khai thác {cve}", "expected_tier": "real_or_synth"})
    for cve in fetch_catalog_cves("desc_only", n_desc, rng):
        samples.append({"category": "catalog_desc_only", "cve_id": cve,
                         "message": f"Cho tôi PoC khai thác {cve}", "expected_tier": "any_grounded"})
    for cve in FAKE_CVES[:n_fake]:
        samples.append({"category": "fake_cve", "cve_id": cve,
                         "message": f"Cho tôi PoC khai thác {cve}", "expected_tier": "not_found"})
    for msg in INVALID_FORMAT[:n_invalid]:
        samples.append({"category": "invalid_format", "cve_id": None,
                         "message": msg, "expected_tier": "format_error"})
    for msg in CHITCHAT[:n_chitchat]:
        samples.append({"category": "chitchat", "cve_id": None,
                         "message": msg, "expected_tier": "chitchat"})
    pool = curated[n_curated:] + FAKE_CVES[n_fake:]
    for i in range(n_natural):
        cve = pool[i % len(pool)] if pool else "CVE-2021-44228"
        tmpl = NATURAL_SENTENCE_TEMPLATES[i % len(NATURAL_SENTENCE_TEMPLATES)]
        samples.append({"category": "natural_sentence", "cve_id": cve,
                         "message": tmpl.format(cve=cve), "expected_tier": "any_grounded"})
    for i in range(n_multi):
        a, b = curated[i % len(curated)], curated[(i + 7) % len(curated)]
        tmpl = MULTI_CVE_TEMPLATES[i % len(MULTI_CVE_TEMPLATES)]
        samples.append({"category": "multi_cve", "cve_id": a,
                         "message": tmpl.format(a=a, b=b), "expected_tier": "any_grounded"})
    for i in range(n_english):
        cve = curated[(i + 3) % len(curated)]
        samples.append({"category": "english_input", "cve_id": cve,
                         "message": ENGLISH_INPUT_TEMPLATE.format(cve=cve), "expected_tier": "any_grounded"})

    rng.shuffle(samples)
    for i, s in enumerate(samples):
        s["id"] = f"exploit-{i:03d}"
    return samples[:n]


CHAT_TOPICS = [
    "Tóm tắt lỗ hổng {cve} và mức độ nghiêm trọng.",
    "CWE-{cwe} là loại lỗ hổng gì, cho ví dụ.",
    "So sánh SQL Injection và XSS về cơ chế khai thác.",
    "OWASP Top 10 2026 gồm những hạng mục nào.",
    "Giải thích kỹ thuật khai thác {cve} ở mức khái niệm (không cần payload).",
    "Làm sao phát hiện một hệ thống có dính {cve} hay không.",
    "CAPEC là gì và khác CWE ở điểm nào.",
    "Zero-day khác với N-day như thế nào.",
    "Giải thích MITRE ATT&CK technique T1059.",
    "Best practice vá lỗ hổng {cve} là gì.",
    "Container escape là gì, cho ví dụ CVE liên quan.",
    "Giải thích khái niệm CVSS score và cách tính.",
]
CHAT_CVE_POOL = CURATED_KNOWN_CVES
CHAT_CWE_POOL = ["79", "89", "22", "78", "94", "352", "434", "502", "611", "918"]


def gen_chat_samples(n: int, rng: random.Random) -> list[dict]:
    samples = []
    i = 0
    while len(samples) < n:
        tmpl = CHAT_TOPICS[i % len(CHAT_TOPICS)]
        cve = CHAT_CVE_POOL[i % len(CHAT_CVE_POOL)]
        cwe = CHAT_CWE_POOL[i % len(CHAT_CWE_POOL)]
        msg = tmpl.format(cve=cve, cwe=cwe) if "{" in tmpl else tmpl
        # nhích message cho khác nhau khi lặp template (i lớn hơn số template)
        if i >= len(CHAT_TOPICS):
            msg = f"{msg} (câu hỏi #{i})"
        samples.append({
            "id": f"chat-{len(samples):03d}", "category": "topic_qa", "message": msg,
            "mentions_cve": cve if "{cve}" in tmpl else None,
            "mentions_cwe": cwe if "{cwe}" in tmpl else None,
        })
        i += 1
    return samples


TRANSLATE_PAIRS = [
    ("Lỗ hổng cho phép kẻ tấn công thực thi mã tùy ý từ xa thông qua tham số chưa được kiểm tra.", "vi", "en"),
    ("This vulnerability allows remote code execution via an unauthenticated endpoint.", "en", "vi"),
    ("Kẻ tấn công có thể chiếm quyền quản trị bằng cách khai thác lỗi leo thang đặc quyền.", "vi", "en"),
    ("The attacker can bypass authentication using a crafted JWT token.", "en", "vi"),
    ("Cần vá bản cập nhật bảo mật ngay lập tức để tránh bị khai thác trong thực tế.", "vi", "en"),
    ("A patch should be applied immediately to prevent real-world exploitation.", "en", "vi"),
    ("Lỗ hổng SQL Injection cho phép đọc toàn bộ cơ sở dữ liệu người dùng.", "vi", "en"),
    ("Cross-site scripting lets an attacker run scripts in a victim's browser.", "en", "vi"),
    ("Hệ thống phòng thủ nhiều lớp giúp giảm thiểu rủi ro khi một lớp bị xuyên thủng.", "vi", "en"),
    ("Zero trust architecture assumes no implicit trust between network segments.", "en", "vi"),
]


def gen_translate_samples(n: int, rng: random.Random) -> list[dict]:
    samples = []
    i = 0
    while len(samples) < n:
        text, src, tgt = TRANSLATE_PAIRS[i % len(TRANSLATE_PAIRS)]
        samples.append({
            "id": f"translate-{len(samples):03d}", "category": "explicit_target",
            "message": text, "source_lang": src, "target_lang": tgt,
        })
        i += 1
    return samples[:n]


REPORT_SECTIONS_VARIANTS = [
    ["overview", "details", "recommendations"],
    ["tóm tắt", "chi tiết", "khuyến nghị"],
    ["executive summary", "findings", "remediation"],
]
REPORT_LOGS = [
    "14:02 login failed user=admin ip=10.0.0.5 x5; 14:03 login success user=admin ip=10.0.0.5; "
    "14:05 outbound connection to 45.33.32.156:4444 from web-01.",
    "Scanner phát hiện cổng 3389 (RDP) mở ra Internet trên host fin-app-02, không có MFA.",
    "WAF log: 200+ request SQLMap signature trong 5 phút từ 1 IP, endpoint /api/search?q=.",
    "Container image scan: base image chứa 12 CVE mức CRITICAL, 34 mức HIGH, chưa có bản vá.",
    "SIEM alert: process powershell.exe -enc <base64> spawn từ winword.exe trên máy kế toán.",
]


def gen_report_samples(n: int, rng: random.Random) -> list[dict]:
    samples = []
    i = 0
    while len(samples) < n:
        logs = REPORT_LOGS[i % len(REPORT_LOGS)]
        sections = REPORT_SECTIONS_VARIANTS[i % len(REPORT_SECTIONS_VARIANTS)]
        samples.append({
            "id": f"report-{len(samples):03d}", "category": "incident_log",
            "logs": logs, "sections": sections,
        })
        i += 1
    return samples[:n]


def gen_statistical_samples(n: int, rng: random.Random) -> list[dict]:
    samples = []
    i = 0
    while len(samples) < n:
        samples.append({
            "id": f"statistical-{len(samples):03d}", "category": "period_summary",
            "from_date": "2026-01-01", "to_date": "2026-08-01",
            "summary": f"Tổng hợp {30 + i} sự cố bảo mật, {5 + (i % 4)} mức CRITICAL đã xử lý.",
            "detail": REPORT_LOGS[i % len(REPORT_LOGS)],
        })
        i += 1
    return samples[:n]


# bughunt: CHỦ ĐỘNG scope xuống, KHÔNG bắn scan thật vào host tuỳ ý/không được uỷ quyền.
# Chỉ nhắm vào dịch vụ CỤC BỘ do chính hạ tầng secai sở hữu (gateway/qdrant/searxng),
# kiểm tra tính đúng đắn của luồng/pipeline (phases, SSE, không crash) chứ không phải
# "độ chính xác tìm lỗ hổng" trên internet thật. Xem README.md phần Scope.
BUGHUNT_LOCAL_TARGETS = [
    "http://127.0.0.1:8131", "http://127.0.0.1:6333", "http://127.0.0.1:7474",
]


def gen_bughunt_samples(n: int, rng: random.Random) -> list[dict]:
    samples = []
    n = min(n, 12)  # xem README — không mở rộng ra scan target bên ngoài
    for i in range(n):
        target = BUGHUNT_LOCAL_TARGETS[i % len(BUGHUNT_LOCAL_TARGETS)]
        samples.append({
            "id": f"bughunt-{i:03d}", "category": "local_authorized",
            "target": target, "scope_note": "self-owned local infra, contract test only",
            "run_cve_sweep": False, "enable_exploit": False,
        })
    return samples


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  {path.name}: {len(rows)} samples")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    args = ap.parse_args()
    rng = random.Random(SEED)
    out = HERE / "samples"
    out.mkdir(exist_ok=True)
    print("Sinh samples...")
    write_jsonl(out / "exploit.jsonl", gen_exploit_samples(args.n, rng))
    write_jsonl(out / "chat.jsonl", gen_chat_samples(args.n, rng))
    write_jsonl(out / "translate.jsonl", gen_translate_samples(args.n, rng))
    write_jsonl(out / "report.jsonl", gen_report_samples(args.n, rng))
    write_jsonl(out / "statistical_report.jsonl", gen_statistical_samples(args.n, rng))
    write_jsonl(out / "bughunt.jsonl", gen_bughunt_samples(args.n, rng))


if __name__ == "__main__":
    main()
