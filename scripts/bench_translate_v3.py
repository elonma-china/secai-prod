#!/usr/bin/env python3
"""Throughput / latency benchmark for POST /ai/v3/translate (run from the project root).

    python3 scripts/bench_translate_v3.py http://127.0.0.1:18285 --levels 1:24,8:96,16:96,32:128

Each level is ``concurrency:requests``. Texts are synthetic, security-flavoured paragraphs
with a unique suffix per request (no cache can answer them). Point it at a smoke container,
not at a production gateway, and raise MULTILANG_RATE_LIMIT_PER_MINUTE there first.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx

EN = [
    "A remote code execution vulnerability exists in the log processing component. An "
    "unauthenticated attacker can send a specially crafted request that causes the server to "
    "deserialize untrusted data. Administrators should upgrade to the latest patched release.",
    "The advisory describes an improper input validation flaw in the file upload handler. "
    "Path traversal sequences in archive entry names are not sanitized, which may allow an "
    "attacker to overwrite arbitrary files outside the intended directory.",
    "During the assessment we found publicly readable storage buckets, overly permissive "
    "identity policies and missing multi-factor authentication for privileged accounts.",
]
VI = [
    "Lỗ hổng cho phép kẻ tấn công chưa xác thực gửi yêu cầu được tạo đặc biệt tới máy chủ, "
    "khiến ứng dụng giải tuần tự hóa dữ liệu không đáng tin cậy và thực thi mã tùy ý.",
]


def payload(i: int) -> dict:
    if i % 4 == 3:
        return {"text": f"{VI[0]} (mục {i})", "source_lang": "vi", "target_lang": "en"}
    return {"text": f"{EN[i % len(EN)]} (item {i})", "source_lang": "en", "target_lang": "vi"}


async def one(client: httpx.AsyncClient, url: str, i: int, gate: asyncio.Semaphore):
    async with gate:
        started = time.perf_counter()
        try:
            response = await client.post(url, json=payload(i), timeout=180)
            return response.status_code, time.perf_counter() - started
        except httpx.HTTPError:
            return 0, time.perf_counter() - started


async def level(base: str, concurrency: int, requests: int) -> dict:
    gate = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency + 8)
    async with httpx.AsyncClient(limits=limits, trust_env=False) as client:
        started = time.perf_counter()
        results = await asyncio.gather(*[one(client, f"{base}/ai/v3/translate", i, gate)
                                         for i in range(requests)])
        wall = time.perf_counter() - started
    ok = sorted(latency for status, latency in results if status == 200)
    codes = sorted({status for status, _ in results if status != 200})
    pick = lambda q: ok[min(len(ok) - 1, int(q * len(ok)))] if ok else float("nan")
    return {"concurrency": concurrency, "requests": requests, "ok": len(ok), "errors": codes,
            "wall_s": round(wall, 2), "rps": round(len(ok) / wall, 2),
            "p50_s": round(pick(0.50), 2), "p95_s": round(pick(0.95), 2),
            "mean_s": round(statistics.fmean(ok), 2) if ok else None}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("--levels", default="1:24,8:96,16:96,32:128")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--warmup", type=int, default=4, help="untimed requests before measuring")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if args.warmup:
        await level(base, 2, args.warmup)
    rows = []
    for spec in args.levels.split(","):
        concurrency, requests = (int(part) for part in spec.split(":"))
        row = await level(base, concurrency, requests)
        rows.append(row)
        if not args.json:
            print(f"c={row['concurrency']:>3} n={row['requests']:>4} ok={row['ok']:>4} err={row['errors'] or '-'} "
                  f"wall={row['wall_s']:>6}s rps={row['rps']:>6} p50={row['p50_s']:>5}s "
                  f"p95={row['p95_s']:>5}s", flush=True)
    if args.json:
        print(json.dumps(rows))


if __name__ == "__main__":
    asyncio.run(main())
