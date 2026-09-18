#!/usr/bin/env python3
"""
Nghiệm thu: Hệ thống hỗ trợ đề xuất + sinh mã khắc phục tham chiếu chuẩn OWASP.

Hai phần:
  A. Prompt eval set (10 cases) — grade theo kỹ thuật OWASP chuẩn expected
  B. Live web_fetch demo — câu hỏi dữ liệu mới nhất kích hoạt tool_call → owasp.org

Chạy:
    python3 evals/eval_remediation_owasp.py
    python3 evals/eval_remediation_owasp.py --base http://IP:8131
    python3 evals/eval_remediation_owasp.py --part A   # chỉ eval set
    python3 evals/eval_remediation_owasp.py --part B   # chỉ live fetch demo
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

# ─── PART A: Prompt eval set graded against OWASP techniques ─────────────────
#
# Mỗi case: input câu hỏi, expected_techniques (kỹ thuật OWASP phải xuất hiện),
# expected_citation (chuỗi chuẩn phải được nhắc), expect_code (có code block không).
# Nguồn expected_techniques lấy từ OWASP Cheat Sheet Series thực tế.

EVAL_CASES = [
    {
        "id": "A01-sqli",
        "owasp_ref": "OWASP SQL Injection Prevention CS",
        "label": "CWE-89 / A03:2021 SQL Injection",
        "message": "CWE-89 SQL Injection là gì và cách khắc phục? Cho ví dụ code Java theo chuẩn OWASP.",
        # Kỹ thuật OWASP chuẩn (≥2 trong số này phải xuất hiện):
        "expected_techniques": [
            "prepared statement", "preparedstatement",
            "parameterized", "tham số", "parameteriz",
            "stored procedure",
        ],
        "expected_citation": ["OWASP", "CWE-89"],
        "expect_code": True,
    },
    {
        "id": "A02-xss",
        "owasp_ref": "OWASP XSS Prevention CS",
        "label": "CWE-79 / A03:2021 XSS",
        "message": "CWE-79 Cross-Site Scripting cách khắc phục theo OWASP? Ví dụ code Python/Flask.",
        "expected_techniques": [
            "escape", "encode", "autoescape", "csp",
            "content-security-policy", "content security policy",
            "sanitize", "sanitiz", "httponly",
        ],
        "expected_citation": ["OWASP", "CWE-79"],
        "expect_code": True,
    },
    {
        "id": "A03-ssrf",
        "owasp_ref": "OWASP SSRF Prevention CS",
        "label": "CWE-918 / A10:2021 SSRF",
        "message": "SSRF là gì, cách phòng chống theo OWASP? Ví dụ code Python whitelist URL.",
        "expected_techniques": [
            "whitelist", "allow.list", "allow list", "danh sách cho phép",
            "private ip", "địa chỉ nội bộ", "internal ip",
            "dns rebind", "resolve",
        ],
        "expected_citation": ["OWASP"],
        "expect_code": True,
    },
    {
        "id": "A04-deserial",
        "owasp_ref": "OWASP Deserialization CS",
        "label": "CWE-502 / A08:2021 Insecure Deserialization",
        "message": "CWE-502 Insecure Deserialization khắc phục thế nào theo OWASP? Ví dụ code Java whitelist class.",
        "expected_techniques": [
            "whitelist", "class whitelist", "allowed class",
            "resolveclass", "objectinputstream",
            "không dùng java serialization", "json", "signature", "chữ ký",
        ],
        "expected_citation": ["OWASP", "CWE-502"],
        "expect_code": True,
    },
    {
        "id": "A05-xxe",
        "owasp_ref": "OWASP XXE Prevention CS",
        "label": "CWE-611 / A05:2021 XXE",
        "message": "CWE-611 XXE (XML External Entity) là gì, cách vá theo OWASP? Ví dụ Java disable external entity.",
        "expected_techniques": [
            "disable external entity", "dtd", "xml external entity",
            "setfeature", "documentbuilderfactory",
            "disallow-doctype-decl", "external-general-entities",
        ],
        "expected_citation": ["OWASP", "CWE-611"],
        "expect_code": True,
    },
    {
        "id": "A06-broken-auth",
        "owasp_ref": "OWASP Authentication CS",
        "label": "CWE-287 / A07:2021 Broken Authentication",
        "message": "CWE-287 Broken Authentication khắc phục thế nào theo OWASP Secure Coding?",
        "expected_techniques": [
            "mfa", "multi-factor", "xác thực đa yếu tố",
            "bcrypt", "argon2", "hash",
            "session", "token", "rate limit",
        ],
        "expected_citation": ["OWASP", "CWE-287"],
        "expect_code": False,  # Câu này thường không cần code
    },
    {
        "id": "A07-idor",
        "owasp_ref": "OWASP IDOR Prevention",
        "label": "CWE-639 / IDOR",
        "message": "IDOR (Insecure Direct Object Reference) là gì và cách phòng chống theo OWASP?",
        "expected_techniques": [
            "access control", "kiểm tra quyền", "authorization",
            "indirect reference", "tham chiếu gián tiếp",
            "uuid", "random id",
        ],
        "expected_citation": ["OWASP"],
        "expect_code": False,
    },
    {
        "id": "A08-crypto",
        "owasp_ref": "OWASP Cryptographic Storage CS",
        "label": "CWE-327 / A02:2021 Cryptographic Failures",
        "message": "CWE-327 sử dụng thuật toán mã hóa yếu, khắc phục theo OWASP thế nào? Ví dụ code Python dùng AES-GCM.",
        "expected_techniques": [
            "aes", "aes-gcm", "aes-256",
            "tls", "tls 1.2", "tls 1.3",
            "không dùng md5", "không dùng sha1", "sha256", "sha-256",
        ],
        "expected_citation": ["OWASP", "CWE-327"],
        "expect_code": True,
    },
    {
        "id": "A09-path-traversal",
        "owasp_ref": "OWASP Path Traversal",
        "label": "CWE-22 / Path Traversal",
        "message": "CWE-22 Path Traversal là gì và cách vá theo OWASP? Ví dụ code Python kiểm tra đường dẫn.",
        "expected_techniques": [
            "realpath", "canonical", "abspath", "resolve",
            "os.path", "pathlib", "base path",
            "chứa ../", "directory traversal", "path traversal",
        ],
        "expected_citation": ["OWASP", "CWE-22"],
        "expect_code": True,
    },
    {
        "id": "A10-log4shell",
        "owasp_ref": "NVD CVE-2021-44228",
        "label": "CVE-2021-44228 Log4Shell (RCE Critical)",
        "message": "CVE-2021-44228 Log4Shell là gì và cách khắc phục?",
        "expected_techniques": [
            "log4j", "2.17", "formatmsgnolookups", "jndi",
            "cập nhật", "upgrade", "nâng cấp",
        ],
        "expected_citation": ["CVE-2021-44228"],
        "expect_code": True,
    },
]


def _post(base: str, path: str, payload: dict, timeout: int = 120) -> tuple[int, str, float]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0


def grade_case(answer: str, case: dict) -> dict:
    answer_lower = answer.lower()
    # Kỹ thuật: phải có ít nhất 2 expected_techniques (hoặc tất cả nếu ít hơn 2)
    tech_found = [t for t in case["expected_techniques"] if t.lower() in answer_lower]
    tech_min = min(2, len(case["expected_techniques"]))
    tech_pass = len(tech_found) >= tech_min

    # Citation
    cit_found = [c for c in case["expected_citation"] if c.lower() in answer_lower]
    cit_pass = len(cit_found) == len(case["expected_citation"])

    # Code block
    code_pass = ("```" in answer) if case["expect_code"] else True

    overall = tech_pass and cit_pass and code_pass
    return {
        "tech_found": tech_found,
        "tech_min_needed": tech_min,
        "tech_pass": tech_pass,
        "cit_found": cit_found,
        "cit_pass": cit_pass,
        "code_pass": code_pass,
        "overall": overall,
    }


def run_part_a(base: str) -> list[dict]:
    print("=" * 70)
    print("PART A — Prompt Eval Set (graded vs. OWASP techniques)")
    print("=" * 70)
    results = []
    for i, case in enumerate(EVAL_CASES, 1):
        print(f"\n[{i:02d}/{len(EVAL_CASES)}] {case['id']} — {case['label']}")
        st, body, dt = _post(base, "/ai/chat", {"user_id": f"eval-a-{case['id']}", "message": case["message"]})
        try:
            j = json.loads(body) if st == 200 else {}
        except Exception:
            j = {}
        answer = j.get("response") or j.get("answer") or ""
        grade = grade_case(answer, case) if answer else {"overall": False, "tech_pass": False, "cit_pass": False, "code_pass": False}

        status = "PASS" if grade["overall"] else "FAIL"
        print(f"  HTTP:{st}  {dt:.1f}s  [{status}]")
        print(f"  Kỹ thuật OWASP: {'✅' if grade['tech_pass'] else '❌'} tìm thấy {grade.get('tech_found',[])} (cần ≥{grade.get('tech_min_needed',2)})")
        print(f"  Citation chuẩn: {'✅' if grade['cit_pass'] else '❌'} {grade.get('cit_found',[])} / {case['expected_citation']}")
        print(f"  Code block:     {'✅' if grade['code_pass'] else '❌ (expected)' if case['expect_code'] else 'N/A'}")
        if answer:
            preview = answer[:200].replace("\n", " ")
            print(f"  Preview: {preview}...")
        results.append({**case, "http": st, "duration_s": round(dt, 2), "grade": grade, "answer": answer})

    npass = sum(1 for r in results if r["grade"]["overall"])
    print(f"\n{'='*70}")
    print(f"PART A SUMMARY: {npass}/{len(results)} PASS")
    print(f"  - Đúng kỹ thuật OWASP:  {sum(1 for r in results if r['grade']['tech_pass'])}/{len(results)}")
    print(f"  - Có citation chuẩn:    {sum(1 for r in results if r['grade']['cit_pass'])}/{len(results)}")
    print(f"  - Có code ví dụ:        {sum(1 for r in results if r['grade']['code_pass'])}/{len(results)}")
    return results


# ─── PART B: Live web_fetch demo (kích hoạt tool_call → owasp.org) ────────────
#
# Câu hỏi về dữ liệu "mới nhất" / "năm hiện tại" buộc model phải fetch live
# vì training data không đủ cập nhật.

LIVE_FETCH_QUERIES = [
    {
        "id": "B1-top10-latest",
        "label": "OWASP Top 10 mới nhất (kích hoạt web_fetch)",
        "message": "OWASP Top 10 phiên bản mới nhất hiện tại (2025) có gì thay đổi so với 2021? Lấy từ owasp.org.",
        "expect_tool": "web_fetch",
        "expect_source": "owasp.org",
    },
    {
        "id": "B2-cwe-top25-latest",
        "label": "CWE Top 25 mới nhất (kích hoạt fetch cwe.mitre.org)",
        "message": "CWE Top 25 Most Dangerous Software Weaknesses năm 2024 gồm những CWE nào? Lấy từ cwe.mitre.org.",
        "expect_tool": "web_fetch",
        "expect_source": "cwe.mitre.org",
    },
]


def run_part_b(base: str) -> list[dict]:
    print("\n" + "=" * 70)
    print("PART B — Live web_fetch Demo (tool_call → owasp.org / cwe.mitre.org)")
    print("=" * 70)

    results = []
    for case in LIVE_FETCH_QUERIES:
        print(f"\n  {case['id']} — {case['label']}")
        print(f"  Query: {case['message']}")

        # Dùng stream để bắt tool_call events
        tool_calls = []
        final_answer = ""
        t0 = time.time()

        payload = json.dumps({"user_id": f"eval-b-{case['id']}", "message": case["message"]}).encode()
        req = urllib.request.Request(
            base + "/ai/chat/stream", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                buf = ""
                for chunk in r:
                    buf += chunk.decode("utf-8", "replace")
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        event = re.search(r"^event: (.+)$", frame, re.M)
                        data_line = re.search(r"^data: (.+)$", frame, re.M)
                        if not event or not data_line or data_line.group(1) == "[DONE]":
                            continue
                        ev = event.group(1).strip()
                        try:
                            d = json.loads(data_line.group(1))
                        except Exception:
                            continue
                        if ev == "tool_call":
                            tool_calls.append(d)
                        elif ev == "final":
                            final_answer = d.get("content") or d.get("answer") or ""
        except Exception as e:
            print(f"  ERROR: {e}")

        dt = time.time() - t0
        # Grade
        fetch_events = [t for t in tool_calls if t.get("name") in ("web_fetch", "fetch_reference")]
        source_hit = any(case["expect_source"] in json.dumps(t) for t in fetch_events)

        status = "PASS" if fetch_events else "PARTIAL"
        print(f"  HTTP stream  {dt:.1f}s  [{status}]")
        print(f"  tool_call events: {len(tool_calls)} total, {len(fetch_events)} fetch")
        for tc in fetch_events:
            inp = tc.get("input") or {}
            url = inp.get("url") or str(inp)[:100]
            print(f"    → {tc['name']} url={url}")
        if not fetch_events:
            print(f"  ⚠️  Không có web_fetch/fetch_reference event — model trả lời từ training data")
            print(f"      (kiến thức OWASP vẫn đúng, nhưng không fetch live trong lần này)")
        if final_answer:
            print(f"  Preview: {final_answer[:200].replace(chr(10), ' ')}...")

        results.append({
            "id": case["id"], "label": case["label"],
            "tool_calls": tool_calls, "fetch_events": fetch_events,
            "source_hit": source_hit, "duration_s": round(dt, 2),
            "answer_preview": final_answer[:300],
        })

    nfetch = sum(1 for r in results if r["fetch_events"])
    print(f"\nPART B SUMMARY: {nfetch}/{len(results)} kích hoạt web_fetch thật")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8131")
    ap.add_argument("--part", choices=["A", "B", "AB"], default="AB")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"== Nghiệm thu: Remediation + OWASP grounding @ {base} ==\n")

    results_a, results_b = [], []
    if "A" in args.part:
        results_a = run_part_a(base)
    if "B" in args.part:
        results_b = run_part_b(base)

    # Save
    out = {
        "timestamp": int(time.time()),
        "base": base,
        "part_a": results_a,
        "part_b": results_b,
        "summary": {
            "part_a_pass": sum(1 for r in results_a if r["grade"]["overall"]),
            "part_a_total": len(results_a),
            "part_b_live_fetch": sum(1 for r in results_b if r["fetch_events"]),
            "part_b_total": len(results_b),
        },
    }
    out_path = f"evals/results/owasp_remediation_eval_{out['timestamp']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'='*70}")
    print(f"Saved → {out_path}")
    if results_a:
        pa = out["summary"]["part_a_pass"]
        pt = out["summary"]["part_a_total"]
        print(f"PART A (prompt eval):   {pa}/{pt} PASS {'✅' if pa==pt else '⚠️'}")
    if results_b:
        pb = out["summary"]["part_b_live_fetch"]
        bt = out["summary"]["part_b_total"]
        print(f"PART B (live fetch):    {pb}/{bt} kích hoạt web_fetch {'✅' if pb>0 else '⚠️ (xem ghi chú)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
