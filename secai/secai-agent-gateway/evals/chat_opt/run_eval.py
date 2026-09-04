#!/usr/bin/env python3
"""Chạy toàn bộ 200 câu hỏi qua chatbot :8131 /ai/chat, lưu answer + metadata.
Dùng: python3 run_eval.py --out round_1/answers.jsonl [--base http://127.0.0.1:8131] [--conc 5]
"""
from __future__ import annotations
import argparse, asyncio, json, time, sys
from pathlib import Path
import httpx

HERE = Path(__file__).parent

_SECPRO_CATS = {"cve_lookup", "cve_detail", "poc_availability", "remediation",
                "cwe_capec", "relation", "aggregate_stats", "concept", "edge_unknown"}

def _src_of(cat):
    return "secpro" if cat in _SECPRO_CATS else "customer"

def load_questions(questions_file=None):
    rows = []
    if questions_file:  # single fixed set (e.g. questions_50.jsonl); derive source from category
        p = HERE / questions_file
        if not p.exists():
            print(f"THIẾU {p}", file=sys.stderr); sys.exit(1)
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            o.setdefault("source", _src_of(o.get("category", "")))
            rows.append(o)
        return rows
    for fn, src in (("questions_customer.jsonl", "customer"), ("questions_secpro.jsonl", "secpro")):
        p = HERE / fn
        if not p.exists():
            print(f"THIẾU {p}", file=sys.stderr); sys.exit(1)
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            o["source"] = src
            rows.append(o)
    return rows

async def ask(client, base, q, sem):
    async with sem:
        t0 = time.time()
        try:
            r = None
            for attempt in range(8):  # retry-on-429: self-pace to the 15/60s window
                r = await client.post(base + "/ai/chat",
                                      json={"user_id": f"eval-{q['id']}", "message": q["q"]},
                                      timeout=httpx.Timeout(120, connect=10))
                if r.status_code != 429:
                    break
                await asyncio.sleep(5 + attempt * 2)
            d = r.json()
            m = d.get("metadata", {}) or {}
            ic = m.get("input_classification") or {}
            return {**q, "http": r.status_code,
                    "answer": d.get("response") or d.get("answer") or d.get("message") or "",
                    "short_circuit": m.get("short_circuit"),
                    "ic_label": ic.get("label"), "ic_action": ic.get("action"),
                    "tool_registry": m.get("tool_registry"),
                    "elapsed": round(time.time() - t0, 1)}
        except Exception as e:
            return {**q, "http": 0, "answer": "", "error": str(e)[:160],
                    "elapsed": round(time.time() - t0, 1)}

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8131")
    ap.add_argument("--conc", type=int, default=5)
    ap.add_argument("--questions", default=None, help="single fixed question file (e.g. questions_50.jsonl)")
    a = ap.parse_args()
    rows = load_questions(a.questions)
    print(f"chạy {len(rows)} câu, conc={a.conc}, base={a.base}")
    sem = asyncio.Semaphore(a.conc)
    out = HERE / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        results = []
        done = 0
        tasks = [ask(client, a.base, q, sem) for q in rows]
        with out.open("w", encoding="utf-8") as f:
            for coro in asyncio.as_completed(tasks):
                res = await coro
                f.write(json.dumps(res, ensure_ascii=False) + "\n"); f.flush()
                results.append(res); done += 1
                if done % 20 == 0:
                    print(f"  {done}/{len(rows)} ({time.time()-t0:.0f}s)")
    errs = sum(1 for r in results if r.get("http") != 200)
    sc = sum(1 for r in results if r.get("short_circuit"))
    print(f"XONG {len(results)} câu, {errs} lỗi/non-200, {sc} short_circuit, {time.time()-t0:.0f}s -> {out}")

asyncio.run(main())
