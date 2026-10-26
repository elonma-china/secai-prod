#!/usr/bin/env python3
"""Chấm CHẤT LƯỢNG BẢN DỊCH có số liệu % (tiêu chí 03 mục 1d). Chạy offline hoặc qua API. Chỉ dùng thư viện chuẩn.

Metric chính: chrF (n-gram ký tự 1..6, beta=2, bỏ khoảng trắng; trung bình P/R theo bậc n rồi F-beta — cùng công thức
/home/ict/secai-opt/quality.py nên so được với số đo cũ: trung bình 69,9). Chấm với gold trong 04-test-data.json
(output_gold.text cho tệp/văn bản, output_gold.translated_text cho ảnh). Mẫu KHÔNG có gold -> "CHƯA CHẤM", không bịa gold.

Một mẫu ĐẠT khi đồng thời: (G0) job succeeded và có đầu ra; (G1) chrF >= ngưỡng theo nhóm hướng dịch;
(G2) tỉ lệ độ dài out/gold trong khoảng; (G3) không sót chữ ngôn ngữ nguồn/ngôn ngữ lạ (<= 5% chữ cái thuộc hệ chữ
không thuộc đích; với đích không-Latin: chữ Latin ngoài literal <= 15%); (G4) đủ số dòng (tệp) / khối cách nhau dòng trống (ảnh) — không bỏ đoạn;
(G5) giữ nguyên literal (preserve_literals + IP/CVE/URL/phiên bản tự trích từ gold) và không có dấu che/marker lạ.
"ĐẠT x/y = z%" = số mẫu qua cả 6 cổng / số mẫu đã chấm. Mã thoát 0 nếu z >= --pass-pct (mặc định 90), 1 nếu không,
2 nếu không có mẫu nào chấm được.

Chế độ offline (đầu ra có sẵn, không gọi mạng):
  quality_score.py --outputs DIR --label q-vllm-bf16 [--kinds file,image,text] [--ids ID,ID] [--json-out F]
    đọc DIR/<label>-<ID>-q.out.txt | DIR/<label>-<ID>.out.txt | DIR/<ID>.out.txt  (mẫu chưa có tệp -> "CHƯA CHẤM")
Chế độ API (tải lên thật, giãn cách --interval giây vì giới hạn upload 5/phút ở production):
  quality_score.py --api-url http://127.0.0.1:18185 [--token-env BIEN | --token-file TEP] [--interval 13]
                   [--save-dir DIR] [--kinds file,image] [--limit N]
  Token (nếu cần) chỉ gửi trong header Authorization, KHÔNG in, KHÔNG ghi vào JSON/tệp.
Tự kiểm: python3 -m unittest fixtures/tools/test_quality_score.py
"""
import argparse, json, mimetypes, os, re, sys, time, unicodedata, uuid
import urllib.error, urllib.request
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TR = HERE.parent.parent  # module-map/translate
DATA = TR / "04-test-data.json"
API_LANG = {"zh-Hans": "zh"}

# ---- ngưỡng (xem 03 mục 1d để biết lý do) -------------------------------------------------------------
CHRF_MIN = {  # theo nhóm hướng dịch; (ngưỡng chrF cho mẫu dài/tệp, ngưỡng cho mẫu ngắn/ảnh một câu)
    "to-vi": (60.0, 50.0),
    "from-vi-latin": (55.0, 45.0),   # vi -> en, fr, de, es, ru, ar (chữ không phải CJK)
    "from-vi-cjk": (38.0, 25.0),     # vi -> zh, ja, ko: chrF theo ký tự thấp hơn do đặc thù metric, không phải dịch kém
}
LEN_RATIO = {"default": (0.75, 1.30), "cjk": (0.60, 1.60)}  # len(out)/len(gold) tính theo ký tự không khoảng trắng
FOREIGN_MAX = 0.05      # tối đa 5% chữ cái thuộc hệ chữ lạ
LATIN_MAX_NONLATIN = 0.15  # đích không-Latin: chữ Latin còn lại (ngoài literal) tối đa 15%
LINES_TOL = 0.10        # số dòng khác gold tối đa 10% (tối thiểu cho phép lệch 0)
SHORT_CHARS = 400       # gold ngắn hơn ngưỡng này -> dùng ngưỡng "mẫu ngắn"
PASS_PCT = 90.0
MARK = re.compile(r"⟦|⟧|\[L\d+\]|<L\d+>|\[REDACTED|\*\*\*|\[đã ẩn\]|\[ẩn thông tin\]")
LIT_RE = [re.compile(p) for p in (r"https?://[A-Za-z0-9._~:/?#@!$&*+,;=%-]*[A-Za-z0-9/_~=]", r"\b\d{1,3}(?:\.\d{1,3}){3}\b", r"\bCVE-\d{4}-\d{3,7}\b",
                                  r"\b\d+\.\d+\.\d+\b")]


