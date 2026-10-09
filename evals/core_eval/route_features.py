#!/usr/bin/env python3
"""Per-question routing evidence, for choosing between the CVE KB and the general knowledge KB.

Runs INSIDE a gateway container (same code, same serving, prod Qdrant read-only):

  docker cp route_features.py C:/tmp/ && docker cp routing_eval_set_b.jsonl C:/tmp/
  docker exec -i C python /tmp/route_features.py /tmp/routing_eval_set_b.jsonl > features_b.json

For every item {"id","query","label","kind"} it records the regex route (flags on) and, for each of the two
scopes, the best dense cosine and the best rerank score of the top candidates:
  k_dense / k_rr   knowledge scope (cheat sheets, ASVS, mitigations, taxonomy)
  e_dense / e_rr   entity scope (CVE records, CWE/CAPEC/ATT&CK definitions)
  k_gated          1 if the production knowledge pre-fetch would hand a block to the model (dense gate + rerank gate)
Offline rules (route_rules.py) are then compared on these numbers without calling the network again.
"""
import asyncio
import json
import re
import sys

import httpx

from secai_gateway.agent.hooks import classify_chat_grounding
from secai_gateway.retrieval import qdrant as q
from secai_gateway.retrieval.embedding import rerank
from secai_gateway.retrieval.filters import build_filter

TOP = 10


async def best_rerank(client, query, hits):
    docs = [str((h.get("payload") or {}).get("text") or "")[:1500] for h in hits[:TOP]]
    docs = [d for d in docs if d]
    if not docs:
        return 0.0
    res = await rerank(client, query, docs, len(docs))
    return max((float(r.get("relevance_score", 0.0)) for r in res or []), default=0.0)


async def one(client, it):
    text = it["query"]
    plan = classify_chat_grounding(text, True, True, True)
    vec = await q.embed(client, text)
    kh, eh = await asyncio.gather(q._dense_search(client, vec, build_filter({"scope": "knowledge"}), TOP),
                                  q._dense_search(client, vec, build_filter({"scope": "entity"}), 30))
    ch = [h for h in eh if (h.get("payload") or {}).get("cve_id")]  # CVE records only (entity scope also holds CWE/CAPEC/ATT&CK nodes)
    k_rr, e_rr, c_rr = await asyncio.gather(best_rerank(client, text, kh), best_rerank(client, text, eh[:TOP]), best_rerank(client, text, ch))
    flat = re.sub(r"[^a-z0-9]", "", text.lower())
    alias_hit = 0
    for h in ch[:8]:
        t = str(h["payload"].get("text") or "")
        if t.startswith("Tên gọi phổ biến:"):
            names = re.split(r"[,(]", t[len("Tên gọi phổ biến:"):t.find(". CVE ID")])
            if any(len(re.sub(r"[^a-z0-9]", "", n.lower())) >= 5 and re.sub(r"[^a-z0-9]", "", n.lower()) in flat for n in names):
                alias_hit = 1
                break
    k_dense = max((h["score"] for h in kh), default=0.0)
    e_dense = max((h["score"] for h in eh), default=0.0)
    gated = await q._qdrant_search(query=text, ctx=None, top_k=4, scope="knowledge")
    return {"id": it["id"], "label": it["label"], "kind": it["kind"], "query": text,
            "route": plan.route, "route_kn": bool(plan.knowledge),
            "k_dense": round(k_dense, 4), "k_rr": round(k_rr, 4), "e_dense": round(e_dense, 4), "e_rr": round(e_rr, 4),
            "c_dense": round(max((h["score"] for h in ch), default=0.0), 4), "c_rr": round(c_rr, 4), "alias_hit": alias_hit,
            "k_gated": int("### Result" in (gated.content or ""))}


async def main():
    items = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=60) as client:
        async def guarded(it):
            async with sem:
                for attempt in range(3):
                    try:
                        return await one(client, it)
                    except Exception as e:  # serving hiccup: retry, then record the failure
                        err = repr(e)
                        await asyncio.sleep(1 + attempt)
                return {"id": it["id"], "label": it["label"], "kind": it["kind"], "query": it["query"], "error": err}
        rows = await asyncio.gather(*(guarded(i) for i in items))
    print(json.dumps(rows, ensure_ascii=False))


asyncio.run(main())
