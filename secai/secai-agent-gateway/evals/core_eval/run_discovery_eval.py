#!/usr/bin/env python3
"""Retrieval-level check of CVE discovery: does the chat pre-fetch put the gold CVE in front of the model?

Runs INSIDE a gateway container so it exercises the code that ships:

  docker cp run_discovery_eval.py C:/tmp/ && docker cp discovery_set.jsonl C:/tmp/
  docker exec C python /tmp/run_discovery_eval.py /tmp/discovery_set.jsonl [--kw discover=true]

Item: {"id","query","gold_cve" (str or list),"group"}. Reports recall@5 / recall@10 per group and the
route the chat classifier would take (a query routed away from cve_search never reaches this search).
"""
import asyncio
import json
import re
import sys

from secai_gateway.agent.hooks import classify_chat_grounding
from secai_gateway.retrieval.qdrant import _qdrant_search

path = sys.argv[1]
kw = {}
if "--kw" in sys.argv:
    for pair in sys.argv[sys.argv.index("--kw") + 1].split(","):
        k, v = pair.split("=")
        kw[k] = {"true": True, "false": False}.get(v, v)


async def main():
    items = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    rows = []
    sem = asyncio.Semaphore(4)

    async def one(it):
        async with sem:
            plan = classify_chat_grounding(it["query"], knowledge_enabled=True, cve_search_enabled=True, discovery_enabled=bool(kw.get('discover')))
            r = await _qdrant_search(query=it["query"], ctx=None, top_k=10, **kw)
            ids = list(dict.fromkeys(re.findall(r"CVE-\d{4}-\d{4,7}", r.content)))
            gold = it["gold_cve"] if isinstance(it["gold_cve"], list) else [it["gold_cve"]]
            rank = next((i + 1 for i, c in enumerate(ids) if c in gold), None)
            return {"id": it["id"], "group": it.get("group", "-"), "route": plan.route, "rank": rank, "n": len(ids), "query": it["query"], "top3": ids[:3]}

    rows = await asyncio.gather(*(one(i) for i in items))
    out = {}
    for g in sorted({r["group"] for r in rows}) + ["ALL"]:
        rs = [r for r in rows if g == "ALL" or r["group"] == g]
        out[g] = {"n": len(rs),
                  "recall@5": round(sum(1 for r in rs if r["rank"] and r["rank"] <= 5) / len(rs), 3),
                  "recall@10": round(sum(1 for r in rs if r["rank"]) / len(rs), 3),
                  "routed_to_cve_search": round(sum(1 for r in rs if r["route"] in ("cve_search", "id_lookup")) / len(rs), 3)}
    print(json.dumps({"kw": kw, "summary": out, "rows": rows}, ensure_ascii=False))

asyncio.run(main())
