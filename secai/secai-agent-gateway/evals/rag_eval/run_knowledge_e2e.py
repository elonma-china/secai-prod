#!/usr/bin/env python3
"""End-to-end chat answers for the knowledge eval set, against one gateway (flag on OR off).

For each concept / remediation item (a sample of --n per group) it streams /ai/chat/stream,
records the answer, time-to-first-token and total time, plus the retrieval the gateway would
have pre-fetched (same query, scope=knowledge) so a judge can check faithfulness and citations.

  python3 run_knowledge_e2e.py --base http://127.0.0.1:8133 --tag off --out /tmp/e2e_off.json
"""
import argparse, asyncio, json, re, sys, time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--tag", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--n", type=int, default=30, help="items per group (concept, remediation)")
ap.add_argument("--cve-desc", type=int, default=0, help="extra CVE-description questions (regression check)")
ap.add_argument("--with-context", action="store_true", help="also record scope=knowledge retrieval per item")
args = ap.parse_args()

if args.with_context:
    sys.path.insert(0, str(HERE.parent.parent / "src"))
    from secai_gateway.retrieval.qdrant import _qdrant_search  # noqa: E402


def sample(items, group, n):
    xs = [i for i in items if i["group"] == group]
    step = max(1, len(xs) // n)
    return xs[::step][:n]


async def ask(client: httpx.AsyncClient, q: str, uid: str) -> dict:
    t0 = time.monotonic()
    ttft, text, final = None, [], None
    async with client.stream("POST", f"{args.base}/ai/chat/stream", json={"user_id": uid, "message": q}, timeout=300) as r:
        ev = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev:
                try:
                    data = json.loads(line[5:].strip() or "{}")
                except ValueError:
                    continue  # non-JSON data line (keep-alive etc.)
                if not isinstance(data, dict):
                    continue
                if ev == "token":
                    ttft = ttft or (time.monotonic() - t0)
                    text.append(data.get("delta", ""))
                elif ev == "final":
                    final = data.get("text") or data.get("response") or data.get("answer")
    return {"answer": final if final else "".join(text), "ttft_s": ttft, "total_s": time.monotonic() - t0}


async def main():
    items = [json.loads(l) for l in open(HERE / "knowledge_eval_set.jsonl", encoding="utf-8") if l.strip()]
    todo = sample(items, "concept", args.n) + sample(items, "remediation", args.n)
    if args.cve_desc:
        # CVE-description prompts the broadened routing now sends to the knowledge pre-fetch too.
        sys.path.insert(0, str(HERE.parent.parent / "src"))
        from secai_gateway.agent.hooks import classify_chat_grounding
        gold = json.loads((HERE.parent.parent / "tests/fixtures/chat_grounding_routes_baseline.json").read_text(encoding="utf-8"))
        qs = [q for q, (r, w, k) in gold.items() if r == "concept" and classify_chat_grounding(q, True).route == "concept_kb" and q.lower().startswith("lỗ hổng")]
        step = max(1, len(qs) // args.cve_desc)
        todo += [{"id": f"cve-{i:02d}", "group": "cve_desc", "lang": "vi", "query": q, "key_points": [], "gold_parents": []} for i, q in enumerate(qs[::step][:args.cve_desc])]
    rows = []
    async with httpx.AsyncClient() as client:
        for it in todo:
            row = {"id": it["id"], "group": it["group"], "lang": it["lang"], "query": it["query"], "key_points": it["key_points"], "gold_parents": it["gold_parents"]}
            try:
                row.update(await ask(client, it["query"], f"kbe2e-{args.tag}-{it['id']}"))
            except Exception as e:  # keep going: a failed item is data too
                row.update(answer="", error=repr(e))
            if args.with_context:
                res = await _qdrant_search(it["query"], None, top_k=4, scope="knowledge")
                row["context"] = res.content if "### Result" in (res.content or "") else ""
            kp = [any(a.lower() in row["answer"].lower() for a in k.split("|")) for k in it["key_points"]]
            row["kp_coverage"] = sum(kp) / len(kp) if kp else None
            row["urls_in_answer"] = re.findall(r"https?://[^\s)\]>\"']+", row["answer"])
            rows.append(row)
            print(f"{it['id']:22s} err={row.get('error')} ttft={row.get('ttft_s') and round(row['ttft_s'], 1)} kp={row['kp_coverage']} len={len(row['answer'])}", flush=True)
    ok = [r for r in rows if r.get("ttft_s")]
    ttfts = sorted(r["ttft_s"] for r in ok)
    Path(args.out).write_text(json.dumps({"tag": args.tag, "n": len(rows), "errors": sum(1 for r in rows if r.get("error")),
                                          "ttft_p50": ttfts[len(ttfts) // 2] if ttfts else None,
                                          "ttft_p95": ttfts[max(0, int(len(ttfts) * 0.95) - 1)] if ttfts else None,
                                          "kp_mean": sum(r["kp_coverage"] for r in rows if r["kp_coverage"] is not None) / max(1, len(rows)),
                                          "rows": rows}, ensure_ascii=False, indent=1))

asyncio.run(main())
