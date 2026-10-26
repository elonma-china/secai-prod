#!/usr/bin/env python3
"""Chạy các ca P0 đợt 1 (formats-extra) qua API, kiểm giữ nguyên thông tin bằng check_output.py.
   p0run.py --label p0a [--only UC-C02,UC-C01]   -> results/<label>-p0.json"""
import argparse, json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, "/home/ict/secai-opt"); import bench
FX = Path("/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/fixtures")
M = json.load(open(FX / "formats-extra/manifest.json"))
# (uc, src, tgt) — hướng dịch mặc định theo ghi chú của từng ca
PLAN = [("UC-C02","vi","en"),("UC-C01","vi","en"),("UC-C07","en","vi"),("UC-C12","vi","en"),("UC-C13","vi","en"),("UC-C15","en","vi"),
        ("UC-C24","vi","en"),("UC-A01","vi","en"),("UC-A08","vi","en"),("UC-B08","vi","en"),("UC-B10","vi","en"),("UC-B13","vi","en"),
        ("UC-B02","vi","en"),("UC-A07","en","vi"),("UC-E18","en","vi"),("UC-E07","en","vi"),("UC-E10","en","vi"),
        ("UC-G08a","vi","en"),("UC-G08b","vi","en"),("UC-G06","vi","en"),("UC-G07","vi","en")]
ap = argparse.ArgumentParser(); ap.add_argument("--label", required=True); ap.add_argument("--only", default="")
a = ap.parse_args(); only = set(a.only.split(",")) if a.only else None
rows = []; last = 0.0
for uc, src, tgt in PLAN:
    if only and uc not in only: continue
    it = next(i for i in M["items"] if i["uc"] == uc); path = FX / it["path"]
    ext = path.suffix.lower(); route = "images" if ext in (".png", ".jpg", ".jpeg") else "files"
    bench.CASES["p"] = (route, path, False)
    w = 0.3 - (time.monotonic() - last)
    if w > 0: time.sleep(w)
    last = time.monotonic()
    rec = bench.run_case("p", src, tgt, 300, f"{a.label}-{uc}")
    out = bench.OUT / f"{a.label}-{uc}-p.out.txt"
    row = {"uc": uc, "file": path.name, "status": rec.get("status"), "wall_s": rec.get("wall_s"), "error": (rec.get("error") or {}).get("code") or rec.get("post_status") if rec.get("status") != "succeeded" else None}
    if rec.get("status") == "succeeded" and out.exists() and (it.get("preserve_literals") or it.get("checks")):
        r = subprocess.run([sys.executable, str(FX / "tools/check_output.py"), "--manifest", "formats-extra", "--id", uc, "--output", str(out)], capture_output=True, text=True)
        row["check"] = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[-120:]
        row["literal"] = next((l for l in r.stdout.splitlines() if "literal giữ" in l), "")
    rows.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
json.dump(rows, open(bench.OUT / f"{a.label}-p0.json", "w"), ensure_ascii=False, indent=1)
