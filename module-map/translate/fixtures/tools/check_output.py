#!/usr/bin/env python3
"""Kiểm bản dịch theo tiêu chí GIỮ NGUYÊN THÔNG TIN (03 mục 1b) bằng so khớp chuỗi con CHÍNH XÁC.

  check_output.py --manifest formats-extra|formats --id UC-C02|<id> --output <tệp bản dịch .txt>
Ra: "literal giữ X/Y" + danh sách thiếu; marker lạ (⟦ ⟧ [L0] <L1> [REDACTED *** ...); và kiểm riêng theo ca
(line_count, json_parse + khóa, must_not_contain). Mã thoát 0 = PASS phần giữ nguyên thông tin, 1 = FAIL.
Không đánh giá nghĩa dịch (việc của người/agent chấm theo 03 mục 1)."""
import argparse, json, re, sys
from pathlib import Path

FX = Path(__file__).resolve().parent.parent
MARK = re.compile(r"⟦|⟧|\[L\d+\]|<L\d+>|\[REDACTED|\*\*\*|\[đã ẩn\]|\[ẩn thông tin\]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="formats-extra"); ap.add_argument("--id", required=True); ap.add_argument("--output", required=True)
    a = ap.parse_args()
    m = json.loads((FX / a.manifest / "manifest.json").read_text(encoding="utf-8"))
    it = next((i for i in m["items"] if i.get("uc") == a.id or i.get("id") == a.id), None)
    if not it:
        sys.exit(f"không có mục {a.id} trong {a.manifest}")
    txt = Path(a.output).read_text(encoding="utf-8")
    lits = it.get("preserve_literals") or []
    missing = [l for l in lits if l not in txt]
    markers = MARK.findall(txt)
    fails = []
    print(f"{a.id}: literal giữ {len(lits) - len(missing)}/{len(lits)}")
    for l in missing[:20]:
        print("  THIẾU:", l)
    if missing: fails.append("literal")
    if markers: print("  marker lạ:", sorted(set(markers))); fails.append("marker")
    ck = it.get("checks") or {}
    if "line_count" in ck:
        n = len([l for l in txt.splitlines() if l.strip()])
        ok = n == ck["line_count"]
        print(f"  số dòng: {n} (nguồn {ck['line_count']}) {'OK' if ok else 'LỆCH'}")
        if not ok: fails.append("line_count")
    if ck.get("json_parse"):
        try:
            d = json.loads(txt)
            keys = ck.get("json_keys_unchanged")
            ok = keys is None or list(d) == keys
            print("  JSON parse OK; khóa", "giữ nguyên" if ok else f"ĐỔI: {list(d)}")
            if not ok: fails.append("json_keys")
        except Exception as e:
            print("  JSON KHÔNG parse được:", e); fails.append("json_parse")
    for bad in ck.get("must_not_contain", []):
        if bad in txt:
            print("  CHỨA chuỗi cấm:", bad); fails.append("must_not_contain")
    print("KẾT QUẢ giữ nguyên thông tin:", "PASS" if not fails else "FAIL " + ",".join(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
