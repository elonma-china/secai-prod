#!/usr/bin/env python3
"""Bài test TẢI: gửi ĐỒNG THỜI N tệp/ảnh vào API dịch v2 và xem hệ thống xử lý ra sao (03 mục 1e).

  loadtest.py --base http://127.0.0.1:18285 --concurrency 20 [--rounds 1] [--poll 1.0] [--timeout 900] [--json out.json]
  loadtest.py --base http://127.0.0.1:8131  --concurrency 8   # production: giới hạn tần suất mặc định sẽ cho 429 (hợp lệ)

Mỗi vòng: N luồng chờ nhau (barrier) rồi POST cùng lúc (tệp lấy lần lượt từ bộ mẫu trộn docx/xlsx/pdf/pdf-scan/png/jpg,
ngắn+dài), sau đó mỗi job được poll (tự lùi khi 429) tới khi xong. Báo:
  - nhận/từ chối: 202 | 429 (RATE_LIMITED/QUEUE_FULL...) | 5xx (LỖI — không bao giờ được có)
  - hoàn tất/thất bại của job đã nhận (kèm mã lỗi), thời gian xếp hàng (queued) và tổng, p50/p95/max
  - thông lượng (tài liệu/phút, trang/phút), tỉ lệ nhận
PASS (mã thoát 0) khi: không có 5xx/timeout/poll_error; mọi job đã nhận đều có kết cục (succeeded hoặc failed có mã lý do);
tỉ lệ job đã nhận mà succeeded >= --min-success (mặc định 0.95); p95 tổng <= --p95-max giây nếu đặt.
Không in token. Chỉ dùng dữ liệu synthetic trong fixtures."""
import argparse, json, mimetypes, statistics, sys, threading, time, urllib.error, urllib.request, uuid
from pathlib import Path

FX = Path(__file__).resolve().parent.parent
MIX = [  # (đường dẫn trong fixtures, route, ngôn ngữ nguồn, số trang)
    ("formats/docx/short-en.docx", "files", "en", 1), ("formats/docx/long-en.docx", "files", "en", 5),
    ("formats/xlsx/long-en.xlsx", "files", "en", 3), ("formats/pdf/long-en.pdf", "files", "en", 4),
    ("formats/pdf-scan/short-en.pdf", "files", "en", 1), ("formats/pdf-scan/long-en.pdf", "files", "en", 3),
    ("formats/png/short-en.png", "images", "en", 1), ("formats/jpg/long-en.jpg", "images", "en", 1),
    ("formats/docx/short-de.docx", "files", "de", 1), ("formats/pdf/short-es.pdf", "files", "es", 1),
    ("formats/png/long-ru.png", "images", "ru", 1), ("formats/xlsx/short-en.xlsx", "files", "en", 1),
]


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
    except Exception as e:  # kết nối đứt/timeout
        return 0, {"error": {"code": type(e).__name__}}


