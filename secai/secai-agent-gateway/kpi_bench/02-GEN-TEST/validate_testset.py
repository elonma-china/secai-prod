#!/usr/bin/env python3
"""validate_testset.py — Cổng chất lượng bộ test (bản Python).

Vì sao cần bản Python: cổng gốc `source/eval/self-eval/acceptance-quality.ts` chạy bằng
`bun`, mà **bun không được cài trên máy giao hàng** → cổng đó hiện không chạy được.
Bản này port lại ĐÚNG bộ kiểm tra của cổng gốc để hai bên không lệch chuẩn, và bổ sung
2 kiểm tra ground truth theo `datasets-acceptance/README.md:37-43`.

Chạy:
    python3 validate_testset.py <thư-mục-chứa-*.jsonl> [--pool <cve_pool.jsonl>] [--quiet]

Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT.
"""
import argparse
import collections
import json
import pathlib
import re
import sys

VALID_CAPS = ["cve_qa", "pentest", "sast", "exploit", "remediation",
              "translation", "report", "poc_search"]
VALID_DIFFS = ["de", "trung_binh", "kho", "tu_duy"]
VALID_BUCKETS = ["dinh_danh", "khac_phuc", "tinh_huong", "ngoai_le"]
MIN_SAMPLES_PER_CAP = 5
MIN_INPUT_LEN = 15

BUCKET_TARGET = {"dinh_danh": 0.40, "khac_phuc": 0.40, "tinh_huong": 0.17, "ngoai_le": 0.03}
BUCKET_TOL = 0.02                      # ±2 điểm %
DIFF_TARGET = {"de": 0.40, "trung_binh": 0.30, "kho": 0.20, "tu_duy": 0.10}
DIFF_TOL = 0.04                        # ±4 điểm %
MIN_TEMPLATES_PER_BUCKET = 10

# gốc repo suy từ vị trí script: <repo>/KPI-VA-GEN-TEST/02-GEN-TEST/validate_testset.py
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,7}$", re.I)
CVE_ANY = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
SHELL_META = set(";|&`$><\n")

_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s
RED, GREEN, YELLOW = (lambda s: _c("31", s)), (lambda s: _c("32", s)), (lambda s: _c("33", s))


class Report:
    def __init__(self):
        self.v = []                      # vi phạm

    def add(self, sid, check, detail):
        self.v.append({"id": sid, "check": check, "detail": detail})

    def by_check(self):
        d = collections.OrderedDict()
        for x in self.v:
            d.setdefault(x["check"], []).append(x)
        return d


def load(dirpath):
    p = pathlib.Path(dirpath)
    # bỏ qua tệp phụ trợ: _pool.jsonl, _*.jsonl (không phải mẫu test)
    files = ([f for f in sorted(p.glob("*.jsonl")) if not f.name.startswith("_")]
             if p.is_dir() else [p])
    rows = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception as e:
                print(RED(f"✗ {f.name}:{i} JSON hỏng: {e}"))
                sys.exit(1)
            r["_file"] = f.name
            rows.append(r)
    return rows, files


def load_pool(path):
    """Tập cve_id hợp lệ (dùng cho grounded_cve). None = bỏ kiểm tra này."""
    if not path:
        return None
    p = pathlib.Path(path)
    if not p.exists():
        return None
    ids = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.update(c.upper() for c in CVE_ANY.findall(line))
    return ids


