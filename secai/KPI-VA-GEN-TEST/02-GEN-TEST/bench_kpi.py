#!/usr/bin/env python3
"""bench_kpi.py — Bench nhanh các KPI hợp đồng secAI trên gateway/model đang chạy, có
thanh tiến trình trực tiếp trên màn hình + bảng tổng kết so với ngưỡng hợp đồng.

Gồm 2 nhóm đo NHANH (vài phút, tự đo lại từng request, thanh tiến trình mượt) và
1 nhóm CHẤT LƯỢNG (nhiều giờ, giao lại cho pipeline gốc `chay_eval_gateway_moi.sh`
vì cần retrieval + LLM-judge + faithfulness, không hợp lý viết lại từ đầu):

  Nhóm B — 5 KPI hiệu năng chatflow (qua gateway, ~1-2 phút @ concurrency mặc định)
  Nhóm C — 3 KPI throughput mô hình (thẳng vào endpoint LLM/embed/rerank, ~1-2 phút)
  Nhóm A — 11 KPI chất lượng (1000 câu, 1-4+ giờ) — cần --group a hoặc all, có cảnh báo

Dùng:
  python3 bench_kpi.py                          # mặc định: nhóm B + C (nhanh)
  python3 bench_kpi.py --group c                 # chỉ nhóm C
  python3 bench_kpi.py --group b --concurrency 30
  python3 bench_kpi.py --group a --dataset ./testset-v2   # nhóm A (chậm, cần xác nhận)
  python3 bench_kpi.py --group all --dataset ./testset-v2

Ngưỡng lấy từ hợp đồng gốc (source/docs/task-AI-requirements.md) + quyết định kinh
doanh đã chốt 2026-08-28: R-PERF-TPS vượt trần 35 tok/s vẫn tính ĐẠT (dải chỉ có ý
nghĩa "tối thiểu 20", vượt nhanh hơn không phải lỗi) — xem cờ --tps-band-strict nếu
muốn chấm nghiêm ngặt cả hai đầu dải như bản gốc.

Thuần thư viện chuẩn Python (urllib/threading/argparse) — không cần cài gì.

LƯU Ý R-MODEL-EMB1200: script này đo từ máy chạy nó (WAN tới endpoint vast.ai), nên số
đo được thường thấp hơn nhiều so với ngưỡng 1200 emb/s dù mô hình vẫn đạt thật — xác
minh 2026-08-27 bằng cách SSH thẳng vào instance đo on-host: WAN ~117-500 emb/s vs
on-host ~2600 emb/s (ngưỡng đo trên A100 tại chỗ, không tính RTT mạng). TRƯỢT ở đây
không tự động nghĩa là hệ thống yếu — cần đo on-host mới kết luận được.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

HERE = Path(__file__).resolve().parent

# ── cấu hình mặc định — override qua env hoặc .env.bench đã dùng thành công trước đó ──
GATEWAY_BASE = os.environ.get("ACC_BASE", "http://127.0.0.1:8131").rstrip("/")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://80.188.223.202:11444/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "DuyTa/Cyber-F1-AWQ")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-secai2026")
EMBED_URL = os.environ.get("EMBED_URL", "http://80.188.223.202:11469").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "DuyTa/sec-embedding")
RERANK_URL = os.environ.get("RERANK_URL", "http://80.188.223.202:11441").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "DuyTa/sec-rerank")

PERF_BENCH_DATASET = Path(
    os.environ.get("PERF_BENCH_DATASET", "/home/ict/secai-88/perf-bench/perf_bench_100.jsonl"))
GATEWAY_ADAPTERS_DIR = HERE / "adapters"
KPI_DIR = Path(os.environ.get("KPI_DIR", "/home/ict/secai-88/eval-standalone"))

# ── nguồn sự thật ngưỡng — task-AI-requirements.md + BANG-KPI.md ──────────────────
# op: ">="  giá trị đo phải >= target
#     "<="  giá trị đo phải <= target
#     "range"        phải nằm trong [lo, hi]
#     "range_floor"  phải >= lo; vượt hi KHÔNG bị trừ (quyết định 2026-08-28, xem --tps-band-strict)
#     "ccu"          tham số đầu vào (số luồng đã chạy), đạt khi 0 lỗi
KPI_SPEC = {
    "R-MODEL-EMB1200":  {"group": "C", "name": "Embedding throughput",      "unit": "emb/s",  "op": ">=",         "target": 1200},
    "R-MODEL-RERANK180": {"group": "C", "name": "Reranker throughput",      "unit": "docs/s", "op": ">=",         "target": 180},
    "R-LLM-TPS28":      {"group": "C", "name": "LLM throughput",            "unit": "tok/s",  "op": ">=",         "target": 28},
    "R-PERF-TPS":       {"group": "B", "name": "TPS chatflow (mỗi luồng)",  "unit": "tok/s",  "op": "range_floor", "lo": 20, "hi": 35},
    "R-PERF-TTFT":      {"group": "B", "name": "TTFT p50",                  "unit": "ms",     "op": "range",      "lo": 900, "hi": 1500},
    "R-PERF-RPS":       {"group": "B", "name": "RPS ổn định",               "unit": "RPS",    "op": ">=",         "target": 0.6},
    "R-PERF-CCU":       {"group": "B", "name": "CCU đồng thời",             "unit": "luồng",  "op": "ccu",        "target": 30},
    "R-CTRL-RESP15":    {"group": "B", "name": "Response p50",              "unit": "ms",     "op": "<=",         "target": 15000},
    # Nhóm A — chỉ hiển thị trong banner + bảng tổng kết; đo thật qua chay_eval_gateway_moi.sh
    "R-KPI-EMB96":      {"group": "A", "name": "Embedding Hit Rate@10",     "unit": "%", "op": ">",  "target": 96},
    "R-KPI-RERANK90":   {"group": "A", "name": "Reranking Precision@1",     "unit": "%", "op": ">",  "target": 90},
    "R-KPI-FAITH98":    {"group": "A", "name": "Faithfulness (RAGAS)",      "unit": "%", "op": ">",  "target": 98},
    "R-KPI-RELEV90":    {"group": "A", "name": "Answer Relevance",          "unit": "%", "op": ">",  "target": 90},
    "R-KPI-ENTITY100":  {"group": "A", "name": "Entity Precision CVE/CWE",  "unit": "%", "op": ">=", "target": 100},
    "R-KPI-HALLU2":     {"group": "A", "name": "Hallucination Rate",        "unit": "%", "op": "<",  "target": 2},
    "R-LLM-SAST85":     {"group": "A", "name": "SAST accuracy",             "unit": "%", "op": ">=", "target": 85},
    "R-LLM-EXPLOIT85":  {"group": "A", "name": "Exploit Explanation",       "unit": "%", "op": ">=", "target": 85},
    "R-LLM-REMED60":    {"group": "A", "name": "Auto-remediation",          "unit": "%", "op": ">=", "target": 60},
    "R-SEC-PII":        {"group": "A", "name": "Sanitization PII/Secret F1", "unit": "%", "op": ">", "target": 90},
    "R-KPI-RADAR90":    {"group": "A", "name": "Radar coverage area",       "unit": "%", "op": ">",  "target": 90},
}
GROUP_NAMES = {"A": "Nhóm A — 11 KPI chất lượng AI", "B": "Nhóm B — 5 KPI hiệu năng chatflow",
               "C": "Nhóm C — 3 KPI throughput mô hình"}


# ── tiện ích hiển thị ──────────────────────────────────────────────────────────────
def _isatty() -> bool:
    return sys.stdout.isatty()


def bar(done: int, total: int, label: str, width: int = 28, extra: str = "") -> None:
    """Thanh tiến trình 1 dòng, ghi đè bằng \\r — không cần thư viện ngoài."""
    total = max(total, 1)
    frac = min(1.0, done / total)
    filled = int(width * frac)
    b = "█" * filled + "░" * (width - filled)
    line = f"  [{b}] {int(frac*100):3d}% ({done}/{total}) {label}"
    if extra:
        line += f" — {extra}"
    if _isatty():
        sys.stdout.write("\r" + line.ljust(100))
        sys.stdout.flush()
        if done >= total:
            sys.stdout.write("\n")
    else:
        if done >= total or done % max(1, total // 10) == 0:
            print(line)


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(int(round(p / 100 * (len(xs) - 1))), len(xs) - 1)]


def banner(groups: list[str]) -> None:
    print("=" * 78)
    print("secAI — Bench KPI hợp đồng")
    print("=" * 78)
    n_total = 0
    for g in groups:
        codes = [c for c, s in KPI_SPEC.items() if s["group"] == g]
        n_total += len(codes)
        print(f"\n{GROUP_NAMES[g]} ({len(codes)} KPI):")
        for c in codes:
            s = KPI_SPEC[c]
            if s["op"] == "range" or s["op"] == "range_floor":
                thr = f"{s['lo']}–{s['hi']}"
            elif s["op"] == "ccu":
                thr = f"~{s['target']}"
            else:
                thr = f"{s['op']} {s['target']}"
            print(f"  {c:<20} {s['name']:<32} ngưỡng {thr} {s['unit']}")
    print(f"\nSẽ đo {n_total} KPI. Gateway: {GATEWAY_BASE} · LLM: {LLM_MODEL} · Embed: {EMBED_MODEL} · Rerank: {RERANK_MODEL}")
    print("=" * 78 + "\n")


# ── Nhóm C — throughput mô hình (thẳng endpoint, không qua gateway) ───────────────
def _post_json(url: str, body: dict, headers: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def bench_embed_throughput(conc_levels=(8, 16, 32), batch=64, reqs_per_level=64) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
    texts = [f"vulnerability test query about CVE-2021-{1000+i} and CWE-{79+i} injection overflow"
             for i in range(batch)]
    best = 0.0
    done_total = 0
    total_work = len(conc_levels) * reqs_per_level
    for conc in conc_levels:
        n_ok = [0]
        lock = Lock()
        t0 = time.time()

        def one(_):
            nonlocal done_total
            try:
                _post_json(f"{EMBED_URL}/v1/embeddings", {"model": EMBED_MODEL, "input": texts}, headers)
                with lock:
                    n_ok[0] += 1
            except Exception:
                pass
            with lock:
                done_total += 1
                bar(done_total, total_work, "Nhóm C · Embedding throughput", extra=f"conc={conc}")

        with ThreadPoolExecutor(max_workers=conc) as ex:
            list(ex.map(one, range(reqs_per_level)))
        elapsed = time.time() - t0
        rate = (n_ok[0] * batch) / elapsed if elapsed > 0 else 0.0
        best = max(best, rate)
    return {"R-MODEL-EMB1200": best}


def bench_rerank_throughput(conc_levels=(8, 16, 32), batch=50, reqs_per_level=64) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
    docs = [f"doc {j}: SQL injection buffer overflow XSS RCE privilege escalation in product version {j}"
            for j in range(batch)]
    best = 0.0
    done_total = 0
    total_work = len(conc_levels) * reqs_per_level
    for conc in conc_levels:
        n_ok = [0]
        lock = Lock()
        t0 = time.time()

        def one(_):
            nonlocal done_total
            try:
                _post_json(f"{RERANK_URL}/v1/rerank",
                           {"model": RERANK_MODEL, "query": "injection vulnerability RCE", "documents": docs},
                           headers)
                with lock:
                    n_ok[0] += 1
            except Exception:
                pass
            with lock:
                done_total += 1
                bar(done_total, total_work, "Nhóm C · Reranker throughput", extra=f"conc={conc}")

        with ThreadPoolExecutor(max_workers=conc) as ex:
            list(ex.map(one, range(reqs_per_level)))
        elapsed = time.time() - t0
        rate = (n_ok[0] * batch) / elapsed if elapsed > 0 else 0.0
        best = max(best, rate)
    return {"R-MODEL-RERANK180": best}


LLM_PROMPT = ("Bạn là trợ lý an toàn thông tin. Trình bày CHI TIẾT về CVE-2021-44228 (Log4Shell): "
              "mô tả, mức độ và điểm CVSS, vector tấn công JNDI, sản phẩm/phiên bản ảnh hưởng, và các "
              "bước khắc phục cụ thể. Trả lời đầy đủ bằng tiếng Việt.")


def bench_llm_tps(ccu: int = 30, total_reqs: int = 60, max_tokens: int = 512) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
    url = f"{LLM_BASE_URL}/chat/completions"
    tps_list, done = [], [0]
    lock = Lock()

    def one(_):
        body = {"model": LLM_MODEL, "messages": [{"role": "user", "content": LLM_PROMPT}],
                "max_tokens": max_tokens, "temperature": 0.0, "stream": True}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        t0 = time.time()
        ttft, toks = None, 0
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                for raw in r:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        d = json.loads(data)["choices"][0].get("delta", {})
                        delta = d.get("content") or d.get("reasoning")
                    except Exception:
                        delta = None
                    if delta:
                        if ttft is None:
                            ttft = time.time() - t0
                        toks += 1
        except Exception:
            pass
        e2e = time.time() - t0
        with lock:
            done[0] += 1
            bar(done[0], total_reqs, "Nhóm C · LLM throughput (tok/s)", extra=f"ccu={ccu}")
        if ttft and toks > 20 and (e2e - ttft) > 0:
            tps_list.append(toks / (e2e - ttft))

    with ThreadPoolExecutor(max_workers=ccu) as ex:
        list(ex.map(one, range(total_reqs)))
    p50 = pct(tps_list, 50) or 0.0
    return {"R-LLM-TPS28": p50}


# ── Nhóm B — hiệu năng chatflow qua gateway (SSE /ai/chat/stream) ─────────────────
def _load_perf_dataset(n: int) -> list[dict]:
    if PERF_BENCH_DATASET.exists():
        items = [json.loads(l) for l in PERF_BENCH_DATASET.read_text(encoding="utf-8").splitlines() if l.strip()]
        return items[:n] if n else items
    # fallback tự sinh nếu không có file perf_bench_100.jsonl sẵn
    return [{"id": f"synthetic_{i:03d}", "input": LLM_PROMPT} for i in range(n)]


class _RateLimiter:
    """Sliding-window tự giới tốc — margin dưới ngưỡng thật của gateway (90 req/60s cho
    /ai/chat kể từ 2026-08-27). Không giới tốc thì @concurrency=30 dễ chạm 429 gần cuối
    đợt tải (đã quan sát thật: 10/100 request 429 khi không có pacer)."""

    def __init__(self, max_calls: int = 80, period_s: float = 62.0) -> None:
        self.max_calls, self.period_s = max_calls, period_s
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                while self._calls and self._calls[0] <= now - self.period_s:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self._calls[0] + self.period_s - now
            time.sleep(max(sleep_for, 0.05))


_rate_limiter = _RateLimiter()


def _stream_chat(question: str, max_429_retries: int = 3) -> dict:
    url = f"{GATEWAY_BASE}/ai/chat/stream"
    body = json.dumps({"message": question}).encode()
    for attempt in range(max_429_retries + 1):
        _rate_limiter.acquire()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.time()
        ttft = None
        final_text = ""
        err = None
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                buf = b""
                for line in r:
                    buf += line
                    if buf.endswith(b"\n\n") or buf == b"\n":
                        frame = buf.decode("utf-8", "ignore").strip()
                        buf = b""
                        if not frame or not frame.startswith("event:"):
                            continue
                        lines = frame.split("\n")
                        ev = lines[0].replace("event:", "").strip()
                        data_line = next((l for l in lines if l.startswith("data:")), "data: {}")
                        try:
                            data = json.loads(data_line[5:].strip())
                        except Exception:
                            data = {}
                        if ev == "token":
                            if ttft is None:
                                ttft = time.time() - t0
                        elif ev == "final":
                            final_text = data.get("content") or data.get("answer") or ""
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_429_retries:
                time.sleep(3)
                continue
            err = f"HTTP{e.code}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        total = time.time() - t0
        tokens_out = max(len(final_text) // 4, 1) if final_text else 0
        return {"ttftMs": (ttft * 1000) if ttft else None, "totalMs": total * 1000,
                "tokens_out": tokens_out, "error": err}
    return {"ttftMs": None, "totalMs": None, "tokens_out": 0, "error": "HTTP429:retries_exhausted"}


def bench_group_b(concurrency: int = 30, n: int = 100) -> dict:
    items = _load_perf_dataset(n)
    n = len(items)
    results = []
    done = [0]
    lock = Lock()

    def one(item):
        r = _stream_chat(item["input"])
        with lock:
            done[0] += 1
            bar(done[0], n, "Nhóm B · chatflow perf", extra=f"conc={concurrency}")
        return r

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(one, items))
    wall = time.time() - t0

    ok = [r for r in results if not r["error"]]
    errs = [r for r in results if r["error"]]
    ttfts = [r["ttftMs"] for r in ok if r["ttftMs"]]
    totals = [r["totalMs"] for r in ok if r["totalMs"]]
    tps_list = []
    for r in ok:
        if r["tokens_out"] and r["ttftMs"] and r["totalMs"]:
            gen_s = (r["totalMs"] - r["ttftMs"]) / 1000.0
            if gen_s > 0.3:
                tps_list.append(r["tokens_out"] / gen_s)

    return {
        "R-PERF-TPS": pct(tps_list, 50) or 0.0,
        "R-PERF-TTFT": pct(ttfts, 50) or 0.0,
        "R-PERF-RPS": round(len(ok) / wall, 3) if wall else 0.0,
        # "chịu được" mức concurrency này = tỉ lệ lỗi THẬT (sau khi đã tự retry 429) < 5%.
        "R-PERF-CCU": concurrency if len(errs) / max(len(results), 1) < 0.05 else 0,
        "R-CTRL-RESP15": pct(totals, 50) or 0.0,
        "_meta_group_b": {"n": n, "ok": len(ok), "errors": len(errs), "wall_s": round(wall, 1)},
    }


# ── Nhóm A — chất lượng (giao lại pipeline gốc, chỉ hiện tiến trình theo bước) ────
GROUP_A_STEPS = [
    "cổng chất lượng bộ đề", "answer-gen", "SAST lượt 2", "SAST lượt 3",
    "retrieval (Hit@10 + P@1)", "llm_kpi (entity/relevance/hallu)",
    "task_kpi (SAST/exploit/remediation)", "perf_kpi", "sanitization",
    "faithfulness", "build_reports", "kiểm deliverable",
]


def bench_group_a(dataset_dir: str, workers: int = 4) -> dict:
    script = HERE / "chay_eval_gateway_moi.sh"
    if not script.exists():
        print(f"  ! Không thấy {script} — bỏ qua Nhóm A.")
        return {}
    print(f"\n  Nhóm A chạy pipeline gốc: {script.name} --dataset {dataset_dir} --workers {workers}")
    print(f"  (Ước tính 1-4+ giờ tuỳ tốc độ gateway — tiến trình hiển thị theo TỪNG BƯỚC, không phải %)\n")
    proc = subprocess.Popen(
        ["bash", str(script), "--dataset", dataset_dir, "--workers", str(workers)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        cwd=str(HERE))
    step_re = re.compile(r"\]\s*(\d+)([a-z]?)\.\s")
    run_dir = None
    run_dir_re = re.compile(r"run=(\S+)")
    seen_steps = 0
    for line in proc.stdout:
        line = line.rstrip("\n")
        m = run_dir_re.search(line)
        if m and run_dir is None:
            run_dir = m.group(1)
        m2 = step_re.search(line)
        if m2:
            seen_steps = max(seen_steps, min(int(m2.group(1)), len(GROUP_A_STEPS)))
            label = GROUP_A_STEPS[seen_steps - 1] if seen_steps >= 1 else "..."
            bar(seen_steps, len(GROUP_A_STEPS), "Nhóm A · pipeline chất lượng", extra=label)
    proc.wait()
    bar(len(GROUP_A_STEPS), len(GROUP_A_STEPS), "Nhóm A · pipeline chất lượng", extra="xong")

    if proc.returncode != 0:
        print(f"  ! chay_eval_gateway_moi.sh thoát mã {proc.returncode} — xem log để biết chi tiết.")
        return {}
    if not run_dir:
        print("  ! Không xác định được thư mục run — không đọc được kpi-summary.json.")
        return {}
    summary_path = KPI_DIR / run_dir / "raw-logs" / "kpi-summary.json"
    if not summary_path.exists():
        print(f"  ! Không thấy {summary_path}")
        return {}
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    return {code: v.get("value") for code, v in raw.items() if code in KPI_SPEC}


# ── chấm điểm + bảng tổng kết ──────────────────────────────────────────────────────
def evaluate(code: str, value: float, strict_tps_band: bool) -> bool | None:
    if value is None:
        return None
    s = KPI_SPEC[code]
    op = s["op"]
    if op == ">=":
        return value >= s["target"]
    if op == ">":
        return value > s["target"]
    if op == "<=":
        return value <= s["target"]
    if op == "<":
        return value < s["target"]
    if op == "range":
        return s["lo"] <= value <= s["hi"]
    if op == "range_floor":
        if strict_tps_band:
            return s["lo"] <= value <= s["hi"]
        return value >= s["lo"]  # vượt trần vẫn ĐẠT (quyết định 2026-08-28)
    if op == "ccu":
        return value >= s["target"]
    return None


def print_summary(results: dict, strict_tps_band: bool = False) -> None:
    print("\n" + "=" * 92)
    print("BẢNG TỔNG KẾT — SO VỚI NGƯỠNG HỢP ĐỒNG")
    print("=" * 92)
    header = f"{'Mã':<20} {'Tên':<30} {'Ngưỡng':<14} {'Đo được':<14} {'Kết quả'}"
    print(header)
    print("-" * 92)
    n_pass = n_total = 0
    for g in ("A", "B", "C"):
        codes = [c for c, s in KPI_SPEC.items() if s["group"] == g]
        if not any(c in results for c in codes):
            continue
        print(f"\n[{GROUP_NAMES[g]}]")
        for code in codes:
            s = KPI_SPEC[code]
            if code not in results:
                print(f"{code:<20} {s['name']:<30} {'—':<14} {'chưa đo':<14} —")
                continue
            value = results[code]
            ok = evaluate(code, value, strict_tps_band)
            n_total += 1
            n_pass += 1 if ok else 0
            thr = f"{s['lo']}–{s['hi']}" if s["op"] in ("range", "range_floor") else (
                f"~{s['target']}" if s["op"] == "ccu" else f"{s['op']} {s['target']}")
            val_s = f"{value:.1f}{s['unit']}" if isinstance(value, (int, float)) else str(value)
            verdict = "✓ ĐẠT" if ok else ("✗ TRƯỢT" if ok is False else "? lỗi đo")
            print(f"{code:<20} {s['name']:<30} {thr:<14} {val_s:<14} {verdict}")
    print("-" * 92)
    print(f"TỔNG: {n_pass}/{n_total} KPI đạt ngưỡng hợp đồng.")
    print("=" * 92)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", choices=["b", "c", "bc", "a", "all"], default="bc",
                    help="b=hiệu năng chatflow · c=throughput mô hình · a=chất lượng (chậm) · bc=B+C (mặc định) · all")
    ap.add_argument("--concurrency", type=int, default=30, help="Nhóm B: số luồng đồng thời (mặc định 30, theo hợp đồng)")
    ap.add_argument("--n", type=int, default=100, help="Nhóm B: số câu hỏi (mặc định 100)")
    ap.add_argument("--dataset", default=None, help="Nhóm A: thư mục testset (bắt buộc nếu chạy nhóm A)")
    ap.add_argument("--workers", type=int, default=4, help="Nhóm A: số luồng answer-gen")
    ap.add_argument("--tps-band-strict", action="store_true",
                    help="Chấm R-PERF-TPS nghiêm ngặt cả 2 đầu dải 20-35 (mặc định: vượt trần vẫn ĐẠT)")
    ap.add_argument("--out", default=None, help="Ghi kết quả JSON ra file này")
    args = ap.parse_args()

    groups = {"b": ["B"], "c": ["C"], "bc": ["B", "C"], "a": ["A"], "all": ["A", "B", "C"]}[args.group]

    if "A" in groups and not args.dataset:
        print("✗ Nhóm A cần --dataset <thư-mục-testset> (vd ./testset-v2)")
        return 2
    if "A" in groups:
        print("⚠ Nhóm A ước tính 1-4+ giờ (1000 câu qua agent thật). Nhấn Ctrl+C trong 5s để huỷ...")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nĐã huỷ.")
            return 1

    banner(groups)
    results: dict[str, float] = {}
    t_start = time.time()

    if "C" in groups:
        results.update(bench_embed_throughput())
        results.update(bench_rerank_throughput())
        results.update(bench_llm_tps())
    if "B" in groups:
        results.update(bench_group_b(concurrency=args.concurrency, n=args.n))
    if "A" in groups:
        results.update(bench_group_a(args.dataset, workers=args.workers))

    elapsed = time.time() - t_start
    print(f"\nHoàn tất đo trong {elapsed/60:.1f} phút.")
    print_summary(results, strict_tps_band=args.tps_band_strict)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in results.items() if not k.startswith("_meta")}
        out_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nĐã ghi kết quả: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
