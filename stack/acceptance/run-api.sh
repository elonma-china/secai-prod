#!/usr/bin/env bash
# Nghiệm thu qua API gateway — dùng cho stack smoke, nơi frontend sản phẩm chưa trỏ tới.
# Gọi đúng các endpoint gateway mà UI đi qua (xem ACCEPTANCE.md, mục "Endpoint"), cùng input và
# cùng expect_all/expect_none trong cases.json. Gateway không đòi khoá API (khác UI, nơi backend IAS
# giữ token đăng nhập).
#
#   run-api.sh <base_url> [ID ...]        ví dụ: run-api.sh http://127.0.0.1:8131 UI-CHAT-1
#   ACCEPT_OUT=<thư mục>   nơi ghi <id>.json (mặc định local/stack-acceptance/<ngày>-api)
#   SKIP_CASES="UI-POC-1 UI-POC-2"   bỏ ca (vd. đã chạy qua UI, mỗi ca PoC chỉ chạy 1 lần trên LLM dùng chung)
#   SKIP_REASONS='{"UI-TR-2": "..."}'  lý do ghi cho ca bỏ qua (mặc định "bỏ qua theo SKIP_CASES")
#   STACK_HAS_OCR=0                   bỏ UI-TR-3
# Ca bỏ qua vẫn có <id>.json (pass=null + lý do); summary.json gom pass/fail/skipped. Thoát 0 khi không ca nào trượt.
set -euo pipefail
BASE=${1:?usage: run-api.sh <base_url> [ID ...]}; shift || true
HERE=$(cd "$(dirname "$0")" && pwd)
export BASE HERE IDS="$*"
export OUT=${ACCEPT_OUT:-$HERE/../../local/stack-acceptance/$(date +%Y%m%d)-api}
mkdir -p "$OUT"
exec python3 - <<'PY'
import json, mimetypes, os, re, sys, time, urllib.error, urllib.request, uuid

BASE, HERE, OUT = os.environ["BASE"].rstrip("/"), os.environ["HERE"], os.environ["OUT"]
CASES = json.load(open(f"{HERE}/cases.json", encoding="utf-8"))["cases"]
ONLY = os.environ["IDS"].split()
SKIP = os.environ.get("SKIP_CASES", "").split()
SKIP_REASONS = json.loads(os.environ.get("SKIP_REASONS") or "{}")
USER = f"acceptance-{uuid.uuid4().hex[:8]}"
VI = re.compile("[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]")


def call(method, path, body=None, headers=None, timeout=120):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    h = {"Content-Type": "application/json"} if isinstance(body, dict) else {}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=method, headers=h), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def sse(path, body, timeout):
    """Yield (event, data) from an SSE POST; stops at [DONE]/end."""
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ev = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = line[5:].strip()
                if d == "[DONE]":
                    return
                try:
                    yield ev, json.loads(d)
                except ValueError:
                    pass


def multipart(fields, files):
    b = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, (name, content) in files.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\nContent-Type: {mimetypes.guess_type(name)[0] or "application/octet-stream"}\r\n\r\n'.encode() + content + b"\r\n"
    return out + f"--{b}--\r\n".encode(), {"Content-Type": f"multipart/form-data; boundary={b}"}


def has(text, pat):
    if pat.startswith("re:"):
        return re.search(pat[3:], text, re.I) is not None
    return any(p.lower() in text.lower() for p in pat.split("|"))


def judge(c, text, extra=()):
    why = [f"thiếu: {p}" for p in [*c["expect_all"], *extra] if not has(text, p)]
    why += [f"không được có: {p}" for p in c.get("expect_none", []) if has(text, p)]
    if c.get("min_chars") and len(text) < c["min_chars"]:
        why.append(f"ngắn {len(text)} < {c['min_chars']} ký tự")
    return why


# ---- từng loại ca: trả (text, endpoints, fails, notes) ------------------------------------------
def run_chat(c):
    q = next(iter(c["fields"].values()))
    st, b = call("POST", "/sessions", {"user_id": USER, "title": c["id"]})
    sid = json.loads(b)["session_id"] if st == 200 else None
    if not sid:
        return "", ["POST /sessions"], [f"POST /sessions -> {st}"], {}
    toks, final = [], None
    for ev, d in sse(f"/sessions/{urllib.request.quote(sid, safe='')}/chat/stream", {"user_id": USER, "message": q}, c["timeout_s"]):
        if ev == "token":
            toks.append(d.get("delta", ""))
        elif ev == "final":
            final = d.get("text") or d.get("response") or d.get("answer")
    return final or "".join(toks), ["POST /sessions", "POST /sessions/{sid}/chat/stream"], [], {}


