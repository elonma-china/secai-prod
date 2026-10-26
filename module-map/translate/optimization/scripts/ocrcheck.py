import base64, json, sys, time, urllib.request, urllib.error
from pathlib import Path
tok=open('/home/ict/secai-opt/ocr.token').read().strip()
FX=Path('/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/fixtures/formats')
LITS=["svc_backup_01","P@ss!w0rd#2026$%^&*()_+","https://portal.example.org/login?user=admin&pass=Ab!9#x","192.168.10.25:8443","CVE-2024-3094","ops.team@example.org","sk-test-9f8E7d6C5b4A3"]
def ocr(path):
    body=json.dumps({"model":"Qwen/Qwen3-VL-2B-Instruct","image_base64":base64.b64encode(path.read_bytes()).decode(),"calibration_prompt":"conditional"}).encode()
    r=urllib.request.Request("http://127.0.0.1:28083/v1/ocr",data=body,headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json"})
    t=time.time()
    try:
        with urllib.request.urlopen(r,timeout=200) as x: d=json.loads(x.read())
    except urllib.error.HTTPError as e: return round(time.time()-t,1), f"HTTP{e.code}"
    return round(time.time()-t,1), (d["blocks"][0]["text"] if d["blocks"] else "")
ok=0; ts=[]; fails=0
for fmt in ("png","jpg"):
    for lang in ["en","vi","zh-Hans","ja","ko","fr","de","es","ru","ar"]:
        t,txt=ocr(FX/fmt/f"long-{lang}.{fmt}")
        if txt.startswith("HTTP"): fails+=1; print(fmt,lang,t,txt); continue
        miss=[l for l in LITS if l not in txt]; ok+=(not miss); ts.append(t)
        print(f"{fmt} {lang:8} {t:5}s chars={len(txt):5} literal {7-len(miss)}/7")
ts.sort(); print("full 7/7:",ok,"/20  http-fail:",fails,"  median s:",ts[len(ts)//2],"  max s:",ts[-1])
