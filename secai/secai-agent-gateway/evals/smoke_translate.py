#!/usr/bin/env python3
"""Smoke test /ai/translate after a deploy — masking (giữ tài khoản/mật khẩu) + chunking.

Read-only: chỉ gọi POST /ai/translate[/stream] (không đổi state), an toàn chạy trên
production. Chỉ dùng thư viện chuẩn python3 — KHÔNG cần .venv.

    python3 evals/smoke_translate.py                         # mặc định http://127.0.0.1:8131
    python3 evals/smoke_translate.py --base http://IP:8131   # host khác
    python3 evals/smoke_translate.py --load 5                # thêm test đồng thời

Exit code 0 = tất cả PASS; 1 = có FAIL (dùng được trong CI / gate deploy).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.error
import urllib.request

FAILS: list[str] = []


def _post(base: str, path: str, payload: dict, timeout: int = 180) -> tuple[int, str]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # 4xx/5xx still carry a body worth seeing
        return e.code, e.read().decode("utf-8", "replace")


def _get(base: str, path: str, timeout: int = 10) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def long_text(n: int = 45) -> tuple[str, int]:
    """~11KB doc: mỗi đoạn 1 IP marker 10.0.0.<i> để đo coverage (không rơi đoạn giữa)."""
    paras = [
        f"Đoạn phân tích số {i}: máy chủ tại địa chỉ 10.0.0.{i} có cấu hình sai cho phép truy "
        "cập trái phép và cần được vá cùng với siết chặt tường lửa trước khi kẻ tấn công lợi "
        "dụng để chiếm quyền điều khiển toàn bộ hệ thống một cách nghiêm trọng."
        for i in range(1, n + 1)
    ]
    return "\n\n".join(paras), n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8131", help="gateway base URL")
    ap.add_argument("--load", type=int, default=0, help="số request dài đồng thời (0=bỏ qua)")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    print(f"== smoke /ai/translate @ {base} ==\n")

    # 1. health
    st, body = _get(base, "/health")
    try:
        h = json.loads(body)
    except Exception:
        h = {}
    check("health ok + llm", st == 200 and h.get("status") == "ok" and h.get("llm") is True,
          json.dumps(h)[:120] if h else f"HTTP{st}")

    # 2/3. short both directions
    st, body = _post(base, "/ai/translate", {"text": "Lỗ hổng cho phép kẻ tấn công thực thi mã từ xa.", "target_lang": "en"})
    out = (json.loads(body).get("translated_text", "") if st == 200 else "")
    check("short VI->EN", st == 200 and out and "Lỗ hổng" not in out, f"HTTP{st} :: {out[:70]}")

    st, body = _post(base, "/ai/translate", {"text": "The attacker can bypass authentication using a crafted token.", "target_lang": "vi"})
    out = (json.loads(body).get("translated_text", "") if st == 200 else "")
    check("short EN->VI", st == 200 and re.search(r"[àáảãạăâđêôơư]", out.lower()) is not None, f"HTTP{st} :: {out[:70]}")

    # 4. CREDENTIAL — phải 200 (không 422) và giữ nguyên giá trị verbatim
    cred = ("Đăng nhập vào máy chủ với tài khoản: admin và mật khẩu: P@ssw0rd!2024. "
            "Khóa API là sk_live_ABCDEFGHIJKLMNOP1234, gửi tới https://target.local:8443/login , "
            "tham chiếu CVE-2024-27198.")
    st, body = _post(base, "/ai/translate", {"text": cred, "target_lang": "en"})
    out = (json.loads(body).get("translated_text", "") if st == 200 else "")
    lits = ["admin", "P@ssw0rd!2024", "sk_live_ABCDEFGHIJKLMNOP1234", "https://target.local:8443/login", "CVE-2024-27198"]
    miss = [x for x in lits if x not in out]
    check("credential -> 200 (KHÔNG 422)", st == 200, f"HTTP{st}")
    check("credential giữ nguyên verbatim", st == 200 and not miss, f"missing={miss}")
    if out:
        print(f"       out: {out[:150]}")

    # 5. LONG ~11KB -> 200, chunked, đủ 45 mốc, không cụt
    lt, n = long_text()
    st, body = _post(base, "/ai/translate", {"text": lt, "target_lang": "en"})
    j = json.loads(body) if st == 200 else {}
    out = j.get("translated_text", "")
    ips = len(set(re.findall(r"\b10\.0\.0\.\d+\b", out)))
    nch = j.get("metadata", {}).get("chunks")
    check("long 200", st == 200, f"HTTP{st} inlen={len(lt)}")
    check("long chunked (metadata.chunks>=2)", isinstance(nch, int) and nch >= 2, f"chunks={nch}")
    check(f"long coverage ({n} IP markers)", ips == n, f"ips={ips}/{n}")
    check("long không cụt đuôi", bool(out) and out.strip()[-1:] in ".!?\"')", f"tail=...{out.strip()[-45:]}")

    # 6/7. streaming
    st, txt = _post(base, "/ai/translate/stream", {"text": "Máy chủ bị lộ mật khẩu quản trị.", "target_lang": "en"})
    check("stream ngắn: token+final+DONE", all(k in txt for k in ["event: token", "event: final", "[DONE]"]),
          txt[:60].replace("\n", " "))

    st, txt = _post(base, "/ai/translate/stream", {"text": lt, "target_lang": "en"})
    ips2 = len(set(re.findall(r"\b10\.0\.0\.\d+\b", txt)))
    check("stream dài: final + coverage", ("event: final" in txt) and ips2 == n, f"ips={ips2}/{n}")

    # 8. concurrency (tùy chọn)
    if args.load > 0:
        def one(_k: int):
            t = time.time()
            s, b = _post(base, "/ai/translate", {"text": lt, "target_lang": "en"})
            jj = json.loads(b) if s == 200 else {}
            o = jj.get("translated_text", "")
            return s, jj.get("metadata", {}).get("chunks"), len(set(re.findall(r"\b10\.0\.0\.\d+\b", o))), round(time.time() - t, 1)
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=args.load) as ex:
            res = list(ex.map(one, range(args.load)))
        okc = sum(1 for s, _c, i, _d in res if s == 200 and i == n)
        for k, (s, c, i, d) in enumerate(res):
            print(f"       req{k}: HTTP{s} chunks={c} ips={i}/{n} {d}s")
        check(f"concurrency {args.load}x long", okc == args.load, f"{okc}/{args.load} OK, wall={time.time()-t0:.1f}s")

    print("\n==== SMOKE:", "ALL PASS ✅" if not FAILS else f"FAIL ❌ -> {FAILS}", "====")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
