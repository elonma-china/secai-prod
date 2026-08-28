#!/usr/bin/env python3
"""gen_testset.py — Sinh bộ test độc lập cho secAI, đúng schema `Sample` của hệ thống.

Đầu ra dùng được NGAY với pipeline chấm có sẵn:
    bash run_eval_all.sh --dataset <thư-mục-vừa-sinh>

Ba nguồn:
    --source qdrant     KB nội bộ (Qdrant cve_kb)   — offline, ground truth chính xác nhất
    --source public     NVD / CISA KEV / Exploit-DB — độc lập hoàn toàn với KB
    --source customer   bảng CSV/XLSX khách hàng    — chạy được offline

Cơ cấu giữ đúng hợp đồng R-EVAL-TESTSET: bucket 40/40/17/3, difficulty 40/30/20/10,
mỗi CVE dùng tối đa 1 lần.

Ví dụ:
    python3 gen_testset.py --source qdrant --n 1000 --out ./testset-indep --exclude-pool
    python3 gen_testset.py --source public --n 300 --feeds nvd,kev --out ./testset-indep
    python3 gen_testset.py --source customer --in MAU-KHACH-HANG.csv --out ./testset-indep
"""
import argparse
import json
import os
import pathlib
import random
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import templates as T                                    # noqa: E402
from sources import from_qdrant, from_public, from_customer   # noqa: E402

REPO = HERE.parents[1]
# nguồn để xây danh sách loại trừ (bảo đảm "không trùng bộ KPI chính")
EXCLUDE_SOURCES = [
    REPO / "source/eval/datasets-acceptance",
    REPO / "source/eval/datasets-acceptance/reserve",
    REPO / "source/eval/datasets-acceptance/_pool/cve_pool.jsonl",
]
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ── loại trừ ──────────────────────────────────────────────────────────────
def build_exclude():
    """Gom mọi cve_id đã dùng trong bộ nghiệm thu hiện có + pool sinh ra nó."""
    ex = set()
    for p in EXCLUDE_SOURCES:
        files = []
        if p.is_dir():
            files = sorted(p.glob("*.jsonl"))
        elif p.is_file():
            files = [p]
        for f in files:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        ex.update(c.upper() for c in CVE_RE.findall(line))
            except Exception as e:
                log(f"   ! bỏ qua {f}: {e}")
    return ex


# ── xây một nhóm câu hỏi ─────────────────────────────────────────────────
def assign_difficulty(n, rng):
    q = T.quotas(n, T.DIFF_RATIOS)
    out = []
    for d, c in q.items():
        out += [d] * c
    rng.shuffle(out)
    return out


def build_group(recs, tmpls, n, bucket, capability, prefix, rng, start=0):
    """Sinh n câu từ `tmpls`, mỗi câu tiêu thụ 1 record."""
    diffs = assign_difficulty(n, rng)
    out = []
    for i in range(n):
        if not recs:
            break
        rec = recs.pop()
        fn = tmpls[i % len(tmpls)]
        try:
            s = fn(rec, rng)
        except Exception as e:
            log(f"   ! template {fn.__name__} lỗi trên {rec.get('cve_id')}: {e}")
            continue
        out.append(finalize(s, fn.__name__, rec, bucket, capability,
                            diffs[i], f"{prefix}_{start + i:04d}"))
    return out


