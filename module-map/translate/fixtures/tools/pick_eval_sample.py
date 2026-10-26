#!/usr/bin/env python3
"""Chọn ngẫu nhiên 30 mẫu (tệp + ảnh) từ fixtures/formats/manifest.json để đánh giá.

Quy tắc (readme.md mục "Lấy mẫu đánh giá"):
  - 30 mẫu = 6 định dạng x 5 mẫu: docx, xlsx, pdf (lớp chữ), pdf-scan, png, jpg.
  - TỈ LỆ DÀI : NGẮN = 15 : 15 (bằng nhau). Mỗi định dạng 5 mẫu: 3 định dạng (chọn ngẫu nhiên theo seed) lấy 3 long + 2 short,
    3 định dạng còn lại lấy 2 long + 3 short. Ưu tiên ngôn ngữ khác nhau trong cùng định dạng.
  - Không lấy size_class "xl" (tệp rất lớn kiểm riêng).
  - Seed in ra và phải ghi vào file kết quả để chạy lại được cùng bộ mẫu.
Chạy:  python3 pick_eval_sample.py [--seed N] [--json]
"""
import argparse, json, random, sys
from pathlib import Path

FX = Path(__file__).resolve().parent.parent
FORMATS = ["docx", "xlsx", "pdf", "pdf-scan", "png", "jpg"]


def pick(items, seed):
    rng = random.Random(seed)
    long_heavy = set(rng.sample(FORMATS, 3))  # 3 định dạng 3L+2S, 3 định dạng 2L+3S -> tổng 15 long + 15 short
    chosen = []
    for fmt in FORMATS:
        pool = [i for i in items if i["format"] == fmt and i["size_class"] in ("short", "long")]
        used_langs = set()
        plan = ["long"] * (3 if fmt in long_heavy else 2) + ["short"] * (2 if fmt in long_heavy else 3)
        rng.shuffle(plan)
        for cls in plan:
            cands = [c for c in pool if c["size_class"] == cls and c not in chosen]
            fresh = [c for c in cands if c["lang"] not in used_langs] or cands
            c = rng.choice(fresh)
            used_langs.add(c["lang"]); chosen.append(c)
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else random.SystemRandom().randrange(1, 10**9)
    m = json.loads((FX / "formats" / "manifest.json").read_text(encoding="utf-8"))
    sel = pick(m["items"], seed)
    if len(sel) != 30:
        sys.exit(f"chọn được {len(sel)} mẫu, cần 30")
    nl = sum(x["size_class"] == "long" for x in sel)
    if nl != 15:
        sys.exit(f"tỉ lệ dài:ngắn sai ({nl}:{30 - nl}), cần 15:15")
    if a.json:
        print(json.dumps({"seed": seed, "samples": [{k: s[k] for k in ("id", "path", "format", "lang", "size_class", "kind")}
                                                    for s in sel]}, ensure_ascii=False, indent=1))
        return
    print(f"seed={seed}  (chạy lại: --seed {seed})")
    for n, s in enumerate(sel, 1):
        print(f"{n:2d}. {s['id']:<22} {s['size_class']:<5} {s['bytes']:>8} B  fixtures/{s['path']}")
    print(f"long={sum(s['size_class']=='long' for s in sel)} short={sum(s['size_class']=='short' for s in sel)}")


if __name__ == "__main__":
    main()
