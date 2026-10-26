#!/usr/bin/env python3
"""Đo CCU (người dùng đồng thời) cho dịch TỆP/ẢNH — API dịch v2.

Mỗi người dùng ảo (VU) lặp: tải 1 tệp -> poll tới xong -> nghỉ think time ngẫu nhiên 5-20 s -> tải tệp tiếp.
VU khởi động rải đều trong --ramp giây; chạy --duration giây (gồm ramp); hết giờ không bắt đầu chu trình mới, chờ
các job dở dang tối đa --drain giây (còn dở thì tính là timeout = thất bại).

  ccu_translate.py --base http://127.0.0.1:18285 --users {20|25|30} --duration 240 [--ramp 30] [--drain 100] [--runs 3] [--cooldown 40] [--json out.json] [--html out.html]
  ccu_translate.py --base http://127.0.0.1:18285 --levels 20,25,30 --duration 240 --runs 3 --json out.json --html out.html   # đo nhiều mức, 1 báo cáo
Mỗi mức VU được đo --runs lần (mặc định 3, mỗi lần seed khác, nghỉ --cooldown giây cho hàng đợi cạn) rồi LẤY TRUNG BÌNH;
--users chỉ nhận 20, 25 hoặc 30 (hoặc dùng --levels để đo nhiều mức trong 1 lần chạy). Kết luận PASS/FAIL dựa trên số
liệu trung bình (p95 trung bình của 3 lần) + điều kiện không lỗi hạ tầng/không bị từ chối ở BẤT KỲ lần nào.
Luôn in kết quả ra terminal khi đo xong (từng lần + trung bình); --html xuất thêm báo cáo HTML tự chứa (không cần
mạng) có bảng số liệu + biểu đồ p95/thông lượng theo mức VU, giống báo cáo CCU của chatbot.
  flock /tmp/translate-gpu-test.lock python3 ccu_translate.py ...      # GPU/Vast dùng chung

Tỉ lệ dùng: ~70% tệp chữ (docx/xlsx/pdf), ~30% cần OCR (pdf-scan/png/jpg); ~20% tệp dài.
Tiêu chí đạt (mặc định): 5xx/đứt kết nối/poll_error/timeout = 0; job nhận (202) thành công >= 99%; không bị từ chối 429;
p95 tổng tệp ngắn <= 30 s, p95 tệp dài <= 120 s. Mã thoát 0 = ĐẠT.
Mọi VU cùng peer 127.0.0.1: chỉ chạy trên stack thử đã nới MAX_ACTIVE_PER_IP/QUEUED/RATE (stack_exp.sh). KHÔNG chạy vào production.
Không in token; chỉ dùng dữ liệu synthetic trong fixtures/formats."""
import argparse, json, mimetypes, random, sys, threading, time, urllib.error, urllib.request, uuid
from pathlib import Path

FX = Path(__file__).resolve().parent.parent / "formats"
LANGS = ["en"] * 4 + ["de", "es", "fr", "ru", "ja", "zh-Hans"]
# (thư mục, route, loại, số trang xấp xỉ short/long)
TEXT = [("docx", "files"), ("xlsx", "files"), ("pdf", "files")]
OCR = [("pdf-scan", "files"), ("png", "images"), ("jpg", "images")]
PAGES = {"short": 1, "long": 4}


