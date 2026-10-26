#!/usr/bin/env python3
"""Tiêu chí OCR-KÝ-TỰ-ĐẶC-BIỆT (03 mục 1b.6): OCR phải đọc ĐÚNG TỪNG KÝ TỰ các giá trị nhạy cảm trong ảnh/PDF scan
(mật khẩu `P@ss!w0rd#2026$%^&*()_+`, tài khoản, URL, IP:cổng, CVE, email, khóa) — không rớt/đổi ký tự nào (vd `&`).

  ocr_special_char_check.py --ocr-url http://127.0.0.1:28083 --token-file <tệp token OCR> [--sizes long,short] [--fmts png,jpg] [--langs en,vi,...]
Với mỗi ảnh trong fixtures/formats/{png,jpg}/{long,short}-<lang>.*: gọi POST /v1/ocr, so khớp chuỗi con CHÍNH XÁC từng literal
(manifest `preserve_literals`). In bảng + thống kê THEO KÝ TỰ (ký tự đặc biệt nào hay bị rớt). Mã thoát 0 = PASS (mọi ảnh đủ literal),
1 = FAIL. Không dùng dữ liệu thật; token chỉ đọc từ tệp, không in."""
import argparse, base64, collections, json, sys, time, urllib.error, urllib.request
from pathlib import Path

FX = Path(__file__).resolve().parent.parent
LANGS = ["en", "vi", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar"]


def ocr(url, token, path, model, timeout=200):
    body = json.dumps({"model": model, "image_base64": base64.b64encode(path.read_bytes()).decode(), "calibration_prompt": "conditional"}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/ocr", data=body, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return time.time() - t, None, f"HTTP{e.code}"
    return time.time() - t, "\n".join(b["text"] for b in d.get("blocks", [])), None


def dropped_chars(literal, text):
    """Ký tự của literal bị rớt/đổi: so ký tự trong cửa sổ OCR quanh phần đầu literal (chỉ để chẩn đoán)."""
    import collections as c
    pos = text.find(literal[:4])
    if pos < 0:
        return list(set(literal))  # không tìm thấy cả phần đầu
    window = c.Counter(text[pos:pos + len(literal) + 4])
    need = c.Counter(literal)
    return [ch for ch in need if not ch.isalnum() and window[ch] < need[ch]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr-url", required=True); ap.add_argument("--token-file", required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-VL-2B-Instruct")
    ap.add_argument("--sizes", default="long,short"); ap.add_argument("--fmts", default="png,jpg"); ap.add_argument("--langs", default=",".join(LANGS))
    a = ap.parse_args()
    token = Path(a.token_file).read_text().strip()
    lits = None
    man = json.loads((FX / "formats/manifest.json").read_text(encoding="utf-8"))["items"]
    by_id = {i["id"]: i for i in man}
    total = ok = 0; lost = collections.Counter(); seen = collections.Counter(); errors = 0; times = []
    for fmt in a.fmts.split(","):
        for size in a.sizes.split(","):
            for lang in a.langs.split(","):
                p = FX / "formats" / fmt / f"{size}-{lang}.{fmt}"
                it = by_id.get(f"{fmt}-{size}-{lang}")
                if not p.exists() or not it:
                    continue
                lits = it.get("preserve_literals") or []
                dt, txt, err = ocr(a.ocr_url, token, p, a.model)
                total += 1
                if err:
                    errors += 1; print(f"{fmt} {size:5} {lang:8} LỖI {err}"); continue
                times.append(dt)
                miss = [l for l in lits if l not in txt]
                for l in lits:
                    for ch in set(l):
                        if not ch.isalnum():
                            seen[ch] += 1
                for l in miss:
                    for ch in dropped_chars(l, txt):
                        lost[ch] += 1
                ok += not miss
                print(f"{fmt} {size:5} {lang:8} {dt:5.1f}s literal {len(lits)-len(miss)}/{len(lits)}" + (f"  THIẾU: {miss}" if miss else ""))
    print(f"\nĐủ literal: {ok}/{total}  lỗi HTTP: {errors}  OCR TB: {sum(times)/max(1,len(times)):.1f}s")
    if lost:
        print("Ký tự bị rớt/đổi (số ảnh bị rớt / số ảnh có ký tự đó):", ", ".join(f"{c!r} {lost[c]}/{seen[c]}" for c, _ in lost.most_common(10)))
    print("PASS" if ok == total and not errors else "FAIL (nhãn OCR, tiêu chí 03 mục 1b.6)")
    sys.exit(0 if ok == total and not errors else 1)


if __name__ == "__main__":
    main()