def validate(rows, base_dir, pool=None):
    R = Report()
    seen_ids, seen_inputs = {}, {}
    cap_count = collections.Counter()
    bucket_count = collections.Counter()
    diff_by_bucket = collections.defaultdict(collections.Counter)
    tmpl_by_bucket = collections.defaultdict(set)

    for r in rows:
        sid = r.get("id") or "(thiếu id)"

        # 1. trường bắt buộc
        for f in ("id", "capability", "input", "expected"):
            if not r.get(f):
                R.add(sid, "required_field", f"thiếu `{f}`")
        # 2-4. giá trị hợp lệ
        cap = r.get("capability")
        if cap not in VALID_CAPS:
            R.add(sid, "valid_capability", f"capability='{cap}'")
        if r.get("difficulty") and r["difficulty"] not in VALID_DIFFS:
            R.add(sid, "valid_difficulty", f"difficulty='{r['difficulty']}'")
        if r.get("bucket") and r["bucket"] not in VALID_BUCKETS:
            R.add(sid, "valid_bucket", f"bucket='{r['bucket']}'")
        # 5. trùng id
        if sid in seen_ids:
            R.add(sid, "duplicate_id", f"trùng với {seen_ids[sid]}")
        seen_ids[sid] = r.get("_file")
        # 6. trùng input (đã chuẩn hoá)
        key = re.sub(r"\s+", " ", (r.get("input") or "").strip().lower())
        if key and key in seen_inputs:
            R.add(sid, "duplicate_input", f"trùng nội dung với {seen_inputs[key]}")
        seen_inputs[key] = sid
        # 7. độ dài input
        if len((r.get("input") or "").strip()) < MIN_INPUT_LEN:
            R.add(sid, "min_input_length", f"{len((r.get('input') or '').strip())} < {MIN_INPUT_LEN}")

        exp = r.get("expected") or {}
        meta = r.get("metadata") or {}
        tags = r.get("tags") or []
        is_trap = "trap" in tags or r.get("bucket") == "ngoai_le"

        # 8. định dạng CVE trong metadata
        cid = meta.get("cve_id")
        if cid and not CVE_RE.match(str(cid)):
            R.add(sid, "cve_format", f"cve_id='{cid}'")
        # 9-10. SAST phải có file thật + must_find_cwe
        if cap == "sast":
            files = r.get("files") or []
            if not files:
                R.add(sid, "sast_file", "câu sast không có `files`")
            else:
                for rel in files:
                    # `files` có thể là: đường dẫn tuyệt đối · repo-relative (quy ước bộ
                    # nghiệm thu) · hoặc tương đối với chính thư mục bộ đề. Thử đủ các gốc
                    # để kết quả KHÔNG phụ thuộc thư mục đang đứng khi chạy.
                    cand = [pathlib.Path(rel), REPO_ROOT / rel, base_dir / rel,
                            base_dir.parent / rel, base_dir / pathlib.Path(rel).name,
                            base_dir / "vuln-samples" / pathlib.Path(rel).name]
                    if not any(c.exists() for c in cand):
                        R.add(sid, "sast_file_exists", f"không thấy tệp `{rel}`")
            if not exp.get("must_find_cwe"):
                R.add(sid, "sast_must_find_cwe", "thiếu must_find_cwe")
        # 11. PoC query không chứa ký tự shell
        if cap == "poc_search":
            bad = SHELL_META & set(r.get("input") or "")
            if bad:
                R.add(sid, "poc_no_shell_meta", f"ký tự {sorted(bad)}")
        # 12. có template_id
        if not meta.get("template_id"):
            R.add(sid, "has_template_id", "thiếu metadata.template_id")
        # 13. grounded_cve — mọi CVE nhắc tới phải có thật (trừ câu bẫy)
        if pool is not None and not is_trap:
            for c in CVE_ANY.findall(json.dumps(
                    {"i": r.get("input"), "e": exp, "m": meta}, ensure_ascii=False)):
                if c.upper() not in pool:
                    R.add(sid, "grounded_cve", f"{c} không có trong nguồn")
                    break
        # 14. trap_not_real — CVE giả KHÔNG được có thật
        if is_trap:
            fake = meta.get("fake_cve")
            if fake and pool is not None and fake.upper() in pool:
                R.add(sid, "trap_not_real", f"{fake} lại CÓ THẬT trong nguồn")
            if not exp.get("must_contain_any"):
                R.add(sid, "trap_expect_refusal", "câu bẫy phải có must_contain_any (lời từ chối)")
            if exp.get("must_contain"):
                R.add(sid, "trap_no_must_contain", "câu bẫy không nên ép must_contain")
        # 15. advisory_not_remediation — câu TƯ VẤN (template kp_*) không được gắn
        #     capability=remediation, vì grader remediation ép mục Before/After/Diff.
        #     (port đúng acceptance-quality.ts:157-159)
        if (r.get("bucket") == "khac_phuc"
                and str(meta.get("template_id") or "").startswith("kp_")
                and cap == "remediation"):
            R.add(sid, "advisory_not_remediation",
                  "tư vấn khắc phục phải dùng capability cve_qa")
        # 16. must_use_tool chỉ đặt cho câu có CVE-id  [BỔ SUNG]
        if "qdrant_search" in (exp.get("must_use_tool") or []) and not cid and not is_trap:
            R.add(sid, "tool_needs_cve", "must_use_tool=qdrant_search nhưng không có metadata.cve_id")
        # 17. must_contain chỉ được chứa CVE ĐÃ KHAI TRONG metadata  [BỔ SUNG]
        #     (câu so sánh 2 lỗ hổng khai thêm cve_id_2 / cve_ids)
        known = {str(c).upper() for c in
                 ([cid, meta.get("cve_id_2")] + list(meta.get("cve_ids") or []))
                 if c}
        for term in (exp.get("must_contain") or []):
            if CVE_ANY.fullmatch(term) and known and term.upper() not in known:
                R.add(sid, "must_contain_grounded",
                      f"'{term}' không có trong metadata (cve_id/cve_id_2/cve_ids)")

        cap_count[cap] += 1
        b = r.get("bucket")
        if b:
            bucket_count[b] += 1
            diff_by_bucket[b][r.get("difficulty")] += 1
            if meta.get("template_id"):
                tmpl_by_bucket[b].add(meta["template_id"])

    total = len(rows)

    # 18. phủ tối thiểu mỗi capability
    for c in VALID_CAPS:
        if 0 < cap_count[c] < MIN_SAMPLES_PER_CAP:
            R.add("(coverage)", "capability_coverage",
                  f"{c}={cap_count[c]} < {MIN_SAMPLES_PER_CAP}")

    # 19. phân bố bucket
    for b, target in BUCKET_TARGET.items():
        frac = bucket_count[b] / max(total, 1)
        if abs(frac - target) > BUCKET_TOL:
            R.add("(dist)", "bucket_distribution",
                  f"{b}={frac*100:.1f}% (mục tiêu {target*100:.0f}±{BUCKET_TOL*100:.0f})")

    # 20. phân bố difficulty theo TỪNG bucket (ngoai_le được miễn)
    for b, dm in diff_by_bucket.items():
        if b == "ngoai_le":
            continue
        bt = bucket_count[b] or 1
        for d, target in DIFF_TARGET.items():
            frac = dm[d] / bt
            if abs(frac - target) > DIFF_TOL:
                R.add("(dist)", "difficulty_distribution",
                      f"{b}/{d}={frac*100:.1f}% (mục tiêu {target*100:.0f}±{DIFF_TOL*100:.0f})")

    # 21. đa dạng template (ngoai_le được miễn)
    for b in ("dinh_danh", "khac_phuc", "tinh_huong"):
        n = len(tmpl_by_bucket.get(b, ()))
        if bucket_count[b] and n < MIN_TEMPLATES_PER_BUCKET:
            R.add("(diversity)", "template_diversity",
                  f"{b}: {n} template < {MIN_TEMPLATES_PER_BUCKET}")

    stats = {"total": total, "bucket": dict(bucket_count), "capability": dict(cap_count),
             "diff_by_bucket": {k: dict(v) for k, v in diff_by_bucket.items()},
             "templates": {k: len(v) for k, v in tmpl_by_bucket.items()}}
    return R, stats