def finalize(s, template_id, rec, bucket, capability, difficulty, sid):
    exp = dict(s["expected"])
    # dọn: bỏ giá trị None/rỗng trong các danh sách
    for k in ("must_contain", "must_contain_any", "must_use_tool", "must_find_cwe",
              "must_have_sections", "keep_terms"):
        if k in exp:
            vals = [v for v in exp[k] if v]
            seen, uniq = set(), []
            for v in vals:
                if v not in seen:
                    seen.add(v)
                    uniq.append(v)
            if uniq:
                exp[k] = uniq
            else:
                exp.pop(k)
    sample = {
        "id": sid,
        "capability": capability,
        "bucket": bucket,
        "difficulty": difficulty,
        "input": s["input"],
        "files": s.get("files", []),
        "expected": exp,
        "tags": ["acceptance"] + list(s.get("tags", [])),
        "notes": s.get("notes", ""),
        "metadata": {
            "cve_id": rec.get("cve_id"),
            "severity": rec.get("severity"),
            "cvss_score": rec.get("cvss_score"),
            "year": rec.get("year"),
            "kev": bool(rec.get("kev")),
            "cwe": rec.get("cwe_refs") or [],
            "products": (rec.get("products") or [])[:5],
            "template_id": template_id,
            "source": rec.get("_source"),
        },
    }
    if s.get("files"):
        sample["metadata"]["file"] = s["files"][0]
    return sample


