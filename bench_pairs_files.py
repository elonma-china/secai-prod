"""18 directions x {short file, long file} on the isolated TEST container (18195).

"File" = blocks extracted from a document by translation/documents.py, each translated through
/ai/v3/translate (the LLM pipeline under test). The v2 file-job endpoint itself still only uses
the dedicated MT model, so this emulates file translation block by block.

  short file: first 8 blocks of the Vietnamese Volt Typhoon report (~1.3k chars)
  long  file: 123 blocks, ~911 chars/block (~112k chars). The long Vietnamese source is built by
              translating the genuine English long-30p.docx en->vi first (that run is itself the
              genuine-English en->vi long-file result).
For X->vi the input is the forward vi->X output of the same file (round trip), so every direction
gets real foreign-language text. Literal survival (CVE ids / version numbers) is checked too.
"""
import asyncio
import collections
import json
import re
import sys
import time

import httpx

BASE = "http://127.0.0.1:18195/ai/v3/translate"
SC = "/tmp/claude-1000/-home-ict-AIEngines-secai-secai-agent-gateway/f5647c66-87a3-4622-8082-3e95b9d130a9/scratchpad/translate-perf/"
VI_REPORT = json.load(open(SC + "blocks_vi.json"))["vt_report"]
EN_LONG = json.load(open(SC + "blocks.json"))["long30p"]
LANGS = ["en", "ja", "ko", "zh-Hans", "ar", "ru", "fr", "de", "es"]
LIT = re.compile(r"CVE-\d{4}-\d{4,7}|\d+(?:\.\d+){1,3}")
sem = asyncio.Semaphore(8)


async def call(c, text, src, dst, tries=3):
    last = "?"
    for k in range(tries):
        async with sem:
            try:
                r = await c.post(BASE, json={"text": text, "source_lang": src, "target_lang": dst}, timeout=120)
                d = r.json()
                if "translated_text" in d:
                    return d["translated_text"], "OK"
                last = d.get("error", {}).get("code", f"HTTP{r.status_code}")
            except Exception as e:  # noqa: BLE001
                last = f"EXC:{type(e).__name__}"
        if last == "RATE_LIMITED":
            await asyncio.sleep(5)
    return None, last


async def run(c, texts, src, dst):
    t0 = time.time()
    res = await asyncio.gather(*(call(c, t, src, dst) for t in texts))
    return res, time.time() - t0


def lit_kept(sources, outs):
    kept = tot = 0
    for s, o in zip(sources, outs):
        want = set(LIT.findall(s))
        if want and o is not None:
            tot += 1
            kept += all(w in o for w in want)
    return kept, tot


def fmt(codes):
    return "OK" if set(codes) == {"OK"} else dict(collections.Counter(codes))


async def main():
    limits = httpx.Limits(max_connections=64)
    async with httpx.AsyncClient(limits=limits) as c:
        print("== chuẩn bị nguồn tiếng Việt dài: dịch en->vi file tiếng Anh thật (123 đoạn) ==", flush=True)
        res, wall = await run(c, EN_LONG, "en", "vi")
        codes = [r[1] for r in res]
        print(f"  [GENUINE en->vi | file dài 123 đoạn] {fmt(codes)} {sum(x == 'OK' for x in codes)}/{len(res)} wall={wall:.0f}s", flush=True)
        vi_long = [r[0] if r[0] else EN_LONG[i] for i, r in enumerate(res)]  # fallback keeps size
        vi_short = VI_REPORT[:8]
        json.dump({"vi_long": vi_long}, open(SC + "vi_long.json", "w"), ensure_ascii=False)

        print("\n== 18 chiều x (file ngắn 8 đoạn | file dài 123 đoạn) ==", flush=True)
        for lang in LANGS:
            for label, src_texts in (("ngắn", vi_short), ("dài", vi_long)):
                fres, fwall = await run(c, src_texts, "vi", lang)
                fouts = [r[0] for r in fres]
                fcodes = [r[1] for r in fres]
                ok_idx = [i for i, r in enumerate(fres) if r[1] == "OK"]
                bres, bwall = await run(c, [fouts[i] for i in ok_idx], lang, "vi")
                bcodes = [r[1] for r in bres]
                fk, ft = lit_kept([src_texts[i] for i in ok_idx], fouts and [fouts[i] for i in ok_idx])
                bk, bt = lit_kept([src_texts[i] for i in ok_idx], [r[0] for r in bres])
                print(f"  {lang:8} file {label:4} | vi->{lang}: {sum(x=='OK' for x in fcodes)}/{len(fcodes)} {fmt(fcodes) if set(fcodes)!={'OK'} else ''} "
                      f"({fwall:.0f}s, literal {fk}/{ft}) | {lang}->vi: {sum(x=='OK' for x in bcodes)}/{len(bcodes)} "
                      f"{fmt(bcodes) if set(bcodes)!={'OK'} else ''} ({bwall:.0f}s, literal đi-về {bk}/{bt})", flush=True)


asyncio.run(main())
