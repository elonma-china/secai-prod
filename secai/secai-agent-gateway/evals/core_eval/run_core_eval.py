#!/usr/bin/env python3
"""Run core_eval_set.jsonl against one gateway; record answer, tools called, timing, errors.

  python3 run_core_eval.py --base http://127.0.0.1:8134 --tag on --out /tmp/core_on.json [--only core-001,core-002]
"""
import argparse, asyncio, json, time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--tag", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--only", default="")
args = ap.parse_args()


async def stream(client: httpx.AsyncClient, url: str, body: dict) -> dict:
    t0 = time.monotonic()
    out = {"answer": "", "final": None, "tools": [], "tool_errors": [], "regenerate": 0, "events_error": [], "ttft_s": None, "status": None}
    toks = []
    async with client.stream("POST", url, json=body, timeout=300) as r:
        out["status"] = r.status_code
        if r.status_code != 200:
            out["answer"] = (await r.aread()).decode("utf-8", "replace")[:500]
            out["total_s"] = time.monotonic() - t0
            return out
        ev = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev:
                try:
                    d = json.loads(line[5:].strip() or "{}")
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if ev == "token":
                    out["ttft_s"] = out["ttft_s"] or (time.monotonic() - t0)
                    toks.append(d.get("delta", ""))
                elif ev == "tool_call":
                    out["tools"].append({"name": d.get("name"), "prefetch": str(d.get("id", "")).startswith("pre-fetch-"), "input": str(d.get("input"))[:160]})
                elif ev == "tool_result" and d.get("is_error"):
                    out["tool_errors"].append({"name": d.get("name"), "summary": str(d.get("summary"))[:160]})
                elif ev == "regenerate":
                    out["regenerate"] += 1
                elif ev == "final":
                    out["final"] = d.get("text") or d.get("response") or d.get("answer")
                elif ev == "error":
                    out["events_error"].append(str(d)[:200])
    out["answer"] = out["final"] if out["final"] else "".join(toks)
    out["total_s"] = time.monotonic() - t0
    return out


async def run_item(client, it) -> dict:
    uid = f"core-{args.tag}-{it['id']}"
    base = {"id": it["id"], "group": it["group"], "query": it["query"] or " → ".join(it["turns"]), "expect": it["expect"], "facts": it["facts"], "must_not": it["must_not"]}
    try:
        if it.get("turns"):
            sid = (await client.post(f"{args.base}/sessions", json={"user_id": uid, "title": it["id"]})).json()["session_id"]
            turns = []
            for q in it["turns"]:
                r = await stream(client, f"{args.base}/sessions/{sid}/chat/stream", {"user_id": uid, "message": q})
                turns.append({"q": q, **r})
            return {**base, "turns": turns, "answer": turns[-1]["answer"], "tools": [t for x in turns for t in x["tools"]], "ttft_s": turns[-1]["ttft_s"], "status": turns[-1]["status"]}
        return {**base, **(await stream(client, f"{args.base}/ai/chat/stream", {"user_id": uid, "message": it["query"]}))}
    except Exception as e:  # a failed item is data
        return {**base, "answer": "", "error": repr(e), "tools": []}


async def main():
    items = [json.loads(l) for l in open(HERE / "core_eval_set.jsonl", encoding="utf-8") if l.strip()]
    if args.only:
        keep = set(args.only.split(","))
        items = [i for i in items if i["id"] in keep]
    rows = []
    async with httpx.AsyncClient() as client:
        for it in items:
            r = await run_item(client, it)
            rows.append(r)
            print(f"{it['id']} {it['group']:14s} status={r.get('status')} len={len(r['answer'])} tools={[t['name'] for t in r['tools']]} err={r.get('error')}", flush=True)
    Path(args.out).write_text(json.dumps({"tag": args.tag, "base": args.base, "n": len(rows), "rows": rows}, ensure_ascii=False, indent=1))

asyncio.run(main())
