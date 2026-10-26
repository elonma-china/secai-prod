#!/usr/bin/env python3
"""Load test 30 CCU qua TOÀN BỘ pipeline thật: gateway (/ai/chat) -> RAG retrieval
(Qdrant/Neo4j) -> guard/input-sanitize -> LLM — trên PRODUCTION :8131.

Workload: 90 câu hỏi KHÁC NHAU (không lặp lại), đọc từ data/gold_questions_91.json
— bộ ĐÃ ĐÓNG BĂNG (seed=42, trích từ gold-set độc lập testset-v2/acceptance_indep_*.jsonl,
1000 câu, mỗi câu gắn CVE/CWE/CVSS thật khác nhau) ngay trong thư mục này, để chạy lại
lần sau cho ra ĐÚNG cùng bộ câu hỏi mà không phụ thuộc testset-v2 gốc còn nguyên hay không.
Không dùng vài câu cố định lặp lại nhiều lần, để tránh (1) không đại diện traffic thật
và (2) trúng prefix-cache của vLLM (--enable-prefix-caching) làm số liệu đẹp giả tạo.
"""
import json
import time
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

URL = "http://localhost:8131/ai/chat"  # production (rate limit raised 90->150 to allow this)
DATA_FILE = Path(__file__).parent / "data" / "gold_questions_91.json"

_pool = json.loads(DATA_FILE.read_text(encoding="utf-8"))["questions"]
WARMUP_PROMPT = _pool[0]
PROMPTS = _pool[1:91]  # đúng 90 câu KHÁC NHAU dùng cho test


def pctl(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 3) if f != c else round(s[f], 3)


def call(i):
    prompt = PROMPTS[i]  # i in [0,89], PROMPTS has 90 unique entries — no repeats
    body = json.dumps({"message": prompt, "user_id": f"benchtest-{i}"}, ensure_ascii=False).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            r.read()
        return time.perf_counter() - t0, True
    except Exception:
        return time.perf_counter() - t0, False


def run_ccu(conc, total):
    lat, errors = [], 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for dt, ok in ex.map(call, range(total)):
            if ok:
                lat.append(dt)
            else:
                errors += 1
    wall = time.monotonic() - t0
    ok_n = total - errors
    return {"concurrency": conc, "total": total, "ok": ok_n, "errors": errors,
            "wall_s": round(wall, 2), "rps": round(ok_n / wall, 3) if wall else None,
            "p50_s": round(pctl(lat, 50), 3) if lat else None,
            "p95_s": round(pctl(lat, 95), 3) if lat else None,
            "p99_s": round(pctl(lat, 99), 3) if lat else None}


def _warmup():
    body = json.dumps({"message": WARMUP_PROMPT, "user_id": "benchtest-warmup"}, ensure_ascii=False).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        r.read()


if __name__ == "__main__":
    _warmup()
    ccu30 = run_ccu(30, 90)
    out = {"target": URL,
           "scope": "full pipeline (gateway+RAG+guard+LLM) trên PRODUCTION, đúng 30 CCU",
           "workload": "90 câu hỏi KHÁC NHAU (không lặp) từ gold-set độc lập testset-v2/acceptance_indep_*.jsonl (seed=42)",
           "ccu30": ccu30}
    print("BENCH_FULLPIPE_RESULT " + json.dumps(out, ensure_ascii=False))
