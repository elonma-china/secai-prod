#!/usr/bin/env python3
"""Knowledge-KB eval: retrieval (scope=knowledge), routing, leakage, latency.

Measures, per docs/plans/kb-knowledge-grounding-criteria.md:
  S2  recall@5 / MRR at PARENT-document level, overall + by group + Vietnamese-only
  S3  routing: non-control prompts -> knowledge route, control prompts -> no knowledge route
  G5  hits flagged contains_attack_example (must be 0)
  G6  fraction of knowledge searches served in hybrid mode
  G1  entity-scope searches that return any knowledge chunk (must be 0)
  S6  p50/p95 latency of scope=knowledge searches

Run (collection via QDRANT_COLLECTION; embed/rerank via the usual env):
  QDRANT_COLLECTION=cve_kb_knowledge_lab python3 evals/rag_eval/run_knowledge_eval.py --out /tmp/k.json
"""
import asyncio, json, re, statistics, sys, time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "src"))
from secai_gateway.agent.hooks import classify_chat_grounding  # noqa: E402
from secai_gateway.retrieval.qdrant import _qdrant_search  # noqa: E402

CHUNKS = Path("/home/ict/AIEngines/secai/secai-data/knowledge/chunks.jsonl")
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else HERE / "knowledge_eval_results.json"
TOP_K = 5
REF = re.compile(r"\*\*Ref:\*\*\s*(\S+)")
HEAD = re.compile(r"### Result \d+ \(score: ([\d.]+)(?:, rerank: ([\d.]+))?\)")


def parse(content: str):
    hits = []
    for block in re.split(r"(?=### Result \d+)", content)[1:]:
        m, r = HEAD.search(block), REF.search(block)
        hits.append({"doc_id": r.group(1) if r else None, "rerank": float(m.group(2)) if m and m.group(2) else None,
                     "knowledge": "(knowledge)" in block})
    return hits


async def main():
    meta = {}
    for line in CHUNKS.open(encoding="utf-8"):
        c = json.loads(line)
        meta[c["doc_id"]] = (c["parent_id"], c["sensitivity"])
    items = [json.loads(l) for l in open(HERE / "knowledge_eval_set.jsonl", encoding="utf-8") if l.strip()]
    rows, lat, modes, leak, attack = [], [], [], 0, 0
    for it in items:
        plan = classify_chat_grounding(it["query"], knowledge_enabled=True)
        row = {**it, "route": plan.route, "knowledge_route": bool(plan.knowledge)}
        if it["group"] != "control":
            t0 = time.monotonic()
            res = await _qdrant_search(it["query"], None, top_k=TOP_K, scope="knowledge")
            lat.append((time.monotonic() - t0) * 1000)
            modes.append(re.search(r"\[(\w+)\]\s*$", res.summary or "").group(1) if re.search(r"\[(\w+)\]\s*$", res.summary or "") else "none")
            hits = parse(res.content or "")
            parents = [meta.get(h["doc_id"], (None, None))[0] for h in hits]
            attack += sum(1 for h in hits if meta.get(h["doc_id"], (None, "defensive"))[1] != "defensive")
            gold = set(it["gold_parents"])
            rank = next((i + 1 for i, p in enumerate(parents) if p in gold or (p and any(p.startswith(g + ".") for g in gold))), None)
            text = (res.content or "").lower()
            kp = [any(alt.lower() in text for alt in k.split("|")) for k in it["key_points"]]
            ent = await _qdrant_search(it["query"], None, top_k=10)  # default (entity) scope
            leak += sum(1 for h in parse(ent.content or "") if h["knowledge"])
            row.update(hits=[{**h, "parent": p} for h, p in zip(hits, parents)], rank=rank, hit5=bool(rank), rr=1 / rank if rank else 0.0,
                       kp_coverage=sum(kp) / len(kp) if kp else None)
        rows.append(row)
    scored = [r for r in rows if r["group"] != "control"]
    def agg(rs):
        n = len(rs)
        return {"n": n, "recall@5": round(sum(r["hit5"] for r in rs) / n, 3), "MRR": round(sum(r["rr"] for r in rs) / n, 3)} if n else {}
    ctrl = [r for r in rows if r["group"] == "control"]
    summary = {
        "overall": agg(scored),
        "by_group": {g: agg([r for r in scored if r["group"] == g]) for g in sorted({r["group"] for r in scored})},
        "vi": agg([r for r in scored if r["lang"] == "vi"]), "en": agg([r for r in scored if r["lang"] == "en"]),
        "routing": {"noncontrol_to_knowledge": round(sum(r["knowledge_route"] for r in scored) / len(scored), 3),
                    "noncontrol_by_route": {k: sum(1 for r in scored if r["route"] == k) for k in sorted({r["route"] for r in scored})},
                    "control_false_trigger": sum(r["knowledge_route"] for r in ctrl), "control_n": len(ctrl)},
        "attack_example_hits": attack, "entity_scope_knowledge_leak": leak,
        "mode_hybrid_fraction": round(sum(m == "hybrid" for m in modes) / len(modes), 3), "modes": {m: modes.count(m) for m in set(modes)},
        "latency_ms": {"p50": round(statistics.median(lat)), "p95": round(sorted(lat)[int(len(lat) * 0.95) - 1])},
        "kp_coverage_mean": round(statistics.mean(r["kp_coverage"] for r in scored if r["kp_coverage"] is not None), 3),
    }
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))

asyncio.run(main())
