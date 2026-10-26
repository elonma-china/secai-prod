#!/usr/bin/env python3
"""Kiểm OCR ở MỨC PIPELINE (đúng đường người dùng đi: phóng ảnh/render trang -> OCR -> dịch) cho ảnh VÀ PDF scan.

  ocr_pipeline_check.py --base http://127.0.0.1:18285 [--concurrency 6] [--fmts png,jpg,pdf-scan] [--sizes long,short]
                        [--langs en,vi,...] [--poll 1.5] [--json out.json]

`ocr_special_char_check.py` chỉ gọi thẳng /v1/ocr trên ảnh gốc nên KHÔNG thấy lỗi phát sinh khi pipeline phóng ảnh hoặc
render trang PDF (đã gặp: PDF scan tiếng Nhật đảo `&*` thành `*&`; ảnh terminal tối sau phóng to mất `()_+`).
Mỗi tệp trong fixtures/formats/{png,jpg,pdf-scan}/{long,short}-<ngôn ngữ> được gửi lên API (nguồn -> vi; nguồn vi -> en),
sau đó so khớp chuỗi con CHÍNH XÁC từng giá trị `preserve_literals` (mật khẩu, IP:cổng, URL, CVE, email, khóa...) trong
bản dịch trả về, và báo: ký tự nào của mật khẩu bị rớt/đảo, giây/trang, cảnh báo. Mã thoát 0 = PASS (100% đủ, không lỗi).
Giới hạn upload production (5/phút): dùng --concurrency 1 --gap 13 hoặc chạy trên stack thử."""
import argparse, json, mimetypes, sys, threading, time, urllib.error, urllib.request, uuid
from pathlib import Path

FX = Path(__file__).resolve().parent.parent
LANGS = ["en", "vi", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar"]
API = {"zh-Hans": "zh"}


def call(method, url, body=None, headers=None, timeout=120):
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


def dropped(lit, text):
    """Mô tả ngắn lỗi của một literal: ký tự thiếu hoặc bị đảo (chỉ để chẩn đoán)."""
    pos = text.find(lit[:5])
    if pos < 0:
        return "không thấy phần đầu"
    win = text[pos:pos + len(lit) + 6]
    missing = [c for c in sorted(set(lit)) if lit.count(c) > win.count(c)]
    return f"thiếu {''.join(missing)!r}" if missing else f"đổi thứ tự: {win[:len(lit)]!r}"


def run_one(base, item, poll, out, sem):
    with sem:
        path, src, rid, lits, pages = item["path"], item["src"], item["id"], item["lits"], item["pages"]
        route = "images" if path.suffix.lower() in (".png", ".jpg", ".jpeg") else "files"
        tgt = "en" if src == "vi" else "vi"
        body, ct = multipart({"target_lang": tgt, "source_lang": API.get(src, src)}, path.name, path.read_bytes())
        rec = {"id": rid, "pages": pages, "status": None}
        t0 = time.monotonic()
        s, j = call("POST", f"{base}/ai/v2/translate/{route}", body, {"Content-Type": ct})
        if s != 202:
            rec.update(status="rejected", error=(j.get("error") or {}).get("code") or f"HTTP{s}"); out[rid] = rec; return
        jid = j["job_id"]; delay = poll
        while True:
            time.sleep(delay)
            s, jj = call("GET", f"{base}/ai/v2/translate/jobs/{jid}")
            if s == 429: delay = min(delay * 1.5, 8); continue
            if s != 200: rec.update(status="poll_error", error=f"HTTP{s}"); break
            if jj["status"] in ("succeeded", "failed", "cancelled"):
                rec.update(status=jj["status"], error=(jj.get("error") or {}).get("code"), warnings=sorted({w.get("code") for w in jj.get("warnings", [])})); break
        rec["wall_s"] = round(time.monotonic() - t0, 2)
        if rec["status"] == "succeeded":
            for _ in range(8):
                s, r = call("GET", f"{base}/ai/v2/translate/jobs/{jid}/result")
                if s != 429: break
                time.sleep(3)
            text = r.get("translated_text", "") or ""
            miss = [l for l in lits if l not in text]
            rec.update(literal=f"{len(lits) - len(miss)}/{len(lits)}", missing=[{"literal": l, "why": dropped(l, text)} for l in miss],
                       s_per_page=round(rec["wall_s"] / max(1, pages), 2))
        out[rid] = rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--fmts", default="png,jpg,pdf-scan"); ap.add_argument("--sizes", default="long,short")
    ap.add_argument("--langs", default=",".join(LANGS)); ap.add_argument("--poll", type=float, default=1.5)
    ap.add_argument("--gap", type=float, default=0, help="giây giữa các lần tải (production: 13)"); ap.add_argument("--json")
    a = ap.parse_args()
    man = {i["id"]: i for i in json.loads((FX / "formats/manifest.json").read_text(encoding="utf-8"))["items"]}
    items = []
    for fmt in a.fmts.split(","):
        for size in a.sizes.split(","):
            for lang in a.langs.split(","):
                rid = f"{fmt}-{size}-{lang}"; ext = "pdf" if fmt == "pdf-scan" else fmt
                p = FX / "formats" / fmt / f"{size}-{lang}.{ext}"
                if p.exists() and rid in man:
                    st = man[rid]["structure"]
                    items.append({"path": p, "src": lang, "id": rid, "lits": man[rid].get("preserve_literals") or [], "pages": st.get("pages") or 1})
    out, th, sem = {}, [], threading.Semaphore(a.concurrency)
    t0 = time.monotonic()
    for it in items:
        t = threading.Thread(target=run_one, args=(a.base, it, a.poll, out, sem)); t.start(); th.append(t)
        if a.gap: time.sleep(a.gap)
    for t in th: t.join()
    rows = [out[i["id"]] for i in items if i["id"] in out]
    bad = [r for r in rows if r["status"] != "succeeded" or r["literal"].split("/")[0] != r["literal"].split("/")[1]]
    for r in rows:
        mark = "OK " if r not in bad else "XẤU"
        extra = "" if r["status"] == "succeeded" else f" [{r.get('error') or r['status']}]"
        why = "; ".join(f"{m['literal']} -> {m['why']}" for m in r.get("missing", []))
        print(f"{mark} {r['id']:22} {r.get('wall_s', '-'):>6}s  {r.get('s_per_page', '-'):>6} s/trang  literal {r.get('literal', '-')}{extra} {why}")
    full = [r for r in rows if r not in bad]
    import statistics as st
    sp = [r["s_per_page"] for r in rows if r.get("s_per_page")]
    print(f"\nĐủ literal & thành công: {len(full)}/{len(rows)} = {100*len(full)/max(1,len(rows)):.1f}%   trung vị {st.median(sp) if sp else '-'} s/trang   "
          f"<=3 s/trang: {sum(1 for x in sp if x <= 3)}/{len(sp)}   chạy {time.monotonic()-t0:.0f}s")
    if a.json: Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print("PASS" if not bad else "FAIL (nhãn OCR, tiêu chí 03 mục 1b.7)")
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
