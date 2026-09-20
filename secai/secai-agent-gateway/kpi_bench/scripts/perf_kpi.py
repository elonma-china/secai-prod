#!/usr/bin/env python3
"""perf_kpi.py — [VPS] KPI Hiệu năng suy luận (R-PERF-*, R-CTRL-RESP15).

Hai nguồn số liệu:
  A. Suy ra từ run đã có (results.jsonl): TTFT, total response (segment độ khó, %≤15s), TPS ước lượng.
  B. Đo trực tiếp (live probe): throughput embedding (/v1/embeddings) + reranker (/v1/rerank) req/s;
     concurrency RPS/CCU bằng N request đồng thời tới backend.

Ngưỡng hợp đồng: TTFT 900-1500ms · TPS 20-35 tok/s · response ≤15s · embed ~1200/s · rerank ~180/s ·
~0.6 RPS / 30 CCU (trên 3×A100 — ở đây đo stack deploy thực: deepseek cloud + bge endpoint).

Usage (VPS): python3 perf_kpi.py --results /opt/secai/acceptance/results/run2/results.jsonl \
                 --out ../raw-logs [--concurrency 10] [--throughput-n 200]

CẬP NHẬT 2026-09-20 (audit /tmp/kpi-method-audit.md — ĐO THẬT dù số tụt, BỔ SUNG field mới
CẠNH field cũ, KHÔNG đổi/xoá số cũ, KHÔNG đổi hành vi mặc định):
  - tokens_out cũ = len(text)/4.0 (chars/4) GIỮ NGUYÊN ở `tps_est`/`performance-per-request.jsonl`.
    BỔ SUNG: nếu dòng results.jsonl có sẵn usage.completion_tokens/completion_tokens/tokens_out
    (hiện bench_answers_gateway.py CHƯA ghi field này — xem TODO ở `_tokens_out_for_row`) thì
    dùng làm `tps_est_real_token` (field RIÊNG) + `token_source` cho biết nguồn.
  - TTFT/response time vẫn là client wall-clock (t0=time.time() trong bench_answers_gateway.py)
    — thêm nhãn `basis` để không hiểu nhầm là server timestamp "(completed-created)" như catalog cũ.
  - Throughput embed/rerank: giữ nguyên số đo WAN cũ; `--onhost` chỉ đổi NHÃN measure_location.
"""
import argparse
import base64
import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KPI_DIR))
import config  # noqa: E402


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = int(round((p / 100) * (len(vals) - 1)))
    return vals[k]


