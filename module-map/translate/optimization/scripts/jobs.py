#!/usr/bin/env python3
"""Liệt kê job trong stack cô lập (đọc DB, không gọi API => không dính rate-limit)."""
import subprocess, sys, json
code = r'''
import sqlite3, json
c=sqlite3.connect("file:/app/data/translate-v2/jobs.sqlite3?mode=ro",uri=True)
rows=[dict(zip(("id","kind","ext","status","stage","done","total","created","updated","err"),r)) for r in c.execute("select id,kind,extension,status,stage,completed,total,created,updated,error_code from jobs order by created")]
print(json.dumps(rows))
'''
out = subprocess.run(["docker","exec","secai-opt-gateway","python","-c",code],capture_output=True,text=True).stdout
for r in json.loads(out or "[]"):
    print(r["id"][:8], r["kind"], r["ext"], r["status"], r["stage"], f'{r["done"]}/{r["total"]}', f'wall={float(r["updated"])-float(r["created"]):.1f}s', r["err"] or "")
