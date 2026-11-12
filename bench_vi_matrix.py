"""Benchmark on GENUINE Vietnamese text (42 blocks of the Volt Typhoon report) against the
isolated TEST container (18195, incumbent LLM backend). Never touches production.
 1) multi-language: all 9 target languages x 42 blocks
 2) concurrency sweep: 1/4/8/16/32 in flight, same language
 3) concurrent users: 10 users, each translating the whole document into its own language,
    all at once (each user sequential inside, users parallel)
"""
import asyncio
import collections
import json
import statistics
import time

import httpx

BASE = "http://127.0.0.1:18195/ai/v3/translate"
TEXTS = json.load(open("/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/"
                       "f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/blocks_vi.json"))["vt_report"]
LANGS = ["en", "ja", "ko", "zh-Hans", "ar", "ru", "fr", "de", "es"]


async def one(client, text, target):
    t0 = time.time()
    try:
        r = await client.post(BASE, json={"text": text, "source_lang": "vi", "target_lang": target}, timeout=90)
        d = r.json()
        code = "OK" if "translated_text" in d else d.get("error", {}).get("code", f"HTTP{r.status_code}")
    except Exception as e:  # noqa: BLE001
        code = f"EXC:{type(e).__name__}"
    return code, time.time() - t0


async def run_batch(client, texts, target, conc):
    sem = asyncio.Semaphore(conc)

    async def g(t):
        async with sem:
            return await one(client, t, target)

    t0 = time.time()
    res = await asyncio.gather(*(g(t) for t in texts))
    return res, time.time() - t0


def pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(len(vals) * p))]


async def main():
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(limits=limits) as c:
        print(f"=== 1) Đa ngôn ngữ: {len(TEXTS)} đoạn tiếng Việt thật x {len(LANGS)} ngôn ngữ (8 song song) ===", flush=True)
        tot0 = time.time()
        for lang in LANGS:
            res, wall = await run_batch(c, TEXTS, lang, 8)
            cnt = collections.Counter(r[0] for r in res)
            print(f"  {lang:8} {dict(cnt)} wall={wall:5.1f}s", flush=True)
        print(f"  tổng {len(TEXTS)*len(LANGS)} lượt dịch trong {time.time()-tot0:.0f}s", flush=True)

        print("\n=== 2) Quét mức đồng thời (vi->en, 42 đoạn x2 = 84 lượt mỗi mức) ===", flush=True)
        for conc in (1, 4, 8, 16, 32):
            res, wall = await run_batch(c, TEXTS * 2, "en", conc)
            ok = sum(r[0] == "OK" for r in res)
            lat = [r[1] for r in res]
            print(f"  conc={conc:2} OK={ok}/{len(res)} wall={wall:5.1f}s thr={len(res)/wall:5.2f} req/s "
                  f"p50={statistics.median(lat):4.1f}s p95={pct(lat,.95):4.1f}s", flush=True)

        print("\n=== 3) 10 user đồng thời, mỗi user dịch cả tài liệu (42 đoạn) sang ngôn ngữ riêng ===", flush=True)
        users = LANGS[:9] + ["en"]

        async def user(lang):
            lat, codes = [], collections.Counter()
            t0 = time.time()
            for t in TEXTS:
                code, d = await one(c, t, lang)
                codes[code] += 1
                lat.append(d)
            return lang, time.time() - t0, codes, lat

        t0 = time.time()
        outs = await asyncio.gather(*(user(l) for l in users))
        wall = time.time() - t0
        alllat = [x for o in outs for x in o[3]]
        total_ok = sum(o[2]["OK"] for o in outs)
        for lang, dur, codes, _ in outs:
            print(f"  user[{lang:8}] doc xong sau {dur:5.1f}s {dict(codes)}", flush=True)
        print(f"  TỔNG: OK={total_ok}/{len(users)*len(TEXTS)} wall={wall:.1f}s "
              f"thr={len(users)*len(TEXTS)/wall:.2f} req/s p50={statistics.median(alllat):.1f}s "
              f"p95={pct(alllat,.95):.1f}s", flush=True)


asyncio.run(main())
