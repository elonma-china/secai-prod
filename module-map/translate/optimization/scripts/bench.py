#!/usr/bin/env python3
"""Đo dịch tệp/ảnh qua API v2 của stack cô lập (cổng 18285).

  bench.py --label baseline [--cases docx20,image,pdfscan] [--src en --tgt vi] [--repeat 1]

Mỗi ca: POST -> poll job (2s, tự lùi khi 429) -> GET result. Ghi 1 dòng JSON/ca vào results/<label>.jsonl:
  wall_s, stages{stage: giây}, status/error, số block, ký tự vào/ra, literal giữ X/Y, marker lạ.
Không in token. Chỉ gọi API; không đổi cấu hình.
"""
import argparse, json, mimetypes, re, sys, time, uuid
from pathlib import Path
import urllib.request, urllib.error

BASE = "http://127.0.0.1:18285"
TR = Path("/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/fixtures")
OUT = Path("/home/ict/secai-opt/results"); OUT.mkdir(exist_ok=True)
LIT = ["svc_backup_01", "P@ss!w0rd#2026$%^&*()_+", "https://portal.example.org/login?user=admin&pass=Ab!9#x",
       "192.168.10.25:8443", "CVE-2024-3094", "ops.team@example.org", "sk-test-9f8E7d6C5b4A3"]
CASES = {  # id -> (route, path, có literal giả?)
    "docx20": ("files", TR / "files/en-long-20pages.docx", False),
    "image": ("images", TR / "formats/png/long-en.png", True),
    "pdfscan": ("files", TR / "formats/pdf-scan/long-en.pdf", True),
    "pdfscan20": ("files", TR / "formats/pdf-scan/xl-en.pdf", False),
    "xlsxinv": ("files", TR / "formats-extra/inventory-300rows-vi.xlsx", False),
    "xlsxxl": ("files", TR / "formats/xlsx/xl-en.xlsx", False),
    "xlsxlong": ("files", TR / "formats/xlsx/long-en.xlsx", True),
    "pentest": ("files", TR / "formats-extra/pentest-report-vi.docx", False),
    "pdftext": ("files", TR / "formats/pdf/long-en.pdf", True),
}
MARK = re.compile(r"⟦|⟧|\[L\d+\]|<L\d+>|\[REDACTED|\*\*\*")


def multipart(fields, fname, data):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    ct = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: {ct}\r\n\r\n'.encode()
                 + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


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


def run_case(cid, src, tgt, max_wait, rec_label="x"):
    route, path, has_lit = CASES[cid]
    data = path.read_bytes()
    body, ct = multipart({"target_lang": tgt, "source_lang": src}, path.name, data)
    t0 = time.monotonic()
    st, job = call("POST", f"{BASE}/ai/v2/translate/{route}", body, {"Content-Type": ct})
    rec = {"case": cid, "file": path.name, "bytes": len(data), "route": route, "post_status": st}
    if st != 202:
        rec.update(status="rejected", error=job.get("error"), wall_s=round(time.monotonic() - t0, 2))
        return rec
    jid = job["job_id"]; stages = {}; last = ("queued", t0); prog = None; poll_s = float(__import__('os').environ.get('BENCH_POLL', '1.5'))
    rec["job_id"] = jid
    while True:
        time.sleep(poll_s)
        now = time.monotonic()
        s, j = call("GET", f"{BASE}/ai/v2/translate/jobs/{jid}")
        if s == 429:  # giới hạn tần suất poll của gateway: lùi rồi thử lại, không tính là lỗi dịch
            poll_s = min(poll_s * 2, 10); continue
        if s != 200:
            rec.update(status="poll_error", poll_status=s); break
        cur = j.get("stage")
        if cur != last[0]:
            stages[last[0]] = round(stages.get(last[0], 0) + now - last[1], 2); last = (cur, now)
        prog = j.get("progress")
        if j["status"] in ("succeeded", "failed", "cancelled"):
            rec["status"] = j["status"]; rec["error"] = j.get("error"); rec["warnings"] = j.get("warnings")
            break
        if now - t0 > max_wait:
            rec["status"] = "timeout"; break
    rec["wall_s"] = round(time.monotonic() - t0, 2)
    stages[last[0]] = round(stages.get(last[0], 0) + time.monotonic() - last[1], 2)
    rec["stages_s"] = stages; rec["progress"] = prog
    if rec.get("status") == "succeeded":
        for _try in range(8):  # giới hạn tra cứu của production: 429 -> chờ rồi thử lại, không coi là kết quả rỗng
            s, r = call("GET", f"{BASE}/ai/v2/translate/jobs/{jid}/result")
            if s != 429:
                break
            time.sleep(3 + 3 * _try)
        txt = r.get("translated_text", "") or ""
        rec.update(blocks=len(r.get("blocks", [])), out_chars=len(txt),
                   in_chars_blocks=sum(len(b.get("source_text", "")) for b in r.get("blocks", [])),
                   stray_markers=len(MARK.findall(txt)), empty_blocks=sum(1 for b in r.get("blocks", []) if not (b.get("translated_text") or "").strip()))
        if has_lit:
            kept = [l for l in LIT if l in txt]
            rec["literal_kept"] = f"{len(kept)}/{len(LIT)}"; rec["literal_missing"] = [l for l in LIT if l not in txt]
        (OUT / f"{rec_label}-{cid}.out.txt").write_text(txt, encoding="utf-8")
        if r.get("ocr"):
            rec["ocr_chars"] = len(r["ocr"].get("raw_text", "")); rec["needs_review"] = r["ocr"].get("needs_review")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True); ap.add_argument("--cases", default="docx20,image,pdfscan")
    ap.add_argument("--src", default="en"); ap.add_argument("--tgt", default="vi")
    ap.add_argument("--repeat", type=int, default=1); ap.add_argument("--max-wait", type=int, default=1800)
    a = ap.parse_args()
    out = OUT / f"{a.label}.jsonl"
    for rep in range(a.repeat):
        for cid in a.cases.split(","):
            rec = run_case(cid, a.src, a.tgt, a.max_wait, a.label)
            rec.update(label=a.label, rep=rep + 1, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
            with out.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps({k: rec.get(k) for k in ("case", "status", "wall_s", "stages_s", "blocks", "literal_kept", "stray_markers", "error")},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
