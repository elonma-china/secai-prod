#!/usr/bin/env python3
"""Chấm chất lượng dịch tệp TXT theo gold của 04-test-data.json bằng chrF (n-gram ký tự 1..6, beta=2).
  quality.py --label q-bf16 [--ids FILE-en-vi,FILE-vi-en,...]   -> results/<label>-quality.json
Dùng API v2 của stack cô lập. Với đích zh/ja/ko tính chrF theo ký tự (không tách từ)."""
import argparse, json, sys, time
from collections import Counter
from pathlib import Path
sys.path.insert(0, "/home/ict/secai-opt")
import bench

DATA = json.load(open("/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/04-test-data.json"))
CASES = {c["id"]: c for c in DATA["cases"] if c["mode"] == "file" and (c.get("output_gold") or {}).get("text")}
CODE = {"zh-Hans": "zh"}


def chrf(hyp, ref, n=6, beta=2.0):
    def grams(s, k):
        s = "".join(s.split())
        return Counter(s[i:i + k] for i in range(len(s) - k + 1))
    p = r = 0.0; used = 0
    for k in range(1, n + 1):
        h, g = grams(hyp, k), grams(ref, k)
        if not h or not g:
            continue
        ov = sum((h & g).values())
        p += ov / sum(h.values()); r += ov / sum(g.values()); used += 1
    if not used:
        return 0.0
    p, r = p / used, r / used
    return 0.0 if p + r == 0 else (1 + beta ** 2) * p * r / (beta ** 2 * p + r) * 100


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--label", required=True); ap.add_argument("--ids", default="")
    a = ap.parse_args()
    ids = a.ids.split(",") if a.ids else list(CASES)
    rows = []; last = time.monotonic()
    for cid in ids:
        c = CASES[cid]; path = Path(c["input"]["file_path"])
        bench.CASES["q"] = ("files", path, False)
        src, tgt = CODE.get(c["source_lang"], c["source_lang"]), CODE.get(c["target_lang"], c["target_lang"])
        wait = 13.0 - (time.monotonic() - last)
        if wait > 0 and rows:
            time.sleep(wait)  # gateway: tối đa 5 upload/phút
        last = time.monotonic()
        rec = bench.run_case("q", src, tgt, 300, f"{a.label}-{cid}")
        out = (bench.OUT / f"{a.label}-{cid}-q.out.txt")
        hyp = (bench.OUT / f"{a.label}-{cid}-q.out.txt").read_text() if out.exists() else ""
        row = {"id": cid, "dir": f"{src}->{tgt}", "status": rec.get("status"), "wall_s": rec.get("wall_s"),
               "chrf": round(chrf(hyp, c["output_gold"]["text"]), 1) if hyp else None}
        rows.append(row); print(row, flush=True)
    ok = [r["chrf"] for r in rows if r["chrf"] is not None]
    summary = {"label": a.label, "n": len(rows), "mean_chrf": round(sum(ok) / len(ok), 2) if ok else None, "rows": rows}
    (bench.OUT / f"{a.label}-quality.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print("MEAN chrF", summary["mean_chrf"])


if __name__ == "__main__":
    main()
