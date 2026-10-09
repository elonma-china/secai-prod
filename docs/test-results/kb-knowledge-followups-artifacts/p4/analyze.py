import json,random,collections,statistics
sc={"PASS":1,"PARTIAL":.5,"FAIL":0}
lm=json.load(open('judge/labelmap.json'))
out={}
for j in range(3): out.update(json.load(open(f'judge/out_{j}.json')))
per=collections.defaultdict(lambda:{"base":[], "new":[]}); fab={"base":0,"new":0}
grp={}
items={}
for j in range(3):
    for it in json.load(open(f'judge/in_{j}.json')): grp[it['id']]=it['group']
for qid,labs in out.items():
    for l,v in labs.items():
        side=lm[qid][l][:-1]
        per[qid][side].append(sc[v['v']]); fab[side]+=bool(v.get('fab'))
q={k:(statistics.mean(v['base']),statistics.mean(v['new'])) for k,v in per.items()}
b=statistics.mean(x[0] for x in q.values())*100; n=statistics.mean(x[1] for x in q.values())*100
print(f"n={len(q)} base={b:.1f} new={n:.1f} diff={n-b:+.1f}  fab base={fab['base']} new={fab['new']}")
diffs=[x[1]-x[0] for x in q.values()]
random.seed(1); bs=sorted(statistics.mean(random.choices(diffs,k=len(diffs))) for _ in range(5000))
print("paired CI95 [%.1f, %.1f] pts"%(bs[125]*100,bs[4875]*100),"better",sum(d>0 for d in diffs),"worse",sum(d<0 for d in diffs),"equal",sum(d==0 for d in diffs))
g=collections.defaultdict(list)
for k,x in q.items(): g[grp[k]].append(x)
for k,v in sorted(g.items()): print(f"  {k:16s} n={len(v):2d} base={statistics.mean(a for a,_ in v)*100:5.1f} new={statistics.mean(b for _,b in v)*100:5.1f}")
print("worse:",[(k,round(x[0],2),round(x[1],2)) for k,x in q.items() if x[1]<x[0]-0.2])