# ---- chrF -----------------------------------------------------------------------------------------------
def chrf(hyp, ref, n=6, beta=2.0):
    """chrF 0..100. Bỏ mọi khoảng trắng; n-gram ký tự 1..n; P và R trung bình theo bậc rồi F-beta."""
    def grams(s, k):
        s = "".join(s.split())
        return Counter(s[i:i + k] for i in range(len(s) - k + 1))
    p = r = 0.0
    used = 0
    for k in range(1, n + 1):
        h, g = grams(hyp, k), grams(ref, k)
        if not h or not g:
            continue
        ov = sum((h & g).values())
        p += ov / sum(h.values())
        r += ov / sum(g.values())
        used += 1
    if not used:
        return 0.0
    p, r = p / used, r / used
    return 0.0 if p + r == 0 else (1 + beta ** 2) * p * r / (beta ** 2 * p + r) * 100


# ---- hệ chữ ---------------------------------------------------------------------------------------------
def script_of(ch):
    o = ord(ch)
    if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
        return "kana"
    if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "hangul"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF:
        return "han"
    if 0x0400 <= o <= 0x052F:
        return "cyrillic"
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
        return "arabic"
    if unicodedata.category(ch).startswith("L"):
        return "latin"  # gồm cả chữ Latin có dấu (Việt, Pháp, Đức...)
    return None


ALLOWED = {  # hệ chữ được phép trong bản dịch theo ngôn ngữ đích
    "vi": {"latin"}, "en": {"latin"}, "fr": {"latin"}, "de": {"latin"}, "es": {"latin"},
    "ru": {"cyrillic", "latin"}, "ar": {"arabic", "latin"},
    "zh": {"han", "latin"}, "ja": {"han", "kana", "latin"}, "ko": {"hangul", "latin"},
}


def norm_lang(c):
    return {"zh-Hans": "zh", "zh-CN": "zh", "zh-Hant": "zh", "zh-TW": "zh"}.get(c, c)


def strip_literals(text, literals):
    for lit in sorted(set(literals), key=len, reverse=True):
        text = text.replace(lit, " ")
    for rx in LIT_RE:
        text = rx.sub(" ", text)
    return text


def script_stats(out, tgt, literals=()):
    """Trả (tỉ lệ chữ cái hệ chữ lạ, tỉ lệ Latin còn lại nếu đích không-Latin) sau khi bỏ literal."""
    tgt = norm_lang(tgt)
    t = strip_literals(out, literals)
    cnt = Counter(s for s in map(script_of, t) if s)
    tot = sum(cnt.values())
    if not tot:
        return 1.0, 0.0
    allowed = ALLOWED.get(tgt, {"latin"})
    foreign = sum(v for k, v in cnt.items() if k not in allowed)
    latin_left = cnt.get("latin", 0) / tot if "latin" in allowed and tgt in ("ru", "ar", "zh", "ja", "ko") else 0.0
    return foreign / tot, latin_left


def derived_literals(text):
    out = []
    for rx in LIT_RE:
        out += rx.findall(text)
    return list(OrderedDict.fromkeys(out))


def direction_group(src, tgt):
    src, tgt = norm_lang(src), norm_lang(tgt)
    if tgt == "vi":
        return "to-vi"
    return "from-vi-cjk" if tgt in ("zh", "ja", "ko") else "from-vi-latin"


def blocks(s):
    return [b for b in re.split(r"\n\s*\n", s) if b.strip()]


def nonblank_lines(s):
    return [l for l in s.splitlines() if l.strip()]


