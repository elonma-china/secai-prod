#!/usr/bin/env python3
"""Đo chất lượng retrieval: recall@5/@10, MRR trên eval set. Gọi _qdrant_search trực tiếp."""
import asyncio, json, re, sys
from pathlib import Path
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE.parent.parent/"src"))
from secai_gateway.retrieval.qdrant import _qdrant_search

CVE_RE=re.compile(r"CVE-\d{4}-\d{3,7}")
def ranked_cves(content:str)->list[str]:
    """Trích cve_id theo thứ tự result (mỗi block '### Result' lấy CVE đầu tiên)."""
    out=[]
    blocks=re.split(r"### Result \d+",content)
    for b in blocks[1:]:
        m=CVE_RE.search(b)
        if m: out.append(m.group(0).upper())
    if not out:  # fast-path/exact format khác → lấy toàn bộ theo thứ tự, dedup
        seen=set()
        for m in CVE_RE.finditer(content):
            c=m.group(0).upper()
            if c not in seen: seen.add(c); out.append(c)
    return out

async def one(q):
    res=await _qdrant_search(q["query"],None,top_k=10)
    content=getattr(res,"content","") or ""
    got=ranked_cves(content)
    rel=set(c.upper() for c in q["relevant_cves"])
    # rank của relevant đầu tiên (dedup theo thứ tự)
    seen=set(); ordered=[]
    for c in got:
        if c not in seen: seen.add(c); ordered.append(c)
    rank=next((i+1 for i,c in enumerate(ordered) if c in rel),None)
    return {"id":q["id"],"query":q["query"][:50],"got":ordered[:10],"rel":list(rel),
            "hit5":bool(rank and rank<=5),"hit10":bool(rank and rank<=10),"rr":(1.0/rank if rank else 0.0),"rank":rank}

async def main():
    qs=[json.loads(l) for l in open(HERE/"queries.jsonl") if l.strip()]
    rows=[]
    for q in qs:
        try: rows.append(await one(q))
        except Exception as e: rows.append({"id":q["id"],"query":q["query"][:50],"err":str(e)[:80],"hit5":False,"hit10":False,"rr":0.0,"rank":None})
    n=len(rows)
    r5=sum(r["hit5"] for r in rows)/n; r10=sum(r["hit10"] for r in rows)/n; mrr=sum(r["rr"] for r in rows)/n
    out={"n":n,"recall@5":round(r5,3),"recall@10":round(r10,3),"MRR":round(mrr,3),"rows":rows}
    (HERE/"baseline_results.json").write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(f"BASELINE n={n}  recall@5={r5:.3f}  recall@10={r10:.3f}  MRR={mrr:.3f}")
    print("--- FAIL (không ra CVE đúng trong top-10) ---")
    for r in rows:
        if not r["hit10"]: print(f"  {r['id']}: {r['query']} | rel={r.get('rel')} | got={r.get('got',[])[:5]} {r.get('err','')}")

asyncio.run(main())
