#!/usr/bin/env python3
"""bench_answers_concurrent.py — Sinh đáp án 1000 mẫu QUA AGENT, ĐA LUỒNG.

Khác acceptance_runner (tuần tự, chống wedge): bắn N request đồng thời tới backend để val 1k
trong ~15 phút (thinking đã tắt → mỗi câu nhanh). Output results.jsonl khớp gen_answers.py.

Mỗi câu: POST /session → POST /session/{sid}/message (blocking) → GET messages → trích text+tools.
Resume: skip id đã có trong results.jsonl. Lỗi 1 câu → ghi error, không chết.

Usage:
  ACC_BASE=http://localhost:4096 ACC_PROVIDER=runpod-secai ACC_MODEL=... \
  OPENCODE_SERVER_USERNAME=.. OPENCODE_SERVER_PASSWORD=.. \
  python3 bench_answers_concurrent.py --dataset /opt/secai/datasets-acceptance \
    --out /opt/secai/kpi/raw-logs/results.jsonl --workers 14 [--limit N]
"""
import argparse
import base64
import glob
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

BASE = os.environ.get("ACC_BASE", "http://localhost:4096").rstrip("/")
PROVIDER = os.environ.get("ACC_PROVIDER", "runpod-secai")
MODEL = os.environ.get("ACC_MODEL", "Qwen/Qwen3-30B-A3B")
AGENT = os.environ.get("ACC_AGENT", "build")
_u = os.environ.get("OPENCODE_SERVER_USERNAME", "")
_p = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
AUTH = "Basic " + base64.b64encode(f"{_u}:{_p}".encode()).decode() if _p else ""


def _req(method, path, body=None, timeout=240):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if AUTH:
        h["Authorization"] = AUTH
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as x:
        return json.loads(x.read().decode())


def answer_one(s):
    qid = s["id"]
    rec = {"id": qid, "capability": s.get("capability"), "bucket": s.get("bucket"),
           "difficulty": s.get("difficulty"), "input": s.get("input"),
           "text": "", "tools": [], "ttftMs": None, "totalMs": None, "error": None}
    try:
        sid = _req("POST", "/session", {"agent": AGENT})["id"]
        t0 = time.time()
        _req("POST", f"/session/{sid}/message",
             {"agent": AGENT, "model": {"providerID": PROVIDER, "modelID": MODEL},
              "parts": [{"type": "text", "text": s["input"]}]}, timeout=240)
        rec["totalMs"] = int((time.time() - t0) * 1000)
        msgs = _req("GET", f"/session/{sid}/message", timeout=30)
        # opencode tách 1 lượt agent thành NHIỀU assistant message (tool-call + answer) →
        # AGGREGATE text + tools qua TẤT CẢ assistant message (đừng chỉ lấy message cuối).
        texts, tools = [], []
        for m in msgs:
            if (m.get("info") or {}).get("role") == "assistant":
                ps = m.get("parts", [])
                texts.append(" ".join(p.get("text", "") for p in ps if p.get("type") == "text"))
                tools += [p.get("tool") for p in ps if p.get("type") == "tool"]
        rec["text"] = " ".join(t for t in texts if t).strip()
        rec["tools"] = tools
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="/opt/secai/datasets-acceptance")
    ap.add_argument("--out", default="/opt/secai/kpi/raw-logs/results.jsonl")
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    qs = []
    for f in sorted(glob.glob(os.path.join(args.dataset, "*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                qs.append(json.loads(line))
    if args.limit:
        qs = qs[: args.limit]

    done = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    todo = [s for s in qs if s["id"] not in done]
    print(f"== answers concurrent: {len(todo)} câu (skip {len(done)} done) · workers={args.workers} · model={MODEL} ==", flush=True)

    lock = Lock()
    out = open(args.out, "a", encoding="utf-8")
    n = [0]
    nerr = [0]
    t0 = time.time()

    def work(s):
        r = answer_one(s)
        with lock:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
            n[0] += 1
            if r["error"]:
                nerr[0] += 1
            if n[0] % 25 == 0 or n[0] == len(todo):
                rate = n[0] / max(1e-9, time.time() - t0)
                eta = (len(todo) - n[0]) / max(1e-9, rate)
                print(f"  {n[0]}/{len(todo)} · {rate:.2f} q/s · err={nerr[0]} · ETA {eta/60:.1f}m", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"== XONG: {n[0]} câu · {nerr[0]} lỗi · {(time.time()-t0)/60:.1f} phút ==", flush=True)


if __name__ == "__main__":
    main()