def multipart(fields, fname, data):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    ct = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: {ct}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def one(base, spec, idx, barrier, poll, timeout, out):
    rel, route, src, pages = spec
    path = FX / rel
    rec = {"i": idx, "file": rel, "pages": pages, "post": None, "accepted": False, "status": None, "error": None}
    body, ct = multipart({"target_lang": "vi", "source_lang": src}, path.name, path.read_bytes())
    barrier.wait()
    t0 = time.monotonic()
    s, j = call("POST", f"{base}/ai/v2/translate/{route}", body, {"Content-Type": ct})
    rec["post"] = s; rec["post_s"] = round(time.monotonic() - t0, 2)
    if s != 202:
        rec["status"] = "rejected"; rec["error"] = (j.get("error") or {}).get("code") or f"HTTP{s}"
        out[idx] = rec; return
    rec["accepted"] = True; jid = j["job_id"]; first_run = None; delay = poll; polls_429 = 0
    while True:
        time.sleep(delay)
        now = time.monotonic()
        s, jj = call("GET", f"{base}/ai/v2/translate/jobs/{jid}")
        if s == 429:
            polls_429 += 1; delay = min(delay * 1.5, 10); continue
        if s != 200:
            rec["status"] = "poll_error"; rec["error"] = f"HTTP{s}"; break
        delay = poll
        if first_run is None and jj.get("stage") not in ("queued", None):
            first_run = now - t0
        if jj["status"] in ("succeeded", "failed", "cancelled"):
            rec["status"] = jj["status"]; rec["error"] = (jj.get("error") or {}).get("code"); break
        if now - t0 > timeout:
            rec["status"] = "timeout"; break
    rec["total_s"] = round(time.monotonic() - t0, 2); rec["queue_s"] = round(first_run, 2) if first_run is not None else None
    rec["poll_429"] = polls_429
    out[idx] = rec


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--concurrency", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=1); ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=900); ap.add_argument("--json")
    ap.add_argument("--kinds", choices=["all", "text", "ocr"], default="all", help="text = docx/xlsx/pdf chữ; ocr = PDF scan + ảnh")
    ap.add_argument("--min-success", type=float, default=0.95); ap.add_argument("--p95-max", type=float)
    a = ap.parse_args()
    mix = MIX if a.kinds == "all" else [m for m in MIX if (("scan" in m[0] or m[1] == "images") == (a.kinds == "ocr"))]
    rows = []; t_start = time.monotonic()
    for rnd in range(a.rounds):
        out = {}; barrier = threading.Barrier(a.concurrency); th = []
        for i in range(a.concurrency):
            spec = mix[(rnd * a.concurrency + i) % len(mix)]
            t = threading.Thread(target=one, args=(a.base, spec, rnd * a.concurrency + i, barrier, a.poll, a.timeout, out)); t.start(); th.append(t)
        for t in th: t.join()
        rows += [out[k] for k in sorted(out)]
    wall = time.monotonic() - t_start
    acc = [r for r in rows if r["accepted"]]; rej = [r for r in rows if not r["accepted"]]
    ok = [r for r in acc if r["status"] == "succeeded"]; bad = [r for r in acc if r["status"] != "succeeded"]
    fivexx = [r for r in rows if (r["post"] or 0) >= 500 or r["post"] == 0 or r["status"] in ("poll_error", "timeout")]
    tot = [r["total_s"] for r in ok]; que = [r["queue_s"] for r in ok if r["queue_s"] is not None]
    print(f"{a.base}  đồng thời={a.concurrency} x {a.rounds} vòng  tổng {len(rows)} tệp  thời gian chạy {wall:.1f}s")
    print(f"  nhận 202: {len(acc)}   từ chối: {len(rej)} " + str({k: sum(1 for r in rej if r['error'] == k) for k in sorted({r['error'] for r in rej})}))
    print(f"  job đã nhận: succeeded {len(ok)}, không thành công {len(bad)} " + str({k: sum(1 for r in bad if (r['error'] or r['status']) == k) for k in sorted({(r['error'] or r['status']) for r in bad})}))
    if tot:
        print(f"  thời gian tổng (s): p50 {pct(tot,50)}  p95 {pct(tot,95)}  max {max(tot)}   xếp hàng p50 {pct(que,50)} p95 {pct(que,95)} max {max(que) if que else None}")
        pages = sum(r["pages"] for r in ok)
        print(f"  thông lượng: {len(ok)/wall*60:.1f} tài liệu/phút, {pages/wall*60:.0f} trang/phút")
    print(f"  LỖI HẠ TẦNG (5xx/đứt kết nối/poll_error/timeout): {len(fivexx)}")
    problems = []
    if fivexx: problems.append("có 5xx/timeout/poll_error")
    if acc and len(ok) / len(acc) < a.min_success: problems.append(f"tỉ lệ succeeded {len(ok)}/{len(acc)} < {a.min_success}")
    if any(r["status"] == "failed" and not r["error"] for r in bad): problems.append("job failed không có mã lý do")
    if a.p95_max and tot and pct(tot, 95) > a.p95_max: problems.append(f"p95 {pct(tot,95)} > {a.p95_max}")
    if a.json:
        Path(a.json).write_text(json.dumps({"base": a.base, "concurrency": a.concurrency, "rounds": a.rounds, "wall_s": round(wall, 1), "rows": rows}, ensure_ascii=False, indent=1))
    print("PASS" if not problems else "FAIL: " + "; ".join(problems))
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