def build_sast(n, rng, prefix, vuln_dir, out_rel, start=0):
    """Sinh câu SAST + GHI FILE mã nguồn có lỗi thật ra đĩa."""
    vuln_dir.mkdir(parents=True, exist_ok=True)
    diffs = assign_difficulty(n, rng)
    out = []
    for i in range(n):
        cwe, ext, code = T.SAST_BASES[i % len(T.SAST_BASES)]
        fname = f"{prefix}_{start + i:03d}_{cwe.lower().replace('-', '')}.{ext}"
        (vuln_dir / fname).write_text(code, encoding="utf-8")
        rel = f"{out_rel}/{fname}"
        s = T.sast_prompt(cwe, rel,
                          ctx=T.SAST_CONTEXTS[i % len(T.SAST_CONTEXTS)],
                          ask=T.SAST_ASKS[(i // len(T.SAST_CONTEXTS)) % len(T.SAST_ASKS)])
        rec = {"cve_id": None, "severity": None, "cvss_score": None, "year": None,
               "kev": False, "cwe_refs": [cwe], "products": [], "_source": "generated"}
        out.append(finalize(s, "sast_prompt", rec, "khac_phuc", "sast",
                            diffs[i], f"{prefix}_{start + i:04d}"))
    return out


def build_remediation(n, rng, prefix, start=0):
    diffs = assign_difficulty(n, rng)
    out = []
    for i in range(n):
        cwe, code = T.RM_BASES[i % len(T.RM_BASES)]
        fn = T.rm_fix if i % 2 == 0 else T.rm_diff
        s = fn(cwe, code,
               ctx=T.SAST_CONTEXTS[i % len(T.SAST_CONTEXTS)],
               ask=T.RM_ASKS[(i // len(T.SAST_CONTEXTS)) % len(T.RM_ASKS)])
        rec = {"cve_id": None, "severity": None, "cvss_score": None, "year": None,
               "kev": False, "cwe_refs": [cwe], "products": [], "_source": "generated"}
        out.append(finalize(s, fn.__name__, rec, "khac_phuc", "remediation",
                            diffs[i], f"{prefix}_{start + i:04d}"))
    return out


def build_traps(n, rng, prefix, verify=True, start=0):
    """Câu bẫy: CVE giả (đã xác minh zero-hit), off-topic, sản phẩm không tồn tại.

    Off-topic và sản phẩm giả bị giới hạn bởi độ dài danh sách; phần còn thiếu được bù
    bằng CVE giả (nguồn vô hạn) để LUÔN đủ chỉ tiêu.
    """
    n_off = min(n // 3, len(T.OFF_TOPIC))
    n_prod = min(n // 3, len(T.FAKE_PRODUCTS))
    n_fake = n - n_off - n_prod

    out, i = [], 0
    fakes, tried = [], 0
    while len(fakes) < n_fake and tried < 400:
        tried += 1
        year = rng.choice(["2019", "2021", "2023", "2024", "2025"])
        fid = f"CVE-{year}-9999{rng.randint(10, 99)}"
        if fid in fakes:
            continue
        if verify:
            c = from_qdrant.count_cve(fid)
            if c is None:
                log(f"   ! không xác minh được {fid} (Qdrant không phản hồi) — vẫn dùng")
            elif c > 0:
                log(f"   ! {fid} CÓ THẬT trong KB ({c} điểm) → bỏ")
                continue
        fakes.append(fid)

    for fid in fakes:
        s = T.trap_fake_cve(fid, rng)
        smp = finalize(s, "trap_fake_cve", {"cve_id": None, "_source": "generated"},
                       "ngoai_le", "cve_qa", rng.choice(["kho", "tu_duy"]),
                       f"{prefix}_{start + i:04d}")
        smp["metadata"]["fake_cve"] = fid
        out.append(smp)
        i += 1
    for q in rng.sample(T.OFF_TOPIC, n_off):
        s = T.trap_offtopic(q, rng)
        out.append(finalize(s, "trap_offtopic", {"cve_id": None, "_source": "generated"},
                            "ngoai_le", "cve_qa", "kho", f"{prefix}_{start + i:04d}"))
        i += 1
    for pr in rng.sample(T.FAKE_PRODUCTS, n_prod):
        s = T.trap_fake_product(pr, rng)
        smp = finalize(s, "trap_fake_product", {"cve_id": None, "_source": "generated"},
                       "ngoai_le", "cve_qa", "tu_duy", f"{prefix}_{start + i:04d}")
        smp["metadata"]["fake_product"] = pr
        out.append(smp)
        i += 1
    return out[:n]


# ── LLM diễn đạt lại (tuỳ chọn) ──────────────────────────────────────────
def paraphrase(samples, log=log):
    """Diễn đạt lại câu hỏi cho tự nhiên hơn. TUYỆT ĐỐI không đổi ground truth.

    Theo mẫu embedding-rerank-bench/build_semantic_queries.py: gọi endpoint
    OpenAI-compatible, giữ nguyên mọi mã định danh.
    """
    import urllib.request
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL", "")
    key = os.environ.get("LLM_API_KEY", "")
    if not base or not model:
        log("   ! Thiếu LLM_BASE_URL / LLM_MODEL → bỏ qua --use-llm")
        return samples
    ok = 0
    for s in samples:
        keep = list(s["expected"].get("must_contain", [])) + \
               list(s["expected"].get("keep_terms", []))
        prompt = ("Viết lại câu hỏi sau cho tự nhiên hơn bằng tiếng Việt. "
                  "TUYỆT ĐỐI giữ nguyên mọi mã định danh (CVE-…, CWE-…) và không thêm "
                  "thông tin mới. Chỉ trả về câu hỏi đã viết lại, không giải thích.\n\n"
                  + s["input"])
        body = json.dumps({"model": model, "temperature": 0.7, "max_tokens": 400,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log(f"   ! paraphrase lỗi {s['id']}: {e}")
            continue
        # chỉ nhận khi MỌI thuật ngữ bắt buộc còn nguyên
        if txt and all(k.lower() in txt.lower() for k in keep):
            s["input"] = txt
            s["metadata"]["paraphrased"] = True
            ok += 1
    log(f"   · diễn đạt lại thành công {ok}/{len(samples)} câu")
    return samples


# ── main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Sinh bộ test độc lập cho secAI",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, choices=["qdrant", "public", "customer"])
    ap.add_argument("--n", type=int, default=1000, help="tổng số câu (mặc định 1000 theo hợp đồng)")
    ap.add_argument("--out", required=True, help="thư mục đầu ra")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--in", dest="infile", help="(customer) file CSV/XLSX của khách")
    ap.add_argument("--feeds", default="nvd,kev", help="(public) nvd,kev,exploitdb")
    ap.add_argument("--exclude-pool", action="store_true",
                    help="loại mọi CVE đã dùng trong bộ nghiệm thu hiện có (KHUYẾN NGHỊ)")
    ap.add_argument("--enrich", action="store_true",
                    help="(customer) tra NVD/Qdrant bổ sung ground truth còn thiếu")
    ap.add_argument("--use-llm", action="store_true", help="diễn đạt lại câu hỏi bằng LLM")
    ap.add_argument("--skip-verify", action="store_true",
                    help="bỏ xác minh zero-hit cho CVE giả (khi không có Qdrant)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    vuln_dir = out_dir / "vuln-samples"

    exclude = build_exclude() if args.exclude_pool else set()
    if args.exclude_pool:
        log(f"→ Loại trừ {len(exclude)} CVE đã dùng trong bộ nghiệm thu hiện có")

    # ── 1. lấy dữ liệu nguồn ────────────────────────────────────────────
    log(f"→ Nguồn: {args.source}")
    overrides = []
    if args.source == "qdrant":
        ok, msg = from_qdrant.ping()
        log("   " + msg)
        if not ok:
            log("   ✗ Không có Qdrant → dùng --source public hoặc --source customer")
            return 2
        recs = from_qdrant.fetch(max(args.n, 200), exclude=exclude, log=log)
    elif args.source == "public":
        recs = from_public.fetch(max(args.n, 100), feeds=tuple(args.feeds.split(",")),
                                 exclude=exclude, log=log)
    else:
        if not args.infile:
            log("   ✗ --source customer cần --in <file.csv|xlsx>")
            return 2
        qlookup = None
        if args.enrich:
            def qlookup(ids):
                got = {}
                for cid in ids:
                    try:
                        r = from_qdrant._post("/scroll", {
                            "filter": {"must": [{"key": "cve_id", "match": {"value": cid}},
                                                {"key": "chunk_kind", "match": {"value": "core"}}]},
                            "limit": 1, "with_payload": True, "with_vector": False})
                        pts = r["result"]["points"]
                        if pts:
                            got[cid] = from_qdrant._norm(pts[0]["payload"])
                    except Exception:
                        pass
                return got
        recs, overrides = from_customer.fetch(args.infile, enrich=args.enrich,
                                              qdrant_lookup=qlookup, log=log)

    if not recs and not overrides:
        log("   ✗ Không lấy được bản ghi nào")
        return 2
    rng.shuffle(recs)
    all_recs = list(recs) + [o["rec"] for o in overrides]   # bản sao để ghi _pool.jsonl
    log(f"→ Có {len(recs)} bản ghi CVE + {len(overrides)} câu khách tự viết")

    # ── 2. chia chỉ tiêu ────────────────────────────────────────────────
    n = args.n
    bq = T.quotas(n, T.BUCKET_RATIOS)
    log(f"→ Chỉ tiêu: {bq}")

    samples = []

    # câu khách tự viết: giữ nguyên, xếp vào bucket theo capability
    CAP2BUCKET = {"cve_qa": "dinh_danh", "translation": "dinh_danh", "poc_search": "dinh_danh",
                  "sast": "khac_phuc", "remediation": "khac_phuc",
                  "pentest": "tinh_huong", "exploit": "tinh_huong", "report": "tinh_huong"}
    for i, o in enumerate(overrides):
        cap = o["capability"] if o["capability"] in CAP2BUCKET else "cve_qa"
        exp = {"must_contain": o["must_contain"], "language": "vi", "min_words": 40}
        if o["rec"].get("cve_id"):
            exp["must_use_tool"] = ["qdrant_search"]
        samples.append(finalize({"input": o["input"], "expected": exp,
                                 "tags": ["customer"], "notes": o["notes"]},
                                "customer_supplied", o["rec"], CAP2BUCKET[cap], cap,
                                o["difficulty"] or "trung_binh", f"ind_cust_{i:04d}"))

    need = max(0, n - len(samples))
    if need < n:
        bq = T.quotas(need, T.BUCKET_RATIOS)
        log(f"→ Còn {need} câu cần sinh: {bq}")

    # ── phân bổ theo SỐ BẢN GHI CÓ THẬT ────────────────────────────────
    # Mỗi CVE chỉ dùng 1 lần. Nếu nguồn ít hơn chỉ tiêu, thu nhỏ ĐỀU các nhóm
    # tiêu thụ bản ghi để giữ đúng tỉ lệ, thay vì để nhóm đầu vét sạch.
    nd, nk, nt = bq.get("dinh_danh", 0), bq.get("khac_phuc", 0), bq.get("tinh_huong", 0)
    want_rec = round(nd) + round(nk * 0.70) + nt      # dd+tr+ps + kp + pt+ex+rp
    scale = 1.0
    if want_rec > len(recs):
        scale = len(recs) / max(want_rec, 1)
        log(f"→ CẢNH BÁO: chỉ có {len(recs)} bản ghi CVE nhưng cần {want_rec}. "
            f"Thu nhỏ các nhóm còn {scale*100:.0f}% để giữ đúng tỉ lệ.")
        log("   (mỗi CVE chỉ được dùng 1 lần — muốn đủ số câu thì cần thêm CVE ở nguồn)")

    def sc(x):
        return max(0, int(round(x * scale)))

    # ── dinh_danh: cve_qa 70% · translation 15% · poc_search 15% ────────
    n_dd, n_tr = sc(nd * 0.70), sc(nd * 0.15)
    n_ps = sc(nd) - n_dd - n_tr
    samples += build_group(recs, T.DD_TEMPLATES, min(n_dd, len(recs)), "dinh_danh",
                           "cve_qa", "ind_dd", rng)
    samples += build_group(recs, T.TR_TEMPLATES, min(n_tr, len(recs)), "dinh_danh",
                           "translation", "ind_tr", rng)
    samples += build_group(recs, T.PS_TEMPLATES, min(n_ps, len(recs)), "dinh_danh",
                           "poc_search", "ind_ps", rng)

    # ── khac_phuc: cve_qa-advisory 70% · sast 15% · remediation 15% ─────
    # sast/remediation KHÔNG tiêu thụ bản ghi CVE (mã nguồn tự sinh) → giữ nguyên chỉ tiêu.
    n_kp = sc(nk * 0.70)
    n_sa = round(nk * 0.15)
    n_rm = nk - round(nk * 0.70) - n_sa
    samples += build_group(recs, T.KP_TEMPLATES, min(n_kp, len(recs)), "khac_phuc",
                           "cve_qa", "ind_kp", rng)
    if n_sa:
        # Đường dẫn ghi vào `files`: repo-relative khi thư mục đầu ra nằm TRONG repo
        # (đúng quy ước bộ nghiệm thu), ngược lại dùng đường dẫn tuyệt đối cho chắc.
        try:
            out_rel = str(vuln_dir.resolve().relative_to(REPO))
        except ValueError:
            out_rel = str(vuln_dir.resolve())
        samples += build_sast(n_sa, rng, "ind_sast", vuln_dir, out_rel)
    if n_rm:
        samples += build_remediation(n_rm, rng, "ind_rm")

    # ── tinh_huong: pentest 35% · exploit 30% · report 35% ──────────────
    n_pt, n_ex = sc(nt * 0.35), sc(nt * 0.30)
    n_rp = sc(nt) - n_pt - n_ex
    samples += build_group(recs, T.PT_TEMPLATES, min(n_pt, len(recs)), "tinh_huong",
                           "pentest", "ind_pt", rng)
    samples += build_group(recs, T.EX_TEMPLATES, min(n_ex, len(recs)), "tinh_huong",
                           "exploit", "ind_ex", rng)
    samples += build_group(recs, T.RP_TEMPLATES, min(n_rp, len(recs)), "tinh_huong",
                           "report", "ind_rp", rng)

    # ── ngoai_le: trap ─────────────────────────────────────────────────
    n_trap = bq.get("ngoai_le", 0)
    if n_trap:
        verify = (not args.skip_verify) and args.source == "qdrant"
        samples += build_traps(n_trap, rng, "ind_trap", verify=verify)

    # ── 3. khử trùng lặp câu hỏi ───────────────────────────────────────
    seen, uniq = set(), []
    for s in samples:
        k = re.sub(r"\s+", " ", s["input"].strip().lower())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    if len(uniq) != len(samples):
        log(f"→ Bỏ {len(samples) - len(uniq)} câu trùng nội dung")
    samples = uniq

    if args.use_llm:
        log("→ Diễn đạt lại bằng LLM…")
        samples = paraphrase(samples)

    # ── 4. ghi ra 3 file theo bucket (giống bộ nghiệm thu) ─────────────
    # TIỀN TỐ `acceptance_` LÀ BẮT BUỘC: retrieval_kpi.py:123 chỉ quét
    # `acceptance_*.jsonl`. Đặt tên khác → Hit@10 và P@1 lặng lẽ ra 0 mẫu.
    files = {"dinh_danh": "acceptance_indep_dinh_danh.jsonl",
             "khac_phuc": "acceptance_indep_khac_phuc.jsonl",
             "tinh_huong": "acceptance_indep_tinh_huong.jsonl",
             "ngoai_le": "acceptance_indep_tinh_huong.jsonl"}   # trap gộp vào tình huống
    buckets = {}
    for s in samples:
        buckets.setdefault(files[s["bucket"]], []).append(s)
    for fname, rows in buckets.items():
        with (out_dir / fname).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        log(f"   ✓ {fname}: {len(rows)} câu")

    # ── 4b. ghi POOL nguồn — để validate_testset.py --pool kiểm grounded_cve ──
    used = {s["metadata"].get("cve_id") for s in samples if s["metadata"].get("cve_id")}
    meta_dir = out_dir / "_meta"
    meta_dir.mkdir(exist_ok=True)
    pool_path = meta_dir / "pool.jsonl"
    with pool_path.open("w", encoding="utf-8") as f:
        for r in all_recs:
            if r.get("cve_id") in used:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"   ✓ _meta/pool.jsonl: {len(used)} CVE nguồn (validate tự tìm)")

    # ── 5. manifest (truy vết nguồn gốc — yêu cầu R-EVAL-RAWLOGS) ──────
    from collections import Counter
    manifest = {
        "sinh_boi": "KPI-VA-GEN-TEST/02-GEN-TEST/gen_testset.py",
        "nguon": args.source,
        "seed": args.seed,
        "tong_so_cau": len(samples),
        "loai_tru_cve": len(exclude),
        "exclude_pool": args.exclude_pool,
        "use_llm": args.use_llm,
        "feeds": args.feeds if args.source == "public" else None,
        "file_khach_hang": args.infile if args.source == "customer" else None,
        "phan_bo_bucket": dict(Counter(s["bucket"] for s in samples)),
        "phan_bo_capability": dict(Counter(s["capability"] for s in samples)),
        "phan_bo_difficulty": dict(Counter(s["difficulty"] for s in samples)),
        "so_template": len({s["metadata"]["template_id"] for s in samples}),
        "so_cve_dung": len({s["metadata"].get("cve_id") for s in samples
                            if s["metadata"].get("cve_id")}),
    }
    (meta_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    log("")
    log(f"→ HOÀN TẤT: {len(samples)} câu → {out_dir}")
    log(f"   bucket    : {manifest['phan_bo_bucket']}")
    log(f"   capability: {manifest['phan_bo_capability']}")
    log(f"   difficulty: {manifest['phan_bo_difficulty']}")
    log("")
    log("→ BƯỚC TIẾP THEO (bắt buộc):")
    log(f"   python3 validate_testset.py {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