def run_translate_text(c):
    src = next(iter(c["fields"].values()))
    st, b = call("POST", "/ai/translate", {"text": src, "source_lang": "en", "target_lang": "vi"}, timeout=c["timeout_s"])
    if st != 200:
        return "", ["POST /ai/translate"], [f"HTTP {st}"], {}
    return json.loads(b).get("translated_text", ""), ["POST /ai/translate"], [], {}


def run_translate_file(c):
    name = os.path.basename(c["input"])
    kind = "images" if name.rsplit(".", 1)[-1].lower() in ("png", "jpg", "jpeg", "webp") else "files"  # UI cũng tách /files và /images
    ep = [f"POST /ai/v2/translate/{kind}", "GET /ai/v2/translate/jobs/{job}", "GET /ai/v2/translate/jobs/{job}/result"]
    body, h = multipart({"source_lang": c["source_value"], "target_lang": c["target_value"]}, {"file": (name, open(f"{HERE}/{c['input']}", "rb").read())})
    st, b = call("POST", ep[0][5:], body, h)
    if st != 202:
        return b.decode()[:300], ep[:1], [f"upload HTTP {st}"], {}
    job = json.loads(b)["job_id"]
    t0 = time.time()
    while time.time() - t0 < c["timeout_s"]:
        j = json.loads(call("GET", f"/ai/v2/translate/jobs/{job}")[1])
        if j["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(3)
    else:
        return "", ep, ["job quá hạn"], {"status": "timeout"}
    notes = {"status": j["status"], "error": (j.get("error") or {}).get("code")}
    if j["status"] != "succeeded":
        return json.dumps(j.get("error") or {}), ep, [f"job {j['status']}"], notes
    st, b = call("GET", f"/ai/v2/translate/jobs/{job}/result")
    return (json.loads(b).get("translated_text", "") if st == 200 else ""), ep, [] if st == 200 else [f"result HTTP {st}"], notes


def run_report(c):
    # backend IAS gom dữ liệu quét rồi gọi gateway; ở đây dùng dữ liệu tổng hợp tổng hợp cố định (synthetic)
    f_to = time.strftime("%Y-%m-%d")
    f_from = time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400))
    summary = ("Tổng số mục tiêu thiết bị được đánh giá: 216; Tổng số lỗ hổng phát hiện: 3982; Số loại lỗ hổng phát hiện: 45; "
               "Số loại lỗ hổng thấp: 0; Số loại lỗ hổng trung bình: 4; Số loại lỗ hổng cao: 96; Số loại lỗ hổng nghiêm trọng: 116")
    data = {"from_date": f_from, "to_date": f_to, "scan_type": 0, "summary": summary}
    body, h = multipart({"data": json.dumps(data), "template_text": c["fields"]["Mẫu template báo cáo"], "output_format": "markdown",
                         "from_date": f_from, "to_date": f_to, "summary": summary}, {})
    st, b = call("POST", "/ai/generate-template-report", body, h, timeout=c["timeout_s"])
    if st != 200:
        return b.decode("utf-8", "replace")[:300], ["POST /ai/generate-template-report"], [f"HTTP {st}"], {}
    d = lambda s: "/".join(reversed(s.split("-")))
    return b.decode("utf-8", "replace"), ["POST /ai/generate-template-report"], [], {"extra": [f"từ {d(f_from)} đến {d(f_to)}"]}


