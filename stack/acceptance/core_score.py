#!/usr/bin/env python3
"""Chấm cơ học kết quả run_core_eval.py (không dùng LLM-judge), để so prod với stack cùng bộ câu hỏi.
  python3 core_score.py a.json [b.json]     in điểm từng file; hai file thì so theo id chung.
Chỉ tính các ca có đáp án máy kiểm được: facts.cvss (cve_lookup/cve_alias), facts.cve, facts.catalog_poc_count.
Ca còn lại chỉ tính "trả lời được" (HTTP 200, không error, >= 50 ký tự). Đây là đo hồi quy, không phải điểm chất lượng ngữ nghĩa."""
import json, statistics, sys


def score(row):
    a, f = row.get("answer") or "", row.get("facts") or {}
    ok = row.get("status") == 200 and not row.get("error") and len(a) >= 50
    checks = []
    if f.get("cve"):
        checks.append(f["cve"] in a)
    if f.get("cvss") is not None:
        checks.append(str(f["cvss"]) in a)
    if f.get("catalog_poc_count") is not None:
        checks.append(str(f["catalog_poc_count"]) in a)
    return ok, (all(checks) if checks else None)


def load(p):
    d = json.load(open(p, encoding="utf-8"))
    return {r["id"]: r for r in d["rows"]}


def summary(rows, ids):
    sc = [score(rows[i]) for i in ids]
    ans = sum(s[0] for s in sc)
    fact = [s[1] for s in sc if s[1] is not None]
    lat = [rows[i].get("total_s") or 0 for i in ids]
    return {"n": len(ids), "answered": ans, "fact_checked": len(fact), "fact_ok": sum(fact),
            "mean_s": round(statistics.mean(lat), 1) if lat else None, "p95_s": round(sorted(lat)[int(len(lat) * .95) - 1], 1) if lat else None}


if __name__ == "__main__":
    fs = [load(p) for p in sys.argv[1:3]]
    ids = sorted(set.intersection(*[set(f) for f in fs]))
    for p, f in zip(sys.argv[1:3], fs):
        print(p, json.dumps(summary(f, ids)))
