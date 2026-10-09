#!/usr/bin/env python3
"""Which database does the chat actually consult for each question? Measured through the real HTTP path.

  python3 run_route_e2e.py BASE_URL SET.jsonl OUT.json [--workers 4]

For each {"id","query","label","kind"} it POSTs /ai/chat/stream and reads only the frames the gateway sends BEFORE the
agent runs (the synthetic qdrant_search tool_call/tool_result produced by the server-side pre-fetch), then closes the
connection: no LLM answer is waited for. Observed grounding:
  cve        a CVE-record block only        knowledge  a reference-text block only
  both       CVE records + reference text   none       no pre-fetch (or the input classifier answered instead)
label scoring: knowledge -> knowledge|both, cve_search -> cve|both, both -> both, none -> none
(kind id_lookup is a bare "which CVE?" follow-up: any grounding but knowledge is fine).
"""
import argparse
import asyncio
import collections
import json
import time
import uuid

import httpx


async def observe(client: httpx.AsyncClient, base: str, query: str) -> dict:
    t0 = time.monotonic()
    body = {"user_id": f"route-{uuid.uuid4().hex[:10]}", "message": query}
    frames = []
    async with client.stream("POST", f"{base}/ai/chat/stream", json=body, timeout=60) as r:
        ev = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except ValueError:
                    data = {}
                frames.append((ev, data))
                if ev == "tool_result" and str(data.get("id", "")).startswith("pre-fetch-"):
                    break
                if ev == "tool_call" and str(data.get("id", "")).startswith("pre-fetch-"):
                    continue
                break  # first frame is not a pre-fetch frame: nothing was pre-fetched
    got = next((d for e, d in frames if e == "tool_result" and str(d.get("id", "")).startswith("pre-fetch-")), None)
    if not got:
        kind = "none"
    else:
        summary = str(got.get("summary") or "")
        head = str(got.get("result") or "")
        kn_first = head.lstrip().startswith("=== Tài liệu tham chiếu")
        both = summary.count("qdrant_search(") >= 2
        kind = "both" if both else ("knowledge" if kn_first else "cve")
    return {"grounding": kind, "ms": int((time.monotonic() - t0) * 1000), "first": frames[0][0] if frames else None}


def ok(label: str, kind: str, grounding: str) -> bool:
    if label == "knowledge":
        return grounding in ("knowledge", "both")
    if label == "cve_search":
        return grounding in ("cve", "both")
    if label == "both":
        return grounding == "both"
    if kind == "id_lookup":
        return grounding not in ("knowledge", "both")
    return grounding == "none"


def group(label: str, kind: str) -> str:
    if label != "none":
        return label
    return "none_lookalike" if kind.startswith("lookalike") else "none_other"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("items")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    items = [json.loads(l) for l in open(a.items, encoding="utf-8") if l.strip()]
    sem = asyncio.Semaphore(a.workers)
    async with httpx.AsyncClient() as client:
        async def one(it):
            async with sem:
                for attempt in range(3):
                    try:
                        return {**it, **await observe(client, a.base, it["query"])}
                    except Exception as e:  # noqa: BLE001 - recorded, retried
                        err = repr(e)
                        await asyncio.sleep(1 + attempt)
                return {**it, "grounding": "error", "error": err}
        rows = await asyncio.gather(*(one(i) for i in items))
    for r in rows:
        r["ok"] = ok(r["label"], r["kind"], r["grounding"])
    json.dump(rows, open(a.out, "w"), ensure_ascii=False)
    g = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        k = group(r["label"], r["kind"])
        g[k][0] += 1
        g[k][1] += r["ok"]
    print(" ".join(f"{k}={v[1] / v[0]:.2f}({v[0]})" for k, v in sorted(g.items())),
          f"errors={sum(r['grounding'] == 'error' for r in rows)}")


asyncio.run(main())
