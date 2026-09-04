#!/usr/bin/env python3
"""Alias enrichment diện rộng: prepend 'Tên gọi phổ biến: <names>' vào core chunk của
CVE nổi tiếng + re-embed dense(1024)+BM25 → Qdrant (cve_kb_hybrid,cve_kb). Idempotent."""
import json, sys, re, urllib.request
sys.path.insert(0,"src")
QDRANT="http://127.0.0.1:6333"; COLS=("cve_kb_hybrid","cve_kb")
ENV={}
for l in open(".env"):
    l=l.strip()
    if l and not l.startswith("#") and "=" in l:
        k,_,v=l.partition("="); ENV[k.strip()]=v.strip().strip('"').strip("'")
EMBED_URL=ENV["EMBEDDING_API_URL"].rstrip("/"); EKEY=ENV.get("EMBEDDING_API_KEY",""); EMODEL=ENV["EMBEDDING_MODEL"]

# Bản đồ CVE → tên gọi phổ biến (aliases). Tập vuln nổi tiếng (không chỉ eval), giá trị chắc chắn.
ALIASES={
"CVE-2021-44228":["Log4Shell"],"CVE-2021-45046":["Log4Shell (bypass thứ hai)"],
"CVE-2017-0144":["EternalBlue","MS17-010"],"CVE-2020-1472":["Zerologon"],
"CVE-2021-34473":["ProxyShell"],"CVE-2021-34523":["ProxyShell"],"CVE-2021-31207":["ProxyShell"],
"CVE-2021-26855":["ProxyLogon"],"CVE-2019-0708":["BlueKeep"],"CVE-2021-34527":["PrintNightmare"],
"CVE-2022-30190":["Follina"],"CVE-2022-22965":["Spring4Shell"],"CVE-2022-22963":["Spring Cloud Function SpEL RCE"],
"CVE-2023-4966":["Citrix Bleed","CitrixBleed"],"CVE-2014-0160":["Heartbleed"],"CVE-2014-6271":["Shellshock","Bashdoor"],
"CVE-2016-5195":["Dirty COW"],"CVE-2021-4034":["PwnKit"],"CVE-2021-3156":["Baron Samedit","Sudo Baron Samedit"],
"CVE-2020-0796":["SMBGhost","CoronaBlue"],"CVE-2020-1350":["SIGRed"],"CVE-2020-1938":["Ghostcat"],
"CVE-2024-3094":["XZ Utils backdoor","xz backdoor"],"CVE-2022-42889":["Text4Shell","Act4Shell"],
"CVE-2017-5754":["Meltdown"],"CVE-2017-5753":["Spectre"],"CVE-2022-0847":["Dirty Pipe"],
"CVE-2024-6387":["regreSSHion"],"CVE-2023-48795":["Terrapin"],"CVE-2015-0235":["GHOST"],
"CVE-2018-7600":["Drupalgeddon2"],"CVE-2023-4911":["Looney Tunables"],"CVE-2017-13077":["KRACK"],
"CVE-2020-11896":["Ripple20"],"CVE-2014-3566":["POODLE"],"CVE-2016-0800":["DROWN"],
"CVE-2016-2183":["Sweet32"],"CVE-2016-2118":["Badlock"],"CVE-2016-3714":["ImageTragick"],
"CVE-2015-3456":["VENOM"],"CVE-2017-0781":["BlueBorne"],"CVE-2020-0601":["Curveball","ChainOfFools"],
"CVE-2020-10713":["BootHole"],"CVE-2021-33909":["Sequoia"],"CVE-2021-36942":["PetitPotam"],
"CVE-2021-36934":["HiveNightmare","SeriousSAM"],"CVE-2022-41040":["ProxyNotShell"],"CVE-2022-41082":["ProxyNotShell"],
"CVE-2023-34362":["MOVEit Transfer zero-day"],"CVE-2023-44487":["HTTP/2 Rapid Reset"],
"CVE-2021-38647":["OMIGOD"],"CVE-2022-29900":["Retbleed"],"CVE-2019-11510":["Pulse Secure arbitrary file read"],
"CVE-2019-19781":["Shitrix","Citrix ADC path traversal"],"CVE-2017-5638":["Equifax Struts2 RCE"],
"CVE-2018-13379":["Fortinet FortiOS SSL VPN path traversal"],"CVE-2024-3400":["PAN-OS GlobalProtect zero-day"],
"CVE-2023-2868":["Barracuda ESG zero-day"],"CVE-2023-20198":["Cisco IOS XE web UI zero-day"],
}

def http(url,method="GET",body=None,headers=None,timeout=120):
    data=None if body is None else json.dumps(body).encode()
    h={"Accept":"application/json"};
    if data:h["Content-Type"]="application/json"
    h.update(headers or {})
    return json.load(urllib.request.urlopen(urllib.request.Request(url,data=data,headers=h,method=method),timeout=timeout))
def embed(text):
    r=http(f"{EMBED_URL}/v1/embeddings",method="POST",body={"model":EMODEL,"input":[text[:6000]]},
           headers={"Authorization":f"Bearer {EKEY}"} if EKEY else None,timeout=120)
    return r["data"][0]["embedding"]
from secai_gateway.retrieval.bm25 import _load_sparse_model
_SP=_load_sparse_model()
def bm25(text):
    e=list(_SP.embed([text]))[0]; return {"indices":[int(i) for i in e.indices],"values":[float(v) for v in e.values]}
def scroll(col,cve):
    return http(f"{QDRANT}/collections/{col}/points/scroll",method="POST",
        body={"filter":{"must":[{"key":"cve_id","match":{"value":cve}},{"key":"chunk_kind","match":{"value":"core"}}]},
              "limit":4,"with_payload":True,"with_vector":False})["result"]["points"]
def set_text(col,pid,text):
    http(f"{QDRANT}/collections/{col}/points/payload?wait=true",method="POST",body={"payload":{"text":text},"points":[pid]})
def set_vec(col,pid,dense,has_bm25):
    v={"dense":dense}
    if has_bm25: v["bm25"]=bm25_val
    http(f"{QDRANT}/collections/{col}/points?wait=true",method="PUT",body={"points":[{"id":pid,"vector":v}]})
def has_bm25(col):
    return "bm25" in (http(f"{QDRANT}/collections/{col}")["result"]["config"]["params"].get("sparse_vectors") or {})

BM25_COLS={c:has_bm25(c) for c in COLS}
done=0; skip=0; err=0
for cve,names in ALIASES.items():
    prefix="Tên gọi phổ biến: "+", ".join(names)+". "
    for col in COLS:
        for pt in scroll(col,cve):
            t=str(pt["payload"].get("text") or "")
            if t.startswith("Tên gọi phổ biến:"):
                skip+=1; continue
            newt=prefix+t
            try:
                dense=embed(newt); bm25_val=bm25(newt) if BM25_COLS[col] else None
                v={"dense":dense}
                if bm25_val: v["bm25"]=bm25_val
                http(f"{QDRANT}/collections/{col}/points?wait=true",method="PUT",body={"points":[{"id":pt['id'],"vector":v}]})
                set_text(col,pt["id"],newt)
                done+=1
            except Exception as e:
                print(f"  SKIP {cve}/{col}/{pt['id']}: {e}",file=sys.stderr); err+=1
    if (done+skip)%20==0: print(f"  {cve}: done={done} skip={skip}",file=sys.stderr)
print(f"ENRICH xong: {len(ALIASES)} CVE | điểm cập nhật={done} | skip={skip} | err={err}",file=sys.stderr)