def _post(url, body, timeout=60, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def auth_headers():
    pw = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not pw:
        return {}
    user = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


# ---------------- A. Derived from run ----------------
def _tokens_out_for_row(r):
    """Trả (tokens, source). Ưu tiên token THẬT nếu dòng results.jsonl có sẵn
    usage.completion_tokens / completion_tokens / tokens_out — BỔ SUNG, không thay hành vi cũ.
    # TODO: bench_answers_gateway.py (KHÔNG thuộc phạm vi sửa ở đây) hiện CHƯA ghi field này
    # từ SSE 'final' của gateway (grep xác nhận không có usage/completion_tokens trong
    # kpi_bench/02-GEN-TEST/adapters/bench_answers_gateway.py) — nên trong thực tế `source`
    # dưới đây gần như luôn là chars/4 cho tới khi field đó được thêm vào results.jsonl.
    """
    usage = r.get("usage") or {}
    real = usage.get("completion_tokens") or r.get("completion_tokens") or r.get("tokens_out")
    if isinstance(real, (int, float)) and real > 0:
        return float(real), "usage.completion_tokens"
    return len((r.get("text") or "")) / 4.0, "chars/4 (approx — TODO real tokens)"


def write_per_request(results_path, out_dir):
    """Kết xuất raw-log hiệu năng per-request (deliverable §7.2): mỗi câu 1 dòng timing."""
    rows = [json.loads(l) for l in Path(results_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    n = 0
    with (Path(out_dir) / "performance-per-request.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            total = r.get("totalMs")
            if not total:
                continue
            ttft = r.get("ttftMs")
            toks = len((r.get("text") or "")) / 4.0  # CŨ — giữ nguyên (chars/4)
            toks_real, token_source = _tokens_out_for_row(r)  # MỚI — real nếu có, else chars/4 lại
            gen = (total - (ttft or 0)) / 1000.0
            f.write(json.dumps({
                "id": r.get("id"), "difficulty": r.get("difficulty"), "capability": r.get("capability"),
                "ttft_ms": ttft, "total_ms": total, "le_15s": total <= 15000,
                "ttft_basis": "client wall-clock to first token (incl network RTT)",
                "tps_est": round(toks / gen, 1) if (gen > 0.5 and toks > 20) else None,  # CŨ
                "tps_est_real_token": (round(toks_real / gen, 1)
                                       if (gen > 0.5 and toks_real > 20 and token_source == "usage.completion_tokens")
                                       else None),  # MỚI — None nếu không có usage
                "token_source": token_source,
            }, ensure_ascii=False) + "\n")
            n += 1
    return n


def derived_metrics(results_path):
    rows = [json.loads(l) for l in Path(results_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("totalMs")]
    ttft = [r["ttftMs"] for r in rows if r.get("ttftMs")]
    total = [r["totalMs"] for r in rows]
    # TPS ước lượng: tokens_out ≈ len(answer)/4 ; gen_time = total - ttft  (CŨ — giữ nguyên)
    tps = []
    tps_real = []  # MỚI — chỉ có giá trị khi row mang usage.completion_tokens thật
    n_with_usage = 0
    for r in rows:
        toks = len((r.get("text") or "")) / 4.0
        gen = (r.get("totalMs", 0) - (r.get("ttftMs") or 0)) / 1000.0
        if gen > 0.5 and toks > 20:
            tps.append(toks / gen)
        toks_real, token_source = _tokens_out_for_row(r)
        if gen > 0.5 and toks_real > 20 and token_source == "usage.completion_tokens":
            n_with_usage += 1
            tps_real.append(toks_real / gen)
    by_diff = {}
    for d in ["de", "trung_binh", "kho", "tu_duy"]:
        sub = [r["totalMs"] for r in rows if r.get("difficulty") == d]
        if sub:
            by_diff[d] = {"p50_ms": pct(sub, 50), "p95_ms": pct(sub, 95),
                          "pct_le_15s": round(100 * sum(1 for x in sub if x <= 15000) / len(sub), 1), "n": len(sub)}
    return {
        "n": len(rows),
        "ttft_ms": {"p50": pct(ttft, 50), "p95": pct(ttft, 95), "mean": round(statistics.mean(ttft)) if ttft else None,
                    "in_900_1500_pct": round(100 * sum(1 for x in ttft if 900 <= x <= 1500) / len(ttft), 1) if ttft else None,
                    "basis": "client wall-clock (t0 in bench_answers_gateway.py) to first SSE token event — "
                             "includes network RTT, NOT server timestamp (catalog's min(part.start)-info.time.created "
                             "references a defunct opencode-era schema). TODO: reconcile catalog formula with SSE reality."},
        "response_ms": {"p50": pct(total, 50), "p95": pct(total, 95), "mean": round(statistics.mean(total)),
                        "pct_le_15s": round(100 * sum(1 for x in total if x <= 15000) / len(total), 1),
                        "basis": "client wall-clock total elapsed (t0..stream complete), not server (completed-created)"},
        "tps_est": {"p50": round(pct(tps, 50), 1) if tps else None, "mean": round(statistics.mean(tps), 1) if tps else None,
                    "in_20_35_pct": round(100 * sum(1 for x in tps if 20 <= x <= 35) / len(tps), 1) if tps else None, "n": len(tps),
                    "token_source": "chars/4 (approx — TODO real tokens)"},
        "tps_est_real_token": {"p50": round(pct(tps_real, 50), 1) if tps_real else None,
                                "mean": round(statistics.mean(tps_real), 1) if tps_real else None,
                                "n": len(tps_real), "n_total": len(rows),
                                "note": "MỚI — chỉ non-null khi results.jsonl có usage.completion_tokens/"
                                        "completion_tokens/tokens_out per-row (hiện bench_answers_gateway.py "
                                        "chưa ghi field này, xem TODO ở _tokens_out_for_row)"},
        "by_difficulty": by_diff,
    }


# ---------------- B. Live throughput ----------------
def throughput_embed(n=200, batch=32, conc=4, onhost=False):
    """Throughput batched + concurrent (đại diện hơn cho 'embeddings/s' của BGE).
    # TODO: đo network-free bằng cách chạy script này TRÊN máy serving (EMBED_URL=127.0.0.1:<port>)
    # — WAN đọc thấp hơn thật ~5x (xem kpi-method-audit.md). `onhost=True` chỉ đổi NHÃN
    # measure_location, không tự SSH/đổi EMBED_URL."""
    texts = [f"vulnerability test query about CVE and CWE number {i}" for i in range(batch)]
    nbatches = max(1, n // batch)

    headers = {"Authorization": f"Bearer {config.EMBED_KEY}"} if config.EMBED_KEY else None

    def one(_):
        try:
            _post(f"{config.EMBED_URL}/v1/embeddings", {"model": config.EMBED_MODEL, "input": texts}, timeout=60, headers=headers)
            return batch
        except Exception:
            return 0
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for got in ex.map(one, range(nbatches)):
            done += got
    dt = time.time() - t0
    return {"embeddings": done, "batch": batch, "concurrency": conc, "seconds": round(dt, 2),
            "embeddings_per_s": round(done / dt, 1) if dt else None,
            "measure_location": "on-host" if onhost else "WAN (client)", "endpoint": config.EMBED_URL}


def throughput_rerank(n=100, batch=20, conc=4, onhost=False):
    """Throughput rerank: mỗi request rerank `batch` doc; đo doc/s (so sánh ~180 rerank/s).
    # TODO: như throughput_embed — đo on-host để loại RTT mạng."""
    docs = [f"vulnerability doc {j}: SQL injection buffer overflow XSS RCE in product" for j in range(batch)]
    nreq = max(1, n // batch)

    headers = {"Authorization": f"Bearer {config.RERANK_KEY}"} if config.RERANK_KEY else None

    def one(i):
        try:
            _post(f"{config.RERANK_URL}/v1/rerank", {"model": config.RERANK_MODEL, "query": f"injection vulnerability {i}", "documents": docs}, timeout=60, headers=headers)
            return batch
        except Exception:
            return 0
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        for got in ex.map(one, range(nreq)):
            done += got
    dt = time.time() - t0
    return {"docs_reranked": done, "batch": batch, "concurrency": conc, "seconds": round(dt, 2),
            "docs_per_s": round(done / dt, 1) if dt else None,
            "measure_location": "on-host" if onhost else "WAN (client)", "endpoint": config.RERANK_URL}


# ---------------- B. Concurrency RPS/CCU (backend) ----------------
def concurrency_probe(base, concurrency, total_req):
    """Gửi total_req message đồng thời (concurrency luồng) tới backend, đo RPS + lỗi."""
    headers = auth_headers()

    def one(i):
        try:
            sid = json.loads(_post(f"{base}/session", {"agent": "build", "model": {"providerID": config.LLM_MODEL.split('-')[0] if '/' not in config.LLM_MODEL else config.LLM_MODEL.split('/')[0], "id": config.LLM_MODEL}}, timeout=30, headers=headers))["id"]
            t0 = time.time()
            _post(f"{base}/session/{sid}/message",
                  {"messageID": f"perf_{i}_{int(time.time()*1000)}", "agent": "build",
                   "model": {"providerID": "deepseek", "modelID": config.LLM_MODEL},
                   "parts": [{"type": "text", "text": "CVE-2021-44228 là gì? Trả lời ngắn gọn."}]}, timeout=180, headers=headers)
            return time.time() - t0, True
        except Exception:
            return None, False

    t0 = time.time()
    lat = []
    ok = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for dt, success in ex.map(one, range(total_req)):
            if success:
                ok += 1
                lat.append(dt)
    wall = time.time() - t0
    return {"concurrency": concurrency, "total": total_req, "ok": ok, "errors": total_req - ok,
            "wall_s": round(wall, 1), "rps": round(ok / wall, 3) if wall else None,
            "latency_p50_s": round(pct(lat, 50), 1) if lat else None, "latency_p95_s": round(pct(lat, 95), 1) if lat else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="/opt/secai/acceptance/results/run2/results.jsonl")
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs"))
    ap.add_argument("--throughput-n", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=0, help="0 = bỏ qua concurrency probe (tránh wedge backend)")
    ap.add_argument("--concurrency-total", type=int, default=20)
    ap.add_argument("--onhost", action="store_true",
                    help="Chỉ đổi NHÃN measure_location=on-host cho throughput embed/rerank "
                         "(không tự SSH/đổi EMBED_URL/RERANK_URL — người chạy phải tự trỏ về "
                         "localhost khi ở trên máy serving)")
    args = ap.parse_args()

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"kpi": "performance", "model": config.LLM_MODEL,
               "note": "Đo trên stack deploy thực (deepseek cloud + bge endpoint), KHÔNG phải 3×A100/Qwen on-prem"}

    if Path(args.results).exists():
        print("A. Suy ra từ run...", flush=True)
        summary["derived"] = derived_metrics(args.results)
        npr = write_per_request(args.results, out_dir)
        print(f"   per-request log: {npr} dòng → {out_dir/'performance-per-request.jsonl'}", flush=True)
    print("B. Throughput embedding...", flush=True)
    summary["throughput_embedding"] = throughput_embed(args.throughput_n, onhost=args.onhost)
    print("B. Throughput reranker...", flush=True)
    summary["throughput_reranker"] = throughput_rerank(max(50, args.throughput_n // 2), onhost=args.onhost)
    if args.concurrency > 0:
        print(f"B. Concurrency probe ({args.concurrency} CCU)...", flush=True)
        summary["concurrency"] = concurrency_probe(os.environ.get("ACC_BASE", "http://localhost:4096"),
                                                   args.concurrency, args.concurrency_total)

    (out_dir / "performance-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== Performance ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