def call(method, url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": {"code": type(e).__name__}}


def multipart(fields, fname, data):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    ct = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: {ct}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def pick(rng):
    ocr = rng.random() < 0.30
    long_ = rng.random() < 0.20
    for _ in range(20):
        fmt, route = rng.choice(OCR if ocr else TEXT)
        lang = rng.choice(LANGS)
        p = FX / fmt / f"{'long' if long_ else 'short'}-{lang}.{ 'pdf' if fmt == 'pdf-scan' else fmt}"
        if p.exists():
            return p, route, lang, ("long" if long_ else "short"), ("ocr" if ocr else "text"), fmt
    raise RuntimeError("không chọn được tệp mẫu")


def cycle(base, rng, poll, deadline_abs, results, lock, inflight):
    path, route, lang, size, kind, fmt = pick(rng)
    rec = {"file": f"{fmt}/{path.name}", "size": size, "kind": kind, "post": None, "status": None, "error": None, "t_start": time.time()}
    body, ct = multipart({"target_lang": "vi", "source_lang": lang}, path.name, path.read_bytes())
    t0 = time.monotonic()
    s, j = call("POST", f"{base}/ai/v2/translate/{route}", body, {"Content-Type": ct})
    rec["post"] = s
    if s != 202:
        rec["status"] = "rejected"; rec["error"] = (j.get("error") or {}).get("code") or f"HTTP{s}"
        rec["total_s"] = round(time.monotonic() - t0, 2)
        with lock: results.append(rec)
        return
    jid = j["job_id"]; first_run = None; delay = poll
    with lock: inflight[0] += 1; inflight[1] = max(inflight[1], inflight[0])
    try:
        while True:
            time.sleep(delay)
            now = time.monotonic()
            s, jj = call("GET", f"{base}/ai/v2/translate/jobs/{jid}")
            if s == 429:
                delay = min(delay * 1.5, 5); rec["poll_429"] = rec.get("poll_429", 0) + 1; continue
            if s != 200:
                rec["status"] = "poll_error"; rec["error"] = f"HTTP{s}"; break
            delay = poll
            if first_run is None and jj.get("stage") not in ("queued", None):
                first_run = now - t0
            if jj["status"] in ("succeeded", "failed", "cancelled"):
                rec["status"] = jj["status"]; rec["error"] = (jj.get("error") or {}).get("code"); break
            if time.time() > deadline_abs:
                rec["status"] = "timeout"; break
    finally:
        with lock: inflight[0] -= 1
    rec["total_s"] = round(time.monotonic() - t0, 2)
    rec["queue_s"] = round(first_run, 2) if first_run is not None else None
    with lock: results.append(rec)


def vu(idx, base, start_at, stop_at, abs_deadline, poll, results, lock, inflight, seed):
    rng = random.Random(seed * 1000 + idx)
    time.sleep(max(0, start_at - time.time()))
    while time.time() < stop_at:
        cycle(base, rng, poll, abs_deadline, results, lock, inflight)
        if time.time() >= stop_at:
            break
        time.sleep(rng.uniform(5, 20))  # think time


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None


def run_once(a, users, seed):
    results, lock, inflight = [], threading.Lock(), [0, 0]
    t0 = time.time(); stop_at = t0 + a.duration; abs_deadline = stop_at + a.drain
    th = [threading.Thread(target=vu, args=(i, a.base, t0 + a.ramp * i / max(1, users), stop_at, abs_deadline, a.poll, results, lock, inflight, seed), daemon=True) for i in range(users)]
    for t in th: t.start()
    for t in th: t.join()
    wall = time.time() - t0
    acc = [r for r in results if r["post"] == 202]; rej = [r for r in results if r["post"] != 202]
    ok = [r for r in acc if r["status"] == "succeeded"]
    bad = [r for r in acc if r["status"] != "succeeded"]
    infra = [r for r in results if (r["post"] or 0) >= 500 or r["post"] == 0 or r["status"] in ("poll_error", "timeout")]
    err = {k: sum(1 for r in bad if (r["error"] or r["status"]) == k) for k in sorted({(r["error"] or r["status"]) for r in bad})}
    rejc = {k: sum(1 for r in rej if r["error"] == k) for k in sorted({r["error"] for r in rej})}
    stats = {}
    for name in ("short", "long"):
        sel = [r for r in ok if r["size"] == name]
        tot = [r["total_s"] for r in sel]; q = [r["queue_s"] for r in sel if r["queue_s"] is not None]
        stats[name] = {"n": len(sel), "p50": pct(tot, 50), "p95": pct(tot, 95), "max": max(tot) if tot else None, "queue_p50": pct(q, 50), "queue_p95": pct(q, 95), "queue_max": max(q) if q else None}
    for name in ("text", "ocr"):
        tot = [r["total_s"] for r in ok if r["kind"] == name]
        stats[name] = {"n": len(tot), "p50": pct(tot, 50), "p95": pct(tot, 95)}
    pages = sum(PAGES[r["size"]] for r in ok)
    thr = {"docs_per_min": round(len(ok) / wall * 60, 1), "pages_per_min": round(pages / wall * 60, 1)}
    print(f"  seed={seed} VU={users} chạy {wall:.0f}s  chu trình {len(results)}  nhận {len(acc)}  từ chối {len(rej)} {rejc}")
    print(f"  succeeded {len(ok)}  không thành công {len(bad)} {err}  hạ tầng {len(infra)}  peak dở dang {inflight[1]}")
    for k, v in stats.items(): print(f"    {k}: {v}")
    print(f"    thông lượng {thr}")
    return {"seed": seed, "wall_s": round(wall, 1), "peak_inflight": inflight[1], "accepted": len(acc), "succeeded": len(ok), "rejected": len(rej),
            "stats": stats, "throughput": thr, "errors": err, "rejected_codes": rejc, "infra": len(infra), "rows": results}


def mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 2) if v else None