# ---- chấm một mẫu ---------------------------------------------------------------------------------------
def score_sample(s, hyp, status="succeeded"):
    """s: dict mẫu (id, src, tgt, gold, literals, kind). Trả dict kết quả + cờ cổng."""
    r = {"id": s["id"], "kind": s["kind"], "dir": f"{norm_lang(s['src'])}->{norm_lang(s['tgt'])}",
         "group": direction_group(s["src"], s["tgt"]), "status": status, "gates": {}, "reasons": []}
    if not s.get("gold"):
        r["state"] = "CHƯA CHẤM"; r["reasons"].append("không có gold")
        return r
    if hyp is None:
        r["state"] = "CHƯA CHẤM"; r["reasons"].append("chưa có đầu ra")
        return r
    gold = s["gold"]
    tgt = norm_lang(s["tgt"])
    short = len("".join(gold.split())) < SHORT_CHARS
    cj = tgt in ("zh", "ja", "ko")
    g = r["gates"]
    g["G0_run"] = status == "succeeded" and bool(hyp.strip())
    if not g["G0_run"]:
        r["reasons"].append(f"job {status} hoặc đầu ra trống")
    r["chrf"] = round(chrf(hyp, gold), 1) if hyp.strip() else 0.0
    thr = CHRF_MIN[r["group"]][1 if short else 0]
    r["chrf_min"] = thr
    g["G1_chrf"] = r["chrf"] >= thr
    if not g["G1_chrf"]:
        r["reasons"].append(f"chrF {r['chrf']} < {thr}")
    lo, hi = LEN_RATIO["cjk" if cj else "default"]
    ratio = len("".join(hyp.split())) / max(1, len("".join(gold.split())))
    r["len_ratio"] = round(ratio, 2)
    g["G2_len"] = lo <= ratio <= hi
    if not g["G2_len"]:
        r["reasons"].append(f"độ dài {r['len_ratio']} ngoài [{lo},{hi}]")
    lits = list(s.get("literals") or []) + derived_literals(gold)
    foreign, latin_left = script_stats(hyp, tgt, lits)
    r["foreign_pct"] = round(foreign * 100, 1)
    r["latin_left_pct"] = round(latin_left * 100, 1)
    g["G3_script"] = foreign <= FOREIGN_MAX and latin_left <= LATIN_MAX_NONLATIN
    if foreign > FOREIGN_MAX:
        r["reasons"].append(f"{r['foreign_pct']}% chữ thuộc hệ chữ lạ (sót ngôn ngữ nguồn?)")
    if latin_left > LATIN_MAX_NONLATIN:
        r["reasons"].append(f"{r['latin_left_pct']}% chữ Latin sót ở đích {tgt}")
    if s["kind"] == "image":  # ảnh: xuống dòng do bố cục hình, nên đếm khối cách nhau bằng dòng trống
        gl, hl = len(blocks(gold)), len(blocks(hyp))
    else:
        gl, hl = len(nonblank_lines(gold)), len(nonblank_lines(hyp))
    r["lines"] = f"{hl}/{gl}"
    g["G4_lines"] = abs(hl - gl) <= int(LINES_TOL * gl)
    if not g["G4_lines"]:
        r["reasons"].append(f"số dòng {hl} khác gold {gl}")
    lits_all = list(OrderedDict.fromkeys(lits))
    missing = [l for l in lits_all if l not in hyp]
    markers = sorted(set(MARK.findall(hyp)))
    r["literal"] = f"{len(lits_all) - len(missing)}/{len(lits_all)}"
    g["G5_literal"] = not missing and not markers
    if missing:
        r["reasons"].append("thiếu literal: " + ", ".join(m[:40] for m in missing[:4]))
    if markers:
        r["reasons"].append("marker lạ: " + " ".join(markers))
    r["state"] = "ĐẠT" if all(g.values()) else "TRƯỢT"
    return r


# ---- nạp mẫu --------------------------------------------------------------------------------------------
def load_samples(kinds, ids=None):
    d = json.loads(DATA.read_text(encoding="utf-8"))
    out = []
    for c in d["cases"] + d.get("integrity_cases", []):
        m, g = c["mode"], c.get("output_gold") or {}
        kind = {"file": "file", "image": "image", "text_direct": "text"}.get(m)
        if kind not in kinds or (ids and c["id"] not in ids):
            continue
        if kind == "image":
            gold, path = g.get("translated_text"), c["input"].get("image_path")
        elif kind == "file":
            gold, path = g.get("text"), c["input"].get("file_path")
        else:
            gold, path = g.get("text"), None
        if kind in ("file", "image") and not path:
            continue
        out.append({"id": c["id"], "kind": kind, "src": c["source_lang"], "tgt": c["target_lang"], "gold": gold,
                    "literals": g.get("preserve_literals") or [], "path": path, "text": c["input"].get("text"),
                    "ocr_gold": g.get("ocr_text")})
    return out


def find_output(dirp, label, sid):
    names = ([f"{label}-{sid}-q.out.txt", f"{label}-{sid}.out.txt"] if label else []) + [f"{sid}.out.txt"]
    for n in names:
        p = Path(dirp) / n
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


