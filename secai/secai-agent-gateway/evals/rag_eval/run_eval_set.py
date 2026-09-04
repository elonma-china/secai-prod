#!/usr/bin/env python3
"""Chạy eval_set.jsonl → recall@5/@10, MRR TỔNG + THEO NHÓM (retrieval); aggregate/edge = answer-level."""
import asyncio,json,re,sys
from pathlib import Path
from collections import defaultdict
HERE=Path(__file__).parent; sys.path.insert(0,str(HERE.parent.parent/"src"))
from secai_gateway.retrieval.qdrant import _qdrant_search
CVE=re.compile(r"CVE-\d{4}-\d{3,7}")
RECALL_CATS=["alias","description","product","cwe","multi_hop","exact_id"]
def ranked(c):
    out=[];seen=set()
    for b in re.split(r"### Result \d+",c)[1:]:
        m=CVE.search(b)
        if m and m.group(0).upper() not in seen: seen.add(m.group(0).upper());out.append(m.group(0).upper())
    return out
async def one(q):
    res=await _qdrant_search(q["query"],None,top_k=10)
    got=ranked(getattr(res,"content","") or "")
    rel=set(c.upper() for c in q.get("relevant_cves") or [])
    rank=next((i+1 for i,c in enumerate(got) if c in rel),None) if rel else None
    return {**q,"got":got[:10],"rank":rank,"hit5":bool(rank and rank<=5),"hit10":bool(rank and rank<=10),"rr":1.0/rank if rank else 0.0}
async def main():
    qs=[json.loads(l) for l in open(HERE/"eval_set.jsonl") if l.strip()]
    rows=[await one(q) for q in qs]
    scored=[r for r in rows if r["category"] in RECALL_CATS]
    def agg(rs):
        n=len(rs); return (round(sum(r['hit5'] for r in rs)/n,3),round(sum(r['hit10'] for r in rs)/n,3),round(sum(r['rr'] for r in rs)/n,3)) if n else (0,0,0)
    r5,r10,mrr=agg(scored)
    print(f"=== RETRIEVAL (n={len(scored)}): recall@5={r5} recall@10={r10} MRR={mrr} ===")
    bycat=defaultdict(list)
    for r in rows: bycat[r['category']].append(r)
    for cat in RECALL_CATS:
        a=agg(bycat[cat]); print(f"  {cat:12s} n={len(bycat[cat]):2d}: recall@5={a[0]:.3f} recall@10={a[1]:.3f} MRR={a[2]:.3f}")
    print(f"  aggregate    n={len(bycat['aggregate'])}: answer-level (đếm/cũ-mới nhất) — cần kb_stats tool, không tính recall")
    print(f"  edge         n={len(bycat['edge'])}: answer-level ('không tìm thấy') — không tính recall")
    (HERE/"eval_set_results.json").write_text(json.dumps({"overall":{"recall@5":r5,"recall@10":r10,"MRR":mrr,"n":len(scored)},"rows":rows},ensure_ascii=False,indent=2))
asyncio.run(main())
