"""1:1 A/B on identical inputs.
  A = incumbent LLM pipeline   (test container, :18195, orcarouter/Qwen3.8)
  B = dedicated MT specialist  (container as deployed in production, :18197, Hy-MT2-7B)
Run one system after the other per direction (never concurrently) so they do not fight for the
shared network/GPU. Everything is saved to ab_results.json so quality can be judged afterwards.

Inputs: the 42-block Vietnamese report for all 18 directions, then the 123-block long file for
vi->en, vi->ja, ja->vi. For X->vi the X text is A's own vi->X output for the same block (blocks
where A failed are dropped for BOTH systems), so the inputs are identical; this slightly favours
A's phrasing as input and is stated in the report.
"""
import asyncio
import json
import re
import statistics
import time

import httpx

SC = ("/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/"
      "f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/")
SYS = {"A": "http://127.0.0.1:18195/ai/v3/translate", "B": "http://127.0.0.1:18197/ai/v3/translate"}
SET_M = json.load(open(SC + "translate-perf/blocks_vi.json"))["vt_report"]
SET_L = json.load(open(SC + "translate-perf/vi_long.json"))["vi_long"]
LANGS = ["en", "ja", "ko", "zh-Hans", "ar", "ru", "fr", "de", "es"]
LIT = re.compile(r"CVE-\d{4}-\d{4,7}|\d+(?:\.\d+){1,3}")
CONC = 8
RESULTS = {}


async def one(c, sem, system, text, src, dst):
    async with sem:
        t0 = time.time()
        try:
            r = await c.post(SYS[system], json={"text": text, "source_lang": src, "target_lang": dst}, timeout=240)
            d = r.json()
            out = d.get("translated_text")
            return {"ok": out is not None, "code": "OK" if out is not None else d.get("error", {}).get("code", f"HTTP{r.status_code}"),
                    "lat": time.time() - t0, "out": out}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": f"EXC:{type(e).__name__}", "lat": time.time() - t0, "out": None}


async def run(c, system, texts, src, dst):
    sem = asyncio.Semaphore(CONC)
    t0 = time.time()
    res = await asyncio.gather(*(one(c, sem, system, t, src, dst) for t in texts))
    return res, time.time() - t0


def summ(res, wall):
    ok = [r for r in res if r["ok"]]
    lat = sorted(r["lat"] for r in ok) or [0]
    codes = {}
    for r in res:
        if not r["ok"]:
            codes[r["code"]] = codes.get(r["code"], 0) + 1
    return (f"OK {len(ok)}/{len(res)}" + (f" {codes}" if codes else "") +
            f" | wall {wall:.0f}s thr {len(res)/wall:.2f}/s p50 {statistics.median(lat):.1f}s p95 {lat[int(len(lat)*.95)-1 if len(lat)>1 else 0]:.1f}s")


def lit_kept(src_texts, results):
    kept = tot = 0
    for s, r in zip(src_texts, results):
        want = set(LIT.findall(s))
        if want and r["ok"]:
            tot += 1
            kept += all(w in r["out"] for w in want)
    return kept, tot


async def direction(c, key, texts, src, dst):
    out = {}
    for system in ("A", "B"):
        res, wall = await run(c, system, texts, src, dst)
        out[system] = res
        k, t = lit_kept(texts, res) if src == "vi" else (0, 0)
        print(f"  [{system}] {key:14} {summ(res, wall)}" + (f" | literal {k}/{t}" if t else ""), flush=True)
    RESULTS[key] = {"src": src, "dst": dst, "inputs": texts,
                    "A": [{k: v for k, v in r.items()} for r in out["A"]],
                    "B": [{k: v for k, v in r.items()} for r in out["B"]]}
    json.dump(RESULTS, open(SC + "ab_results.json", "w"), ensure_ascii=False)
    return out


async def main():
    limits = httpx.Limits(max_connections=64)
    async with httpx.AsyncClient(limits=limits) as c:
        print("== 1) 18 chiều, file 42 đoạn (cùng đầu vào cho A và B) ==", flush=True)
        a_fwd = {}
        for lang in LANGS:
            r = await direction(c, f"vi->{lang}", SET_M, "vi", lang)
            a_fwd[lang] = r["A"]
        for lang in LANGS:
            idx = [i for i, r in enumerate(a_fwd[lang]) if r["ok"]]
            texts = [a_fwd[lang][i]["out"] for i in idx]
            await direction(c, f"{lang}->vi", texts, lang, "vi")
        print("\n== 2) file dài 123 đoạn: vi->en, vi->ja, ja->vi ==", flush=True)
        long_ja = None
        for lang in ("en", "ja"):
            r = await direction(c, f"L vi->{lang}", SET_L, "vi", lang)
            if lang == "ja":
                long_ja = r["A"]
        idx = [i for i, r in enumerate(long_ja) if r["ok"]]
        await direction(c, "L ja->vi", [long_ja[i]["out"] for i in idx], "ja", "vi")
        print("\nDONE", flush=True)


asyncio.run(main())