def run_exploit(c):
    cve, target = c["fields"]["Nhập CVE"], c["fields"]["Nhập target"]
    lines, final, steps = [], None, []
    for ev, d in sse("/ai/exploit-v2/stream", {"user_id": USER, "cve": cve, "target": target}, c["timeout_s"]):
        if ev == "step":
            steps.append(d)
            lines.append(f"[{d.get('name')}] {d.get('status')} {d.get('detail') or ''}")
        elif ev == "log":
            lines.append(d.get("line", ""))
        elif ev == "final":
            final = d
    # Kiểm thẳng trên SSE: tên/trạng thái step và các trường của `final` (khác UI: chuỗi hiển thị của FE không có ở đây).
    names = [x.get("name") for x in steps]
    fails = [f"thiếu step {n}" for n in c["api"]["steps"] if n not in names]
    fails += [f"không được có step {n}" for n in c["api"].get("steps_none", []) if n in names]
    if c["api"].get("check_target_status"):
        st = [x.get("status") for x in steps if x.get("name") == "check_target"]
        if c["api"]["check_target_status"] not in st:
            fails.append(f"check_target phải có status {c['api']['check_target_status']}, thấy {st}")
    if not final:
        fails.append("không có sự kiện final")
    elif c["id"] == "UI-POC-3":
        if final.get("ok") is not False or not final.get("reason") or final.get("run_id"):
            fails.append("final của ca dừng ở check_target phải ok=false, có reason, run_id rỗng")
    else:
        if not final.get("run_id"):
            fails.append("final thiếu run_id")
        if final.get("ok") is True and not (final.get("code") and final.get("run_instructions")):
            fails.append("final ok=true nhưng thiếu code/run_instructions")
        if final.get("ok") is False and not final.get("reason"):
            fails.append("final ok=false nhưng thiếu reason")
    lines.append(f"final ok={final.get('ok') if final else None} run_id={final.get('run_id') if final else None} reason={str((final or {}).get('reason'))[:200]}")
    return "\n".join(lines), ["POST /ai/exploit-v2/stream"], fails, {"steps": names, "final_ok": (final or {}).get("ok"), "judge": False}


RUN = {"chat": run_chat, "translate_text": run_translate_text, "translate_file": run_translate_file, "report": run_report, "exploit": run_exploit}

failed = 0
summary = {"base": BASE, "pass": [], "fail": [], "skipped": {}}
for c in CASES:
    if ONLY and c["id"] not in ONLY:
        continue
    rec = {"id": c["id"], "base": BASE, "started": time.strftime("%FT%T")}
    t0 = time.time()
    if c["id"] in SKIP:  # ghi lại để ca bỏ qua không bao giờ bị đọc nhầm là đạt
        rec.update(pass_=None, reason=SKIP_REASONS.get(c["id"], "bỏ qua theo SKIP_CASES"))
    elif c.get("requires") == "ocr" and os.environ.get("STACK_HAS_OCR") == "0":
        rec.update(pass_=None, reason=c.get("skip_message"))
    else:
        try:
            text, eps, fails, notes = RUN[c["kind"]](c)
            extra = notes.pop("extra", [])
            judged = notes.pop("judge", True)  # exploit: kiểm trực tiếp trên SSE (fails), không dùng chuỗi hiển thị của UI
            why = (judge(c, text, extra) if judged else []) + fails
            rec.update(pass_=not why, reason="; ".join(why) or "đạt", output_head=text[:600], endpoints=eps, notes=notes)
            open(f"{OUT}/{c['id']}.txt", "w", encoding="utf-8").write(text)  # toàn văn trả lời, để kiểm lại dấu hiệu sau này
        except Exception as e:  # ca lỗi là dữ liệu, không dừng cả bộ
            rec.update(pass_=False, reason=f"lỗi chạy: {type(e).__name__}: {str(e)[:200]}")
    rec["duration_s"] = round(time.time() - t0)
    rec["pass"] = rec.pop("pass_")
    json.dump(rec, open(f"{OUT}/{c['id']}.json", "w"), ensure_ascii=False, indent=1)
    flag = {True: "PASS", False: "FAIL", None: "SKIP"}[rec["pass"]]
    print(f"{flag} {c['id']} ({rec['duration_s']}s) {rec['reason'][:140]}", flush=True)
    failed += rec["pass"] is False
    if rec["pass"] is None:
        summary["skipped"][c["id"]] = rec["reason"]
    else:
        summary["pass" if rec["pass"] else "fail"].append(c["id"])
json.dump(summary, open(f"{OUT}/summary.json", "w"), ensure_ascii=False, indent=1)
sys.exit(1 if failed else 0)
PY