def measure_level(a, users):
    """Đo 1 mức VU --runs lần, trả về (runs, avg, thông lượng, problems) — in tiến độ ra terminal ngay khi chạy."""
    runs = []
    for i in range(a.runs):
        print(f"{a.base} VU={users} lần đo {i + 1}/{a.runs}")
        runs.append(run_once(a, users, a.seed + i))
        if i < a.runs - 1: time.sleep(a.cooldown)
    avg = {name: {k: mean([r["stats"][name][k] for r in runs]) for k in ("p50", "p95", "max", "queue_p50", "queue_p95")} for name in ("short", "long")}
    avg["text"] = {k: mean([r["stats"]["text"][k] for r in runs]) for k in ("p50", "p95")}
    avg["ocr"] = {k: mean([r["stats"]["ocr"][k] for r in runs]) for k in ("p50", "p95")}
    thr = {k: mean([r["throughput"][k] for r in runs]) for k in ("docs_per_min", "pages_per_min")}
    acc = sum(r["accepted"] for r in runs); ok = sum(r["succeeded"] for r in runs)
    print(f"\n=== TRUNG BÌNH {a.runs} lần, VU={users} ===")
    for k, v in avg.items(): print(f"  {k}: {v}")
    print(f"  thông lượng {thr}   succeeded {ok}/{acc}")
    print("  p95 ngắn từng lần: " + ", ".join(str(r["stats"]["short"]["p95"]) for r in runs) + "   p95 dài từng lần: " + ", ".join(str(r["stats"]["long"]["p95"]) for r in runs))
    problems = []
    if any(r["infra"] for r in runs): problems.append("có 5xx/timeout/poll_error ở ít nhất 1 lần đo")
    if any(r["rejected"] for r in runs): problems.append(f"bị từ chối {sum(r['rejected'] for r in runs)}")
    if acc and ok / acc < a.min_success: problems.append(f"succeeded {ok}/{acc} < {a.min_success}")
    if avg["short"]["p95"] and avg["short"]["p95"] > a.p95_short: problems.append(f"p95 ngắn TB {avg['short']['p95']} > {a.p95_short}")
    if avg["long"]["p95"] and avg["long"]["p95"] > a.p95_long: problems.append(f"p95 dài TB {avg['long']['p95']} > {a.p95_long}")
    verdict = "PASS" if not problems else "FAIL: " + "; ".join(problems)
    print(verdict)
    return {"users": users, "runs": runs, "average": avg, "throughput_avg": thr, "succeeded": ok, "accepted": acc, "verdict": verdict, "problems": problems}


def render_html(a, levels, path):
    """Báo cáo HTML tự chứa (không cần mạng): bảng số liệu + biểu đồ p95/thông lượng theo mức VU (ảnh base64)."""
    import base64, io
    def row(lv):
        css = "ok" if not lv["problems"] else "bad"
        return (f"<tr><td>{lv['users']}</td><td class='{css}'>{lv['verdict'].split(':')[0]}</td>"
                f"<td>{lv['average']['short']['p95']}</td><td>{lv['average']['long']['p95']}</td>"
                f"<td>{lv['throughput_avg']['docs_per_min']}</td><td>{lv['succeeded']}/{lv['accepted']}</td></tr>")
    rows = "".join(row(lv) for lv in levels)
    chart_b64 = ""
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ink, sec, grid, c1, c2 = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#d6732a"
        vus = [lv["users"] for lv in levels]
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.2), facecolor="#fcfcfb")
        for x in ax:
            x.set_facecolor("#fcfcfb"); x.grid(axis="y", color=grid, lw=.8); x.set_axisbelow(True)
            for s in ("top", "right"): x.spines[s].set_visible(False)
            x.tick_params(colors=sec); x.set_xticks(vus); x.set_xlabel("Số người dùng ảo (VU)", color=sec)
        p = ax[0]
        p.plot(vus, [lv["average"]["short"]["p95"] for lv in levels], "-o", color=c1, lw=2, ms=7, label="p95 ngắn")
        p.plot(vus, [lv["average"]["long"]["p95"] for lv in levels], "-o", color=c2, lw=2, ms=7, label="p95 dài")
        p.axhline(a.p95_short, color=sec, ls="--", lw=1)
        p.set_title("p95 thời gian (s) — thấp hơn là tốt", loc="left", color=ink, fontsize=11); p.legend(frameon=False)
        q = ax[1]
        q.plot(vus, [lv["throughput_avg"]["docs_per_min"] for lv in levels], "-o", color=c1, lw=2, ms=7)
        q.set_title("Thông lượng (tài liệu/phút, TB) — cao hơn là tốt", loc="left", color=ink, fontsize=11)
        fig.suptitle(f"CCU dịch tệp/ảnh — {a.base}", x=.01, ha="left", color=ink, fontsize=12)
        fig.tight_layout()
        buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=150); plt.close(fig)
        chart_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"(không vẽ được biểu đồ: {e})")
    html = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<title>Báo cáo CCU — dịch tệp/ảnh</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial;background:#fcfcfb;color:#0b0b0b;margin:2rem}}
