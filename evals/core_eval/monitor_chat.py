#!/usr/bin/env python3
"""Post-rollout health check of the chat gateway, from its container log (read-only).

  python3 monitor_chat.py --container secai-docker-gateway --since 60m [--probe 6 --base http://127.0.0.1:8131]

Reports 5xx on chat routes, restarts, knowledge pre-fetch outcome and latency, hits=0 rate per scope and
Python errors. With --probe N it also sends N synthetic questions (concept/remediation) and reports TTFT,
which the gateway does not log. Exit code 1 if a hard criterion fails:
  0 chat 5xx, 0 restarts, 0 tracebacks, knowledge pre-fetch p95 < 3 s, probe TTFT p95 within --ttft-budget.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import time

import httpx

PROBES = [
    "SQL injection là gì và cách phòng chống?",
    "Làm sao chống XSS trong ứng dụng web?",
    "CSRF khác gì với XSS?",
    "Cách lưu mật khẩu an toàn cho người dùng?",
    "Hardening SSH server nên làm những gì?",
    "Giải thích CWE-79 và cách khắc phục.",
    "CVE-2021-44228 là lỗ hổng gì?",
    "Top 10 OWASP gồm những mục nào?",
]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def parse_log(container: str, since: str) -> dict:
    r = sh("docker", "logs", "--since", since, container)
    lines = (r.stdout + r.stderr).splitlines()
    chat = {"total": 0, "5xx": 0, "4xx": 0}
    pf_ms, pf_timeout, hits = [], 0, {}
    errors = tracebacks = 0
    for ln in lines:
        m = re.search(r'"POST (/ai/chat\S*|/sessions/\S+/chat\S*) HTTP/[\d.]+" (\d{3})', ln)
        if m:
            chat["total"] += 1
            code = int(m.group(2))
            chat["5xx"] += code >= 500
            chat["4xx"] += 400 <= code < 500
        m = re.search(r"knowledge pre-fetch ok ms=(\d+)", ln)
        if m:
            pf_ms.append(int(m.group(1)))
        if "knowledge pre-fetch quá" in ln:
            pf_timeout += 1
        m = re.search(r"qdrant_search scope=(\w+) .*?hits=(\d+)", ln)
        if m:
            s = hits.setdefault(m.group(1), [0, 0])
            s[0] += 1
            s[1] += m.group(2) == "0"
        if " ERROR " in ln:
            errors += 1
        if "Traceback (most recent call last)" in ln:
            tracebacks += 1
    return {"chat": chat, "prefetch_ms": pf_ms, "prefetch_timeout": pf_timeout, "hits": hits,
            "errors": errors, "tracebacks": tracebacks, "lines": len(lines)}


def probe(base: str, n: int) -> list[float]:
    ttfts = []
    with httpx.Client(timeout=120) as c:
        for i in range(n):
            q = PROBES[i % len(PROBES)]
            t0 = time.monotonic()
            first = None
            with c.stream("POST", f"{base}/ai/chat/stream", json={"user_id": f"monitor-{int(t0)}-{i}", "message": q}) as r:
                ev = None
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:") and ev == "token" and first is None:
                        first = time.monotonic() - t0
                        break
            if first is not None:
                ttfts.append(first)
    return ttfts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="secai-docker-gateway")
    ap.add_argument("--since", default="60m")
    ap.add_argument("--probe", type=int, default=0)
    ap.add_argument("--base", default="http://127.0.0.1:8131")
    ap.add_argument("--ttft-budget", type=float, default=4.0, help="max acceptable probe TTFT p95, seconds")
    args = ap.parse_args()

    ins = json.loads(sh("docker", "inspect", args.container).stdout)[0]
    restarts = ins["RestartCount"]
    rep = parse_log(args.container, args.since)
    out = {"container": args.container, "image": ins["Image"][:19], "since": args.since, "restarts": restarts,
           "chat": rep["chat"], "log_lines": rep["lines"], "errors": rep["errors"], "tracebacks": rep["tracebacks"],
           "prefetch": {"n": len(rep["prefetch_ms"]), "timeouts": rep["prefetch_timeout"],
                        "p50_ms": pct(rep["prefetch_ms"], 0.5), "p95_ms": pct(rep["prefetch_ms"], 0.95)},
           "hits0_rate": {k: round(v[1] / v[0], 3) for k, v in rep["hits"].items() if v[0]}}
    fails = []
    if rep["chat"]["5xx"]:
        fails.append("chat 5xx")
    if restarts:
        fails.append("restarts")
    if rep["tracebacks"]:
        fails.append("tracebacks")
    p95 = out["prefetch"]["p95_ms"]
    if p95 is not None and p95 >= 3000:
        fails.append("prefetch p95 >= 3s")
    if args.probe:
        tt = probe(args.base, args.probe)
        out["probe"] = {"n": len(tt), "ttft_p50_s": round(statistics.median(tt), 2) if tt else None,
                        "ttft_p95_s": round(pct(tt, 0.95), 2) if tt else None}
        if len(tt) < args.probe:
            fails.append("probe without tokens")
        if tt and pct(tt, 0.95) > args.ttft_budget:
            fails.append("probe TTFT p95 over budget")
    out["fail"] = fails
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
