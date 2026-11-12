"""All 18 supported directions on the isolated TEST container (18195).
Forward vi->X is translated for real, then the produced X text is fed back X->vi
(round trip), so every X->vi direction gets genuine foreign-language input.
Also checks that CVE ids / version numbers from the Vietnamese source survive the round trip.
"""
import asyncio
import collections
import json
import re
import time

import httpx

BASE = "http://127.0.0.1:18195/ai/v3/translate"
T = json.load(open("/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/"
                   "f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/blocks_vi.json"))["vt_report"]
LANGS = ["en", "ja", "ko", "zh-Hans", "ar", "ru", "fr", "de", "es"]
LIT = re.compile(r"CVE-\d{4}-\d{4,7}|\d+(?:\.\d+){1,3}")


async def call(c, sem, text, src, dst):
    async with sem:
        try:
            r = await c.post(BASE, json={"text": text, "source_lang": src, "target_lang": dst}, timeout=90)
            d = r.json()
            return d.get("translated_text"), ("OK" if "translated_text" in d else d["error"]["code"])
        except Exception as e:  # noqa: BLE001
            return None, f"EXC:{type(e).__name__}"


async def main():
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=64)) as c:
        for lang in LANGS:
            t0 = time.time()
            fwd = await asyncio.gather(*(call(c, sem, t, "vi", lang) for t in T))
            fcodes = collections.Counter(code for _, code in fwd)
            idx = [i for i, (txt, code) in enumerate(fwd) if code == "OK"]
            back = await asyncio.gather(*(call(c, sem, fwd[i][0], lang, "vi") for i in idx))
            bcodes = collections.Counter(code for _, code in back)
            kept = tot = 0
            for i, (btxt, code) in zip(idx, back):
                if code != "OK":
                    continue
                want = set(LIT.findall(T[i]))
                if want:
                    tot += 1
                    kept += all(w in btxt for w in want)
            print(f"{lang:8} vi->{lang}: {dict(fcodes)} | {lang}->vi: {dict(bcodes)} | "
                  f"literal giữ nguyên sau đi-về: {kept}/{tot} | {time.time()-t0:.0f}s", flush=True)


asyncio.run(main())
