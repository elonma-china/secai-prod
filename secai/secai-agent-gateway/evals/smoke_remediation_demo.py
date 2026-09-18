#!/usr/bin/env python3
"""Chứng minh kịch bản: hệ thống đề xuất + sinh mã khắc phục/vá lỗi tham khảo
chuẩn OWASP / Secure Coding Guideline, qua endpoint /ai/chat đang chạy thật.

Không đổi state hệ thống — chỉ gọi POST /ai/chat (read-only).
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8131"

CASES = [
    {
        "id": "sqli",
        "label": "SQL Injection (CWE-89)",
        "message": (
            "CWE-89 SQL Injection là gì và cách khắc phục theo chuẩn OWASP? "
            "Cho ví dụ đoạn code sửa lỗi bằng Java, dùng PreparedStatement thay vì nối chuỗi SQL."
        ),
        "expect_terms": ["OWASP", "CWE-89"],
        "expect_code_lang_hint": ["preparedstatement", "```"],
    },
    {
        "id": "xss",
        "label": "XSS (CWE-79)",
        "message": (
            "CWE-79 Cross-Site Scripting là gì, cách phòng chống theo Secure Coding Guideline/OWASP? "
            "Cho ví dụ code Python/Flask escape output đúng cách để chống XSS."
        ),
        "expect_terms": ["OWASP", "CWE-79"],
        "expect_code_lang_hint": ["```"],
    },
    {
        "id": "ssrf",
        "label": "SSRF",
        "message": (
            "SSRF là gì và cách phòng chống theo OWASP? "
            "Cho ví dụ code Python kiểm tra whitelist URL trước khi gọi request ra ngoài để chống SSRF."
        ),
        "expect_terms": ["OWASP"],
        "expect_code_lang_hint": ["```"],
    },
    {
        "id": "log4shell",
        "label": "CVE-2021-44228 Log4Shell",
        "message": "CVE-2021-44228 (Log4Shell) là lỗi gì và cách khắc phục thế nào?",
        "expect_terms": ["CVE-2021-44228"],
        "expect_code_lang_hint": [],
    },
    {
        "id": "deserial",
        "label": "Insecure Deserialization (CWE-502)",
        "message": (
            "CWE-502 insecure deserialization khắc phục thế nào theo OWASP? "
            "Cho ví dụ code Java an toàn (whitelist class trước khi deserialize)."
        ),
        "expect_terms": ["OWASP", "CWE-502"],
        "expect_code_lang_hint": ["```"],
    },
]


def post(path: str, payload: dict, timeout: int = 180) -> tuple[int, str, float]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0


def main() -> int:
    results = []
    print(f"== demo remediation-code scenario @ {BASE} ==\n")
    for c in CASES:
        st, body, dt = post("/ai/chat", {"user_id": "demo-remediation", "message": c["message"]})
        try:
            j = json.loads(body) if st == 200 else {}
        except Exception:
            j = {}
        answer = j.get("response") or j.get("answer") or ""
        has_code_block = "```" in answer
        terms_found = [t for t in c["expect_terms"] if t.lower() in answer.lower()]
        terms_missing = [t for t in c["expect_terms"] if t.lower() not in answer.lower()]
        code_hint_hit = any(h.lower() in answer.lower() for h in c["expect_code_lang_hint"]) if c["expect_code_lang_hint"] else True

        ok = st == 200 and bool(answer) and not terms_missing and code_hint_hit
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {c['id']} — {c['label']}  (HTTP{st}, {dt:.1f}s, code_block={has_code_block}, terms_found={terms_found}, terms_missing={terms_missing})")
        print(f"        Q: {c['message']}")
        print(f"        A (đầu 300 ký tự): {answer[:300].replace(chr(10),' ')}")
        print()

        results.append({
            "id": c["id"],
            "label": c["label"],
            "question": c["message"],
            "http_status": st,
            "duration_s": round(dt, 2),
            "answer": answer,
            "has_code_block": has_code_block,
            "expect_terms": c["expect_terms"],
            "terms_found": terms_found,
            "terms_missing": terms_missing,
            "pass": ok,
            "metadata": j.get("metadata"),
        })

    out_path = "evals/results/remediation_demo_" + str(int(time.time())) + ".json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Saved -> {out_path}")

    npass = sum(1 for r in results if r["pass"])
    print(f"\n==== {npass}/{len(results)} PASS ====")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
