#!/usr/bin/env python3
"""So sánh kết quả giữa các lần tối ưu: thời gian + độ giống bản dịch baseline (difflib, 0..1)
và các chỉ số toàn vẹn. Dùng: compare.py baseline opt1 opt2 ..."""
import json, sys, difflib
from pathlib import Path
R = Path("/home/ict/secai-opt/results")
labels = sys.argv[1:]
rows = {}
for lab in labels:
    f = R / f"{lab}.jsonl"
    if not f.exists():
        continue
    for l in f.read_text().splitlines():
        r = json.loads(l); rows.setdefault(r["case"], {}).setdefault(lab, []).append(r)
print(f"{'case':8} {'label':14} {'status':10} {'wall_s':>8} {'speedup':>8} {'sim_vs_base':>11} {'lit':>5} {'marker':>6} {'empty':>5}")
for case, d in rows.items():
    base = d.get(labels[0], [None])[0]
    bt = (R / f"{labels[0]}-{case}.out.txt")
    btxt = bt.read_text() if bt.exists() else ""
    for lab in labels:
        for r in d.get(lab, []):
            o = R / f"{lab}-{case}.out.txt"
            sim = "-"
            if btxt and o.exists() and lab != labels[0]:
                sim = f"{difflib.SequenceMatcher(None, btxt[:20000], o.read_text()[:20000], autojunk=False).ratio():.3f}"
            sp = f"{base['wall_s']/r['wall_s']:.2f}x" if base and r.get('wall_s') and base.get('status') == 'succeeded' and r.get('status') == 'succeeded' else "-"
            print(f"{case:8} {lab:14} {r.get('status','?'):10} {r.get('wall_s','-'):>8} {sp:>8} {sim:>11} {str(r.get('literal_kept','-')):>5} {str(r.get('stray_markers','-')):>6} {str(r.get('empty_blocks','-')):>5}")
