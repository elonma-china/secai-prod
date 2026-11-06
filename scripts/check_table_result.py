#!/usr/bin/env python3
"""Automatic checker for TABLE-translation job results (criteria: module-map/translate/08-tieu-chi-dich-bang.txt).

Usage:
  python3 scripts/check_table_result.py --data module-map/translate/04c-docx-bang-20trang-data.json \
      --sample L1 --result result.json [--job job.json] [--txt out.txt] [--json-out report.json] \
      [--review review.json] [--elapsed SECONDS]
  python3 scripts/check_table_result.py --data ... --selftest [--sample L1]

Exit code: 0 = DAT / DAT CO LUU Y, 1 = KHONG DAT (Vietnamese verdict labels are printed with diacritics).
Stdlib only. Reads local files only; never contacts any service.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime

OK, LUU_Y, KHONG = "ĐẠT", "ĐẠT CÓ LƯU Ý", "KHÔNG ĐẠT"
TRACKED_WARNINGS = {"NUMERIC_MISMATCH", "OUTPUT_UNBALANCED_BRACKETS"}
GOLD_THRESHOLD = 0.95
FUZZY_RATIO = 0.80
MARKERS = [r"⟦", r"⟧", r"\[REDACTED[^\]]*\]", r"\[L\d+\]", r"<L\d+>", r"\*\*\*", r"(?i)xxx", r"\[đã ẩn\]", r"\[ẩn thông tin\]"]
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
VI_RE = re.compile("[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.I)
SPEED_GOOD, SPEED_SLOW = 4.0, 6.0  # informational seconds per block


# ----------------------------------------------------------------------------- helpers
def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def norm(s: str) -> str:
    s = nfc(s).replace("\r\n", "\n").casefold()
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)           # decimal comma == decimal point
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" .。:;,!")


def digits_of(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s)
    return [re.sub(r"[.,]", "", t) for t in NUM_RE.findall(s)]


def strip_preserved(text: str, preserved: list[str]) -> str:
    for p in sorted(preserved, key=len, reverse=True):
        text = text.replace(p, " ")
    return text


def first(items, n=10):
    return items[:n]


class Crit:
    def __init__(self, cid, name, hard=True):
        self.cid, self.name, self.hard = cid, name, hard
        self.ok = True
        self.soft = False       # passed but with a caveat
        self.detail = ""
        self.items: list[str] = []
        self.info = False

    def fail(self, detail, items=()):
        self.ok, self.detail, self.items = False, detail, list(items)
        return self

    def passed(self, detail=""):
        self.detail = detail
        return self

    def caveat(self, detail, items=()):
        self.soft, self.detail, self.items = True, detail, list(items)
        return self

    def to_dict(self):
        return {"id": self.cid, "name": self.name, "hard": self.hard, "pass": self.ok, "caveat": self.soft,
                "detail": self.detail, "offenders": first(self.items)}


# ----------------------------------------------------------------------------- loading
def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def get_sample(data, sid):
    for s in data["samples"]:
        if s["id"] == sid:
            return s
    raise SystemExit(f"sample {sid!r} not in data file (have: {[s['id'] for s in data['samples']]})")


def unwrap_result(obj):
    if isinstance(obj, dict) and "blocks" not in obj and isinstance(obj.get("result"), dict):
        return obj["result"]
    return obj


def parse_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ----------------------------------------------------------------------------- core check
def check(sample, result, job=None, txt=None, review=None, elapsed=None):
    review = review or {}
    crits: list[Crit] = []
    blocks = result.get("blocks") if isinstance(result, dict) else None
    seq = sample["block_sequence"]
    cells = sample["cells"]
    paras = sample["paragraphs"]
    stats = sample["stats"]

    # ---- A. JSON contract -------------------------------------------------------------
    c = Crit("A1", "JSON có translated_text, blocks[], warnings và đủ trường mỗi khối")
    bad = []
    if not isinstance(blocks, list):
        crits.append(c.fail("không có blocks[] trong result")); blocks = []
    else:
        if not isinstance(result.get("translated_text"), str):
            bad.append("thiếu translated_text (chuỗi)")
        if not isinstance(result.get("warnings", []), list):
            bad.append("warnings không phải danh sách")
        for i, b in enumerate(blocks):
            for f in ("id", "kind", "order", "source_text", "translated_text", "metadata"):
                if not isinstance(b, dict) or f not in b:
                    bad.append(f"blocks[{i}] thiếu {f}")
            if isinstance(b, dict) and b.get("kind") == "table_cell":
                md = b.get("metadata") or {}
                for f in ("section", "table", "row", "column"):
                    if not isinstance(md.get(f), int):
                        bad.append(f"blocks[{i}] ({b.get('id')}) metadata thiếu {f}")
        if isinstance(result.get("translated_text"), str) and not bad:
            joined = "\n".join(b["translated_text"] for b in blocks)
            if joined != result["translated_text"]:
                bad.append("translated_text khác phép nối \\n các translated_text của blocks")
        crits.append(c.fail(f"{len(bad)} lỗi hợp đồng", bad) if bad else c.passed(f"{len(blocks)} khối, đủ trường"))
    blocks = [b for b in blocks if isinstance(b, dict)]

    # ---- B. Structure -----------------------------------------------------------------
    src_ids = [x[0] for x in seq]
    src_kind = {x[0]: x[1] for x in seq}
    cnt = Counter(b.get("id") for b in blocks)
    dropped = [i for i in src_ids if cnt.get(i, 0) == 0]
    duplicated = [i for i, n in cnt.items() if n > 1]
    extra = [i for i in cnt if i not in src_kind]

    c = Crit("B1", f"Số khối kết quả = số khối nguồn ({stats['blocks']})")
    crits.append(c.passed(f"{len(blocks)}/{stats['blocks']}") if len(blocks) == stats["blocks"]
                 else c.fail(f"kết quả {len(blocks)} ≠ nguồn {stats['blocks']}"))

    cell_by_id = {x["block_id"]: x for x in cells}
    key_of = lambda x: (x["table"], x["row"], x["column"])
    src_keys = {key_of(x): x["block_id"] for x in cells}
    res_cells = [b for b in blocks if b.get("kind") == "table_cell"]
    kc = Counter(((b.get("metadata") or {}).get("table"), (b.get("metadata") or {}).get("row"),
                  (b.get("metadata") or {}).get("column")) for b in res_cells)
    miss_k = [k for k in src_keys if kc.get(k, 0) == 0]
    dup_k = [k for k, n in kc.items() if n > 1]
    extra_k = [k for k in kc if k not in src_keys]
    c = Crit("B2", "Mỗi ô nguồn có đúng 1 khối kết quả cùng (table,row,column); không mất/thừa/nhân đôi")
    probs = [f"mất ô {k}" for k in miss_k] + [f"nhân đôi ô {k}" for k in dup_k] + [f"thừa ô {k}" for k in extra_k]
    crits.append(c.fail(f"mất {len(miss_k)}, nhân đôi {len(dup_k)}, thừa {len(extra_k)} (ô)", probs) if probs
                 else c.passed(f"{len(src_keys)}/{len(src_keys)} ô khớp 1-1"))

    c = Crit("B3", "Không mất / nhân đôi / thừa khối (theo id, cả đoạn văn)")
    probs = [f"mất {i}" for i in dropped] + [f"nhân đôi {i} (x{cnt[i]})" for i in duplicated] + [f"thừa {i}" for i in extra]
    crits.append(c.fail(f"mất {len(dropped)}, nhân đôi {len(duplicated)}, thừa {len(extra)} (khối)", probs) if probs
                 else c.passed("0 mất, 0 nhân đôi, 0 thừa"))

    c = Crit("B4", "Ô gộp (gridSpan/vMerge) chỉ 1 khối tại ô đầu; không khối ở vị trí ô bị gộp")
    merged = [x for x in cells if x["kind_of_cell"] == "merged" or "column_span" in x or "row_span" in x]
    probs = []
    for x in merged:
        k = key_of(x)
        if kc.get(k, 0) != 1:
            probs.append(f"{x['block_id']} {k}: {kc.get(k, 0)} khối")
    probs += [f"khối thừa tại {k} (ô bị gộp?)" for k in extra_k]
    crits.append(c.fail(f"{len(probs)} sai", probs) if probs else c.passed(f"{len(merged)} ô gộp đều 1 khối"))

    c = Crit("B5", "Bảng lồng và số bảng được giữ nguyên")
    want_tables = {x["table"] for x in cells}
    got_tables = {(b.get("metadata") or {}).get("table") for b in res_cells}
    nested = [x for x in cells if x["kind_of_cell"] == "nested"]
    nested_missing = [x["block_id"] for x in nested if kc.get(key_of(x), 0) != 1]
    probs = []
    if want_tables != got_tables:
        probs.append(f"bảng thiếu {sorted(want_tables - got_tables)} thừa {sorted(t for t in got_tables - want_tables if t is not None)}")
    probs += [f"ô lồng {i} mất/nhân đôi" for i in nested_missing]
    crits.append(c.fail(f"{len(probs)} sai", probs) if probs
                 else c.passed(f"{len(got_tables)}/{len(want_tables)} bảng, {len(nested)} ô thuộc bảng lồng/ô chứa bảng lồng"))

    c = Crit("B6", "Thứ tự khối giữ nguyên (id theo thứ tự nguồn, order tăng dần khớp nguồn)")
    got_seq = [b.get("id") for b in blocks if b.get("id") in src_kind]
    exp_seq = [i for i in src_ids if i in set(got_seq)]
    probs = []
    if got_seq != exp_seq:
        j = next((n for n, (a, b) in enumerate(zip(got_seq, exp_seq)) if a != b), 0)
        probs.append(f"lệch thứ tự từ vị trí {j}: kết quả {got_seq[j:j+3]} ≠ nguồn {exp_seq[j:j+3]}")
    pos = {i: n for n, i in enumerate(src_ids)}
    bad_order = [b.get("id") for b in blocks if b.get("id") in pos and b.get("order") != pos[b["id"]]]
    probs += [f"order sai ở {i}" for i in bad_order]
    kind_bad = [b.get("id") for b in blocks if b.get("id") in src_kind and b.get("kind") != src_kind[b["id"]]]
    probs += [f"kind sai ở {i}" for i in kind_bad]
    crits.append(c.fail(f"{len(probs)} sai", probs) if probs else c.passed("thứ tự, order, kind khớp nguồn"))

    c = Crit("B7", "source_text trong kết quả không bị sửa (ô bảng) và metadata table/row/column khớp")
    probs = []
    for b in res_cells:
        x = cell_by_id.get(b.get("id"))
        if x is None:
            continue
        if (b.get("source_text") or "").replace("\r\n", "\n") != x["source_text"]:
            probs.append(f"{x['block_id']}: source_text khác")
        md = b.get("metadata") or {}
        if key_of(x) != (md.get("table"), md.get("row"), md.get("column")):
            probs.append(f"{x['block_id']}: metadata {md.get('table')},{md.get('row')},{md.get('column')} ≠ {key_of(x)}")
    crits.append(c.fail(f"{len(probs)} sai", probs) if probs else c.passed("khớp"))

    c = Crit("B8", "Đầu ra TXT = mỗi khối một dòng theo thứ tự (ô nhiều đoạn chiếm nhiều dòng)", hard=txt is not None)
    if txt is None:
        c.info = True; crits.append(c.passed("bỏ qua (không có --txt)"))
    else:
        want = "\n".join(b.get("translated_text", "") for b in blocks)
        a, b_ = txt.replace("\r\n", "\n").rstrip("\n"), want.rstrip("\n")
        if a == b_:
            crits.append(c.passed(f"{len(a.split(chr(10)))} dòng khớp translated_text của JSON"))
        else:
            la, lb = a.split("\n"), b_.split("\n")
            j = next((n for n, (p, q) in enumerate(zip(la, lb)) if p != q), min(len(la), len(lb)))
            crits.append(c.fail(f"TXT {len(la)} dòng ≠ JSON {len(lb)} dòng; lệch từ dòng {j + 1}", [f"TXT: {la[j][:80] if j < len(la) else '(hết)'}", f"JSON: {lb[j][:80] if j < len(lb) else '(hết)'}"]))

    # ---- C. Content per cell ----------------------------------------------------------
    by_id = {}
    for b in blocks:
        by_id.setdefault(b.get("id"), b)
    tr = lambda i: (by_id.get(i, {}).get("translated_text") if by_id.get(i) else None)

    empty, untrans, wrong_lang, lost_lit, lost_num = [], [], [], [], []
    lit_total = lit_ok = num_total = num_ok = 0
    exact = variant = fuzzy = rejected = 0
    gold_total = 0
    rej_items, fuzzy_items = [], []
    per_kind: dict[str, list[int]] = {}
    lang_is_ja = sample["source_lang"] == "ja"
    for x in cells:
        t = tr(x["block_id"])
        if t is None:
            continue                                     # dropped: reported in B
        src = x["source_text"]
        if src.strip() and not t.strip():
            empty.append(f"{x['block_id']} {key_of(x)} nguồn={src.strip()[:30]!r}")
            t = ""
        gold = x.get("gold_vi")
        # must_preserve (exact substring)
        for lit in x.get("must_preserve", []):
            lit_total += 1
            if lit in t:
                lit_ok += 1
            else:
                lost_lit.append(f"{x['block_id']} thiếu {lit[:40]!r}")
        # numbers (separator-normalised multiset)
        want_n = Counter(digits_of(" ".join(x.get("numbers", []))))
        got_n = Counter(digits_of(t))
        for n, k in want_n.items():
            num_total += k
            num_ok += min(k, got_n.get(n, 0))
            if got_n.get(n, 0) < k:
                lost_num.append(f"{x['block_id']} thiếu số {n!r} (có {sorted(got_n)[:6]})")
        # untranslated / language
        needs_tr = bool(gold) and norm(gold) != norm(src) or (not gold and x["kind_of_cell"] in ("label", "sentence", "multiline"))
        if needs_tr and t.strip() and norm(t) == norm(src):
            untrans.append(f"{x['block_id']} giữ nguyên {src.strip()[:30]!r}")
        if lang_is_ja and t.strip() and needs_tr:
            if CJK_RE.search(strip_preserved(t, x.get("must_preserve", []))):
                wrong_lang.append(f"{x['block_id']} còn chữ Nhật: {t.strip()[:30]!r}")
        elif (not lang_is_ja) and x["kind_of_cell"] in ("sentence", "multiline") and len(src.split()) >= 5 and t.strip():
            if not VI_RE.search(t):
                wrong_lang.append(f"{x['block_id']} không có dấu tiếng Việt: {t.strip()[:30]!r}")
        # gold
        if gold:
            gold_total += 1
            nt = norm(t)
            how = None
            if nt == norm(gold):
                how = "exact"
            elif any(nt == norm(v) for v in x.get("accepted_variants", [])):
                how = "variant"
            elif x["kind_of_cell"] in ("sentence", "multiline", "nested") and "\n" in gold or x["kind_of_cell"] == "sentence":
                if difflib.SequenceMatcher(None, nt, norm(gold)).ratio() >= FUZZY_RATIO:
                    how = "fuzzy"
            if how is None and review.get(x["block_id"]) in (True, "ok", "OK", "pass"):
                how = "review"
            pk = per_kind.setdefault(x["kind_of_cell"], [0, 0])
            pk[1] += 1
            if how:
                pk[0] += 1
            if how == "exact":
                exact += 1
            elif how == "variant":
                variant += 1
            elif how in ("fuzzy", "review"):
                fuzzy += 1
                fuzzy_items.append(f"{x['block_id']} ({how}) {t.strip()[:50]!r}")
            else:
                rejected += 1
                rej_items.append(f"{x['block_id']} {key_of(x)}: nguồn {src.strip()[:25]!r} → {t.strip()[:40]!r} (gold {gold[:40]!r})")
    accepted = exact + variant + fuzzy

    c = Crit("C1", "Không ô nào kết quả rỗng (khi nguồn có chữ)")
    crits.append(c.fail(f"{len(empty)} ô rỗng", empty) if empty else c.passed("0 ô rỗng"))
    c = Crit("C2", "Không ô nào bị bỏ nguyên tiếng nguồn (trừ ô chỉ gồm literal/số)")
    crits.append(c.fail(f"{len(untrans)} ô chưa dịch", untrans) if untrans else c.passed("0 ô chưa dịch"))
    c = Crit("C3", "Ngôn ngữ đích đúng (tiếng Việt; không còn chữ Nhật trong ô đã dịch)")
    crits.append(c.fail(f"{len(wrong_lang)} ô sai ngôn ngữ", wrong_lang) if wrong_lang else c.passed("0 ô sai ngôn ngữ"))
    rate = accepted / gold_total if gold_total else 1.0
    c = Crit("C4", f"Khớp gold_vi (exact-normalised / accepted_variants / sentence-fuzzy≥{FUZZY_RATIO}) ≥ {GOLD_THRESHOLD:.0%}")
    detail = (f"{accepted}/{gold_total} = {rate:.1%} (exact {exact}, variant {variant}, fuzzy/review {fuzzy}, loại {rejected}); "
              + ", ".join(f"{k} {a}/{n}" for k, (a, n) in sorted(per_kind.items())))
    if rate < GOLD_THRESHOLD:
        crits.append(c.fail(detail, rej_items))
    elif rejected:
        crits.append(c.caveat(detail, rej_items))
    else:
        crits.append(c.passed(detail))
    crits[-1].info_fuzzy = fuzzy_items  # type: ignore[attr-defined]
    c = Crit("C5", "must_preserve (ô) còn nguyên văn 100%")
    crits.append(c.fail(f"{lit_ok}/{lit_total} giữ; mất {len(lost_lit)}", lost_lit) if lost_lit else c.passed(f"{lit_ok}/{lit_total}"))
    c = Crit("C6", "Số trong ô còn đủ 100% (chuẩn hoá dấu phân cách . ,)")
    crits.append(c.fail(f"{num_ok}/{num_total} giữ; mất {len(lost_num)}", lost_num) if lost_num else c.passed(f"{num_ok}/{num_total}"))

    # paragraphs: preserve + empty + untranslated
    p_lit_total = p_lit_ok = 0
    p_empty, p_untr, p_lost = [], [], []
    for p in paras:
        t = tr(p["block_id"])
        if t is None:
            continue
        if not t.strip():
            p_empty.append(p["block_id"]); continue
        for lit in p.get("must_preserve", []):
            p_lit_total += 1
            if lit in t:
                p_lit_ok += 1
            else:
                p_lost.append(f"{p['block_id']} thiếu {lit[:40]!r}")
        if len(p["source_text"]) >= 40 and norm(t) == norm(p["source_text"]):
            p_untr.append(p["block_id"])
        if lang_is_ja and len(p["source_text"]) >= 40 and CJK_RE.search(strip_preserved(t, p.get("must_preserve", []))):
            p_untr.append(p["block_id"] + " (còn chữ Nhật)")
    c = Crit("C7", "Đoạn văn: không rỗng, không để nguyên tiếng nguồn, literal giữ 100%")
    probs = [f"rỗng {i}" for i in p_empty] + [f"chưa dịch {i}" for i in p_untr] + p_lost
    crits.append(c.fail(f"{len(p_empty)} rỗng, {len(p_untr)} chưa dịch, literal {p_lit_ok}/{p_lit_total}", probs)
                 if probs else c.passed(f"{len(paras)} đoạn; literal {p_lit_ok}/{p_lit_total}"))

    # markers
    mk = []
    for b in blocks:
        for pat in MARKERS:
            m = re.search(pat, b.get("translated_text", "") or "")
            if m and not re.search(pat, b.get("source_text", "") or ""):
                mk.append(f"{b.get('id')}: {m.group(0)!r}")
    for pat in MARKERS:
        m = re.search(pat, result.get("translated_text", "") or "") if isinstance(result, dict) else None
        if m and not any(re.search(pat, p["source_text"]) for p in paras):
            mk.append(f"translated_text: {m.group(0)!r}")
    c = Crit("C8", "0 dấu che / ký hiệu nội bộ (⟦ ⟧ [REDACTED] [L0] <L1> *** xxx)")
    crits.append(c.fail(f"{len(mk)} dấu che", mk) if mk else c.passed("0"))

    # ---- D. Document level ------------------------------------------------------------
    job = job or {}
    status = job.get("status") or (result.get("status") if isinstance(result, dict) else None)
    err = job.get("error")
    c = Crit("D1", "Job succeeded, không có lỗi trên job")
    probs = []
    if status != "succeeded":
        probs.append(f"status={status!r}")
    if err:
        probs.append(f"error={err}")
    crits.append(c.fail("; ".join(probs), probs) if probs else c.passed("status=succeeded, không error"))

    wl = []
    for src_w in (result.get("warnings") if isinstance(result, dict) else None) or [], job.get("warnings") or []:
        for w in src_w:
            code = w.get("code") if isinstance(w, dict) else str(w)
            msg = w.get("message", "") if isinstance(w, dict) else ""
            if (code, msg) not in wl:
                wl.append((code, msg))
    expected = set(sample.get("warnings_expected", []))
    tracked = [(c_, m) for c_, m in wl if c_ in TRACKED_WARNINGS]
    unexpected = [(c_, m) for c_, m in wl if c_ not in TRACKED_WARNINGS and c_ not in expected]
    c = Crit("D2", "Cảnh báo: chỉ UNSUPPORTED_PART/OCR khi có thật; NUMERIC_MISMATCH/OUTPUT_UNBALANCED_BRACKETS được đếm")
    cc = Counter(c_ for c_, _ in wl)
    if unexpected:
        crits.append(c.fail(f"cảnh báo ngoài tập cho phép: {dict(Counter(c_ for c_, _ in unexpected))}", [f"{a}: {b}" for a, b in unexpected]))
    elif tracked:
        crits.append(c.caveat(f"có cảnh báo cần báo cáo: {dict(cc)}", [f"{a}: {b}" for a, b in tracked]))
    else:
        crits.append(c.passed(f"{len(wl)} cảnh báo ({dict(cc) or 'không có'})"))

    c = Crit("D3", "Tốc độ (tham khảo, không ảnh hưởng kết luận)", hard=False)
    el = elapsed
    if el is None:
        t0, t1 = parse_ts(job.get("created_at")), parse_ts(job.get("updated_at"))
        if t0 is not None and t1 is not None:
            el = t1 - t0
        elif isinstance(job.get("elapsed_seconds"), (int, float)):
            el = float(job["elapsed_seconds"])
    if el is None:
        c.info = True; crits.append(c.passed("không có thời gian (dùng --job có created_at/updated_at hoặc --elapsed)"))
    else:
        spb = el / max(1, stats["blocks"])
        tag = "tốt" if spb <= SPEED_GOOD else ("chậm" if spb > SPEED_SLOW else "chấp nhận được")
        c.info = True
        crits.append(c.passed(f"{el:.0f} s / {stats['blocks']} khối = {spb:.2f} s/khối ({tag}; mốc tham khảo ≤{SPEED_GOOD:g} tốt, >{SPEED_SLOW:g} chậm; planned_chunks={stats['planned_chunks']})"))

    # ---- verdict ----------------------------------------------------------------------
    hard_fail = [c for c in crits if c.hard and not c.ok]
    soft = [c for c in crits if c.ok and c.soft]
    verdict = KHONG if hard_fail else (LUU_Y if soft else OK)
    return verdict, crits, {"gold_total": gold_total, "gold_accepted": accepted, "gold_rate": rate, "fuzzy": fuzzy_items,
                            "tracked_warnings": len(tracked), "elapsed": el}


def render(sample, verdict, crits, extra, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    s = sample["stats"]
    p(f"== {sample['id']} {sample['source_lang']}->vi | khối {s['blocks']} (ô {s['table_cell_blocks']}, đoạn {s['paragraph_blocks']}) | bảng {s['tables']} | planned_chunks {s['planned_chunks']} ==")
    for c in crits:
        tag = "INFO" if c.info else ("PASS" if c.ok and not c.soft else ("WARN" if c.ok else "FAIL"))
        p(f"[{tag}] {c.cid} {c.name}\n        {c.detail}")
        if c.items and tag in ("FAIL", "WARN"):
            for it in first(c.items):
                p(f"          - {it}")
            if len(c.items) > 10:
                p(f"          ... +{len(c.items) - 10} nữa")
    if extra["fuzzy"]:
        p(f"[INFO] {len(extra['fuzzy'])} ô sentence được nhận theo độ giống (fuzzy) — nên xem lại bằng mắt; ví dụ: {extra['fuzzy'][0]}")
    p(f"KẾT LUẬN {sample['id']}: {verdict}")


# ----------------------------------------------------------------------------- self-test
def fake_perfect(sample):
    cells = {x["block_id"]: x for x in sample["cells"]}
    paras = {x["block_id"]: x for x in sample["paragraphs"]}
    blocks = []
    for n, (bid, kind) in enumerate(sample["block_sequence"]):
        if kind == "table_cell":
            x = cells[bid]
            t = x["gold_vi"] if x.get("gold_vi") else x["source_text"]
            src = x["source_text"]
            md = {"section": 0, "table": x["table"], "row": x["row"], "column": x["column"]}
            if "column_span" in x:
                md["column_span"] = x["column_span"]
            if "row_span" in x:
                md["row_span"] = x["row_span"]
        else:
            x = paras.get(bid)
            src = x["source_text"] if x else ""
            t = ("Bản dịch đoạn văn: " + " ".join(x["must_preserve"])) if x else ""
            md = {"section": 0}
        blocks.append({"id": bid, "kind": kind, "order": n, "source_text": src, "translated_text": t, "metadata": md})
    res = {"job_id": "selftest", "status": "succeeded", "translated_text": "\n".join(b["translated_text"] for b in blocks),
           "blocks": blocks, "warnings": []}
    job = {"status": "succeeded", "error": None, "warnings": [], "created_at": "2026-10-10T00:00:00Z", "updated_at": "2026-10-10T00:20:00Z"}
    return res, job


def resync(res):
    res["translated_text"] = "\n".join(b["translated_text"] for b in res["blocks"])
    return res


def selftest(data, sid=None):
    ok_all = True
    for sample in data["samples"]:
        if sid and sample["id"] != sid:
            continue
        print(f"\n##### SELFTEST {sample['id']} #####")
        res, job = fake_perfect(sample)
        txt = res["translated_text"] + "\n"
        verdict, crits, _ = check(sample, res, job, txt)
        good = verdict == OK
        print(f"perfect result -> {verdict}: {'OK' if good else 'SAI (mong đợi ĐẠT)'}")
        if not good:
            render(sample, verdict, crits, _)
        ok_all &= good
        cells = sample["cells"]
        idx_of = {b["id"]: i for i, b in enumerate(res["blocks"])}
        pick = lambda pred: next(x for x in cells if pred(x))
        defects = []

        def d_drop():
            r = copy.deepcopy(res); x = pick(lambda x: x["kind_of_cell"] == "label" and x["gold_vi"])
            del r["blocks"][idx_of[x["block_id"]]]; return resync(r), {"B1", "B2", "B3"}
        def d_dup():
            r = copy.deepcopy(res); x = pick(lambda x: x["kind_of_cell"] == "label" and x["gold_vi"])
            i = idx_of[x["block_id"]]; r["blocks"].insert(i + 1, copy.deepcopy(r["blocks"][i])); return resync(r), {"B1", "B2", "B3"}
        def d_empty():
            r = copy.deepcopy(res); x = pick(lambda x: x["kind_of_cell"] == "label" and x["gold_vi"])
            r["blocks"][idx_of[x["block_id"]]]["translated_text"] = ""; return resync(r), {"C1"}
        def d_lit():
            r = copy.deepcopy(res); x = pick(lambda x: x["kind_of_cell"] == "literal" and any(m.startswith("CVE-") for m in x["must_preserve"]))
            b = r["blocks"][idx_of[x["block_id"]]]; b["translated_text"] = b["translated_text"].replace("CVE", "cve"); return resync(r), {"C5"}
        def d_num():
            r = copy.deepcopy(res); x = pick(lambda x: x["kind_of_cell"] == "number" and not x["must_preserve"] and x["numbers"] and "." in x["numbers"][0])
            b = r["blocks"][idx_of[x["block_id"]]]; b["translated_text"] = re.sub(r"\d(?!.*\d)", lambda m: str((int(m.group(0)) + 1) % 10), b["translated_text"]); return resync(r), {"C6"}
        for name, fn in (("drop a cell", d_drop), ("duplicate a cell", d_dup), ("empty a cell", d_empty),
                         ("alter a preserved literal", d_lit), ("change a number", d_num)):
            r, want = fn()
            v, cr, ex = check(sample, r, job)
            failed = {c.cid for c in cr if c.hard and not c.ok}
            hit = v == KHONG and want <= failed
            print(f"defect '{name}': verdict {v}; criteria failed {sorted(failed)}; expected ⊇ {sorted(want)} -> {'DETECTED' if hit else 'MISSED'}")
            ok_all &= hit
    print("\nSELFTEST:", "PASS" if ok_all else "FAIL")
    return ok_all


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--sample")
    ap.add_argument("--result")
    ap.add_argument("--job")
    ap.add_argument("--txt")
    ap.add_argument("--json-out")
    ap.add_argument("--review", help="JSON {block_id: 'ok'} — cells a human accepted despite no gold match")
    ap.add_argument("--elapsed", type=float, help="job seconds (overrides job created_at/updated_at)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    data = load_json(a.data)
    if a.selftest:
        return 0 if selftest(data, a.sample) else 1
    if not (a.sample and a.result):
        ap.error("--sample and --result are required (or use --selftest)")
    sample = get_sample(data, a.sample)
    result = unwrap_result(load_json(a.result))
    job = load_json(a.job) if a.job else None
    txt = open(a.txt, encoding="utf-8").read() if a.txt else None
    review = load_json(a.review) if a.review else None
    verdict, crits, extra = check(sample, result, job, txt, review, a.elapsed)
    render(sample, verdict, crits, extra)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump({"sample": a.sample, "verdict": verdict, "criteria": [c.to_dict() for c in crits],
                       "summary": {k: v for k, v in extra.items() if k != "fuzzy"}, "fuzzy_cells": extra["fuzzy"]}, fh, ensure_ascii=False, indent=1)
    return 0 if verdict in (OK, LUU_Y) else 1


if __name__ == "__main__":
    sys.exit(main())