table{{border-collapse:collapse;margin:1rem 0}} td,th{{border:1px solid #e4e3df;padding:.4rem .8rem;text-align:center}}
th{{background:#f1f0ec}} .ok{{color:#1a7f37;font-weight:600}} .bad{{color:#c23a2b;font-weight:600}}
img{{max-width:100%}} h1{{font-size:1.3rem}}
</style></head><body>
<h1>Báo cáo CCU (người dùng đồng thời) — API dịch tệp/ảnh</h1>
<p>base: {a.base} — duration/VU {a.duration}s, ramp {a.ramp}s, mỗi mức đo {a.runs} lần (TB)</p>
<table><tr><th>VU</th><th>Kết luận</th><th>p95 ngắn TB (s)</th><th>p95 dài TB (s)</th><th>tài liệu/phút TB</th><th>succeeded/accepted</th></tr>
{rows}</table>
{f'<img src="data:image/png;base64,{chart_b64}" alt="biểu đồ CCU">' if chart_b64 else '<p>(không có biểu đồ)</p>'}
</body></html>"""
    Path(path).write_text(html, encoding="utf-8")
    print(f"đã ghi báo cáo HTML: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--users", type=int, choices=[20, 25, 30], help="1 mức; bỏ qua nếu dùng --levels")
    ap.add_argument("--levels", help="nhiều mức, phân tách dấu phẩy, mỗi giá trị phải là 20/25/30, vd 20,25,30")
    ap.add_argument("--runs", type=int, default=3, help="số lần đo mỗi mức VU, kết quả lấy trung bình (mặc định 3)")
    ap.add_argument("--cooldown", type=float, default=40, help="giây nghỉ giữa các lần đo để hàng đợi cạn")
    ap.add_argument("--duration", type=float, default=240); ap.add_argument("--ramp", type=float, default=30)
    ap.add_argument("--drain", type=float, default=100); ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20261011); ap.add_argument("--json"); ap.add_argument("--html")
    ap.add_argument("--min-success", type=float, default=0.99)
    ap.add_argument("--p95-short", type=float, default=30); ap.add_argument("--p95-long", type=float, default=120)
    a = ap.parse_args()
    if ":8131" in a.base or ":18185" in a.base:
        sys.exit("từ chối: không đo CCU vào production")
    if a.levels:
        users_list = [int(x) for x in a.levels.split(",")]
        for u in users_list:
            if u not in (20, 25, 30): sys.exit(f"--levels chỉ nhận 20/25/30, gặp {u}")
    elif a.users:
        users_list = [a.users]
    else:
        sys.exit("cần --users hoặc --levels")
    levels = [measure_level(a, u) for u in users_list]
    print("\n=== TỔNG HỢP CÁC MỨC ===")
    for lv in levels:
        print(f"  VU={lv['users']:>3}  {lv['verdict']}")
    if a.json:
        Path(a.json).write_text(json.dumps({"base": a.base, "duration": a.duration, "runs": a.runs, "levels": levels}, ensure_ascii=False, indent=1))
    if a.html:
        render_html(a, levels, a.html)
    fail = any(lv["problems"] for lv in levels)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