def main():
    ap = argparse.ArgumentParser(description="Cổng chất lượng bộ test secAI")
    ap.add_argument("dataset", help="thư mục chứa *.jsonl (hoặc 1 file .jsonl)")
    ap.add_argument("--pool", help="file nguồn để kiểm grounded_cve (vd cve_pool.jsonl)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    base = pathlib.Path(args.dataset)
    rows, files = load(args.dataset)
    if not rows:
        print(RED(f"✗ Không có mẫu nào trong {args.dataset}"))
        return 1

    auto_pool = base / "_meta" / "pool.jsonl" if base.is_dir() else None
    if not args.pool and auto_pool and auto_pool.exists():
        args.pool = str(auto_pool)
    pool = load_pool(args.pool)
    R, st = validate(rows, base if base.is_dir() else base.parent, pool)

    if not args.quiet:
        print()
        print(f"  BỘ TEST: {args.dataset}  ({len(files)} tệp · {st['total']} câu)")
        print("  " + "─" * 78)
        print(f"  Bucket    : " + "  ".join(f"{k}={v}" for k, v in st["bucket"].items()))
        print(f"  Capability: " + "  ".join(f"{k}={v}" for k, v in st["capability"].items()))
        for b, dm in st["diff_by_bucket"].items():
            print(f"  Diff[{b:<11}]: " + " ".join(f"{k}={v}" for k, v in dm.items())
                  + f"   (template={st['templates'].get(b, 0)})")
        if pool is None:
            print(YELLOW("  ! Không có --pool → bỏ qua kiểm tra grounded_cve / trap_not_real"))
        print("  " + "─" * 78)

    groups = R.by_check()
    if not groups:
        print(GREEN(f"  ✓ ĐẠT — {st['total']} câu, không có vi phạm nào."))
        print()
        return 0

    print(RED(f"  ✗ KHÔNG ĐẠT — {len(R.v)} vi phạm thuộc {len(groups)} loại:"))
    print()
    for check, items in groups.items():
        print(RED(f"  ● {check}  ({len(items)})"))
        for it in items[:5]:
            print(f"      {it['id']}: {it['detail']}")
        if len(items) > 5:
            print(f"      … và {len(items) - 5} trường hợp nữa")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
