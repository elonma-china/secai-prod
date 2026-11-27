#!/usr/bin/env python3
"""Chấm CƠ HỌC kết quả run_core_eval.py để so prod với stack cùng bộ câu hỏi.

  python3 core_score.py a.json [b.json]     in số từng file; hai file thì chỉ so các id chung.

ĐÂY LÀ KIỂM TRA CHUỖI CON, không phải điểm của chính bộ eval: run_core_eval.py chỉ ghi câu trả lời/tool/thời gian,
còn điểm chất lượng (so với `expect`/`facts`/`must_not`) do bước chấm riêng của eval quyết định. Ở đây chỉ kiểm
(1) trả lời được: HTTP 200, không error, >= 50 ký tự; (2) với ca có facts máy kiểm được (facts.cve, facts.cvss,
facts.catalog_poc_count): các giá trị đó xuất hiện nguyên văn trong câu trả lời. Ca không có facts như vậy chỉ tính (1).
Hai số này là thước đo hồi quy rẻ, KHÔNG thay thế điểm chất lượng; đừng so với điểm chấm của eval."""
import json, math, statistics, sys


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
            "mean_s": round(statistics.mean(lat), 1) if lat else None, "p95_s": round(sorted(lat)[math.ceil(len(lat) * .95) - 1], 1) if lat else None}


if __name__ == "__main__":
    fs = [load(p) for p in sys.argv[1:3]]
    ids = sorted(set.intersection(*[set(f) for f in fs]))
    for p, f in zip(sys.argv[1:3], fs):
        print(p, json.dumps(summary(f, ids)))