# ---- API ------------------------------------------------------------------------------------------------
def _call(method, url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def _multipart(fields, fname, data):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    ct = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: {ct}\r\n\r\n'.encode()
                 + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def api_translate(base, s, headers, max_wait=600):
    """Trả (status, translated_text, ocr_raw_text, wall_s)."""
    p = Path(s["path"])
    route = "images" if s["kind"] == "image" else "files"
    body, ct = _multipart({"target_lang": API_LANG.get(s["tgt"], s["tgt"]), "source_lang": API_LANG.get(s["src"], s["src"])},
                          p.name, p.read_bytes())
    t0 = time.monotonic()
    for attempt in range(6):  # 429 giới hạn upload: lùi rồi thử lại
        st, job = _call("POST", f"{base}/ai/v2/translate/{route}", body, {**headers, "Content-Type": ct})
        if st != 429:
            break
        time.sleep(15 + 5 * attempt)
    if st != 202:
        return f"rejected:{st}", None, None, time.monotonic() - t0
    jid, poll = job["job_id"], 0.5
    while True:
        time.sleep(poll)
        s2, j = _call("GET", f"{base}/ai/v2/translate/jobs/{jid}", headers=headers)
        if s2 == 429:
            poll = min(poll * 2, 10); continue
        if s2 != 200:
            return f"poll_error:{s2}", None, None, time.monotonic() - t0
        if j["status"] in ("succeeded", "failed", "cancelled"):
            break
        if time.monotonic() - t0 > max_wait:
            return "timeout", None, None, time.monotonic() - t0
    wall = time.monotonic() - t0
    if j["status"] != "succeeded":
        return (j["status"] + ":" + str((j.get("error") or {}).get("code", ""))), None, None, wall
    s3, r = _call("GET", f"{base}/ai/v2/translate/jobs/{jid}/result", headers=headers)
    return "succeeded", r.get("translated_text", "") or "", (r.get("ocr") or {}).get("raw_text"), wall


# ---- tổng kết -------------------------------------------------------------------------------------------
def summarize(rows):
    graded = [r for r in rows if r["state"] in ("ĐẠT", "TRƯỢT")]
    ok = [r for r in graded if r["state"] == "ĐẠT"]

    def agg(key):
        d = OrderedDict()
        for r in sorted(graded, key=lambda x: str(x.get(key))):
            d.setdefault(r.get(key), []).append(r)
        return {k: {"n": len(v), "dat": sum(1 for x in v if x["state"] == "ĐẠT"),
                    "pct": round(100 * sum(1 for x in v if x["state"] == "ĐẠT") / len(v), 1),
                    "chrf_mean": round(sum(x["chrf"] for x in v) / len(v), 1)} for k, v in d.items()}
    return {"graded": len(graded), "dat": len(ok), "pct": round(100 * len(ok) / len(graded), 1) if graded else None,
            "chrf_mean": round(sum(r["chrf"] for r in graded) / len(graded), 2) if graded else None,
            "ungraded": len(rows) - len(graded), "by_group": agg("group"), "by_kind": agg("kind"),
            "by_lang": agg_lang(graded)}


def agg_lang(graded):
    d = OrderedDict()
    for r in graded:
        lang = r["dir"].split("->")[1] if r["group"] != "to-vi" else r["dir"].split("->")[0]
        d.setdefault(lang, []).append(r)
    return {k: {"n": len(v), "dat": sum(1 for x in v if x["state"] == "ĐẠT"),
                "chrf_mean": round(sum(x["chrf"] for x in v) / len(v), 1)} for k, v in sorted(d.items())}


def print_report(rows, summ, pass_pct):
    print(f"{'ID':<18}{'loai':<6}{'huong':<9}{'nhom':<14}{'chrF':>6}{'min':>5}{'dai':>6}{'sot%':>6}{'dong':>8}{'literal':>8}  KQ")
    for r in rows:
        if r["state"] == "CHƯA CHẤM":
            print(f"{r['id']:<18}{r['kind']:<6}{r['dir']:<9}{r['group']:<14}{'-':>6}{'-':>5}{'-':>6}{'-':>6}{'-':>8}{'-':>8}  CHƯA CHẤM ({'; '.join(r['reasons'])})")
            continue
        print(f"{r['id']:<18}{r['kind']:<6}{r['dir']:<9}{r['group']:<14}{r.get('chrf', 0):>6}{r.get('chrf_min', 0):>5.0f}"
              f"{r.get('len_ratio', 0):>6}{r.get('foreign_pct', 0):>6}{r.get('lines', ''):>8}{r.get('literal', ''):>8}  {r['state']}"
              + (f" ({'; '.join(r['reasons'])})" if r["reasons"] else ""))
    print("\nTheo nhóm hướng dịch:")
    for k, v in summ["by_group"].items():
        print(f"  {k:<14} ĐẠT {v['dat']}/{v['n']} = {v['pct']}%   chrF TB {v['chrf_mean']}")
    print("Theo loại đầu vào:")
    for k, v in summ["by_kind"].items():
        print(f"  {k:<14} ĐẠT {v['dat']}/{v['n']} = {v['pct']}%   chrF TB {v['chrf_mean']}")
    print("Theo ngôn ngữ (ngoại ngữ của cặp):")
    print("  " + "  ".join(f"{k}:{v['dat']}/{v['n']} (chrF {v['chrf_mean']})" for k, v in summ["by_lang"].items()))
    if not summ["graded"]:
        print(f"\nKHÔNG CÓ MẪU NÀO CHẤM ĐƯỢC ({summ['ungraded']} chưa chấm).")
        return 2
    print(f"\nchrF trung bình: {summ['chrf_mean']}   (mẫu chưa chấm: {summ['ungraded']})")
    ok = summ["pct"] >= pass_pct
    print(f"ĐẠT {summ['dat']}/{summ['graded']} = {summ['pct']}%  -> {'PASS' if ok else 'FAIL'} "
          f"(ngưỡng tổng {pass_pct:g}%: {'đạt' if ok else 'chưa đạt'} vì {summ['pct']}% {'>=' if ok else '<'} {pass_pct:g}%)")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kinds", default="file,image", help="file,image,text (mặc định file,image)")
    ap.add_argument("--ids", default="", help="danh sách ID, phân tách dấu phẩy")
    ap.add_argument("--outputs", help="thư mục đầu ra *.out.txt (chế độ offline)")
    ap.add_argument("--label", default="", help="tiền tố tệp đầu ra, ví dụ q-vllm-bf16")
    ap.add_argument("--api-url", help="gốc API, ví dụ http://127.0.0.1:18185")
    ap.add_argument("--token-env", help="tên biến môi trường chứa token (nếu cần)")
    ap.add_argument("--token-file", help="tệp chứa token (nếu cần)")
    ap.add_argument("--interval", type=float, default=13.0, help="giây giữa hai lần tải lên (production 5/phút => >=13)")
    ap.add_argument("--save-dir", help="lưu bản dịch *.out.txt (không chứa token)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pass-pct", type=float, default=PASS_PCT)
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)
    if bool(a.outputs) == bool(a.api_url):
        ap.error("chọn đúng một trong --outputs (offline) hoặc --api-url")
    samples = load_samples(set(a.kinds.split(",")), set(filter(None, a.ids.split(","))))
    if a.limit:
        samples = samples[:a.limit]
    headers = {}
    if a.api_url:
        tok = os.environ.get(a.token_env or "", "").strip() if a.token_env else ""
        if a.token_file:
            tok = Path(a.token_file).read_text().strip()
        if tok:
            headers["Authorization"] = "Bearer " + tok
        if a.save_dir:
            Path(a.save_dir).mkdir(parents=True, exist_ok=True)
    rows, last = [], 0.0
    for s in samples:
        status, hyp, ocr, wall = "succeeded", None, None, None
        if a.outputs:
            hyp = find_output(a.outputs, a.label, s["id"])
        elif s["kind"] == "text" or not s["path"] or not s["gold"]:
            hyp = None  # văn bản trực tiếp không đi qua API tệp
        else:
            gap = a.interval - (time.monotonic() - last)
            if gap > 0 and last:
                time.sleep(gap)
            last = time.monotonic()
            status, hyp, ocr, wall = api_translate(a.api_url.rstrip("/"), s, headers)
            if hyp is not None and a.save_dir:
                (Path(a.save_dir) / f"{s['id']}.out.txt").write_text(hyp, encoding="utf-8")
        r = score_sample(s, hyp, status if hyp is not None else status)
        if hyp is None and status != "succeeded":  # lỗi chạy thật = TRƯỢT, không phải "chưa chấm"
            r.update(state="TRƯỢT", chrf=0.0, reasons=[f"job {status}"], gates={"G0_run": False})
        if wall is not None:
            r["wall_s"] = round(wall, 1)
        if ocr is not None and s.get("ocr_gold"):
            r["ocr_chrf"] = round(chrf(ocr, s["ocr_gold"]), 1)  # chỉ để tham khảo, không vào cổng
        rows.append(r)
        if a.api_url:
            print(f"  .. {s['id']}: {r['state']} chrF={r.get('chrf')}", file=sys.stderr, flush=True)
    summ = summarize(rows)
    code = print_report(rows, summ, a.pass_pct)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "pass_pct": a.pass_pct,
                                                "summary": summ, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
