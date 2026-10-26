#!/usr/bin/env python3
"""Một VÒNG kiểm: 30 mẫu ngẫu nhiên (15 dài + 15 ngắn) qua API v2, chấm theo 03 (1, 1b, 1c).
  loop30.py --seed N --label loop1 [--limit K]
Hướng dịch: ngôn ngữ nguồn của mẫu -> vi; mẫu tiếng Việt -> xoay vòng en,zh,ja,ko,fr,de,es,ru,ar.
Ghi results/<label>-loop30.json + in bảng. Không chấm nghĩa dịch (việc của người/agent chấm theo 03 mục 1)."""
import argparse, json, subprocess, sys, time, re
from pathlib import Path
sys.path.insert(0, "/home/ict/secai-opt"); import bench
FX = Path("/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/fixtures")
ROT = ["en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"]
CODE = {"zh-Hans": "zh"}
ap = argparse.ArgumentParser(); ap.add_argument("--seed", type=int, required=True); ap.add_argument("--label", required=True); ap.add_argument("--limit", type=int, default=30)
a = ap.parse_args()
sel = json.loads(subprocess.check_output([sys.executable, str(FX / "tools/pick_eval_sample.py"), "--seed", str(a.seed), "--json"], text=True))["samples"]
man = {i["id"]: i for i in json.load(open(FX / "formats/manifest.json"))["items"]}
rows = []; k = 0
for s in sel[: a.limit]:
    it = man[s["id"]]; path = FX / s["path"]; ext = path.suffix.lower()
    src = CODE.get(s["lang"], s["lang"])
    if src == "vi": tgt = ROT[k % len(ROT)]; k += 1
    else: tgt = "vi"
    route = "images" if ext in (".png", ".jpg", ".jpeg") else "files"
    bench.CASES["s"] = (route, path, False)
    rec = bench.run_case("s", src, tgt, 400, f"{a.label}-{s['id']}")
    st = it["structure"]
    pages = st.get("pages") or st.get("sheets") or (5 if (s["format"] == "docx" and s["size_class"] == "long") else 1)
    out = bench.OUT / f"{a.label}-{s['id']}-s.out.txt"
    txt = out.read_text() if out.exists() else ""
    lits = it.get("preserve_literals") or []
    kept = [l for l in lits if l in txt]
    row = {"id": s["id"], "size": s["size_class"], "fmt": s["format"], "dir": f"{src}->{tgt}", "status": rec.get("status"),
           "error": (rec.get("error") or {}).get("code") if rec.get("status") != "succeeded" else None,
           "wall_s": rec.get("wall_s"), "pages": pages, "s_per_page": round(rec["wall_s"] / pages, 2) if rec.get("wall_s") and pages else None,
           "literal": f"{len(kept)}/{len(lits)}", "literal_missing": [l for l in lits if l not in txt] if txt else None,
           "stray_markers": rec.get("stray_markers"), "empty_blocks": rec.get("empty_blocks"), "needs_ocr": it.get("needs_ocr")}
    rows.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
ok = [r for r in rows if r["status"] == "succeeded"]
summ = {"seed": a.seed, "n": len(rows), "long": sum(r["size"] == "long" for r in rows), "short": sum(r["size"] == "short" for r in rows),
        "succeeded": len(ok), "literal_full": sum(1 for r in ok if r["literal"].split("/")[0] == r["literal"].split("/")[1]),
        "max_s_per_page_long": max((r["s_per_page"] for r in ok if r["size"] == "long" and r["s_per_page"]), default=None),
        "rows": rows}
json.dump(summ, open(bench.OUT / f"{a.label}-loop30.json", "w"), ensure_ascii=False, indent=1)
print("SUMMARY", {k: v for k, v in summ.items() if k != "rows"})
