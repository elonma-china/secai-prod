#!/usr/bin/env python3
"""bm25_py.py — Đọc BM25 CSC artifacts bằng PURE PYTHON (không numpy) — chạy trên VPS.

Port từ packages/opencode/src/tool/bm25.ts (đã verify score-identical với bm25s).
CSC: cột = vocab token, hàng = doc. Search = column-sum precomputed scores.
Chỉ seek-đọc cột của token trong query (vài cột/truy vấn) → không nạp 95MB vào RAM.

Artifacts ($DATA_ROOT/knowledge_base/bm25_cve_kb/):
  data.csc.index.npy  float32 · indices.csc.index.npy int32 · indptr.csc.index.npy int64
  vocab.index.json {token:col} · doc_ids.json [point_id]
"""
import array
import json
import os
import re
import struct
from pathlib import Path
from threading import local as thread_local

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if", "in", "into", "is", "it",
    "no", "not", "of", "on", "or", "such", "that", "the", "their", "then", "there", "these", "they",
    "this", "to", "was", "will", "with",
}
TOKEN_RE = re.compile(r"\b\w\w+\b")


def _npy_data_offset(path):
    """Trả (data_start_byte, dtype_descr) của file .npy little-endian."""
    with open(path, "rb") as f:
        magic = f.read(6)
        assert magic == b"\x93NUMPY", f"not npy: {path}"
        major = f.read(1)[0]
        f.read(1)  # minor
        if major >= 2:
            hlen = struct.unpack("<I", f.read(4))[0]
            data_start = 12 + hlen
        else:
            hlen = struct.unpack("<H", f.read(2))[0]
            data_start = 10 + hlen
        header = f.read(hlen).decode("latin1")
    descr = re.search(r"'descr':\s*'([^']+)'", header).group(1)
    return data_start, descr


class Bm25Index:
    def __init__(self, dir_path):
        self.dir = Path(dir_path)
        self.vocab = json.loads((self.dir / "vocab.index.json").read_text())
        self.doc_ids = json.loads((self.dir / "doc_ids.json").read_text())
        # indptr (int64) nạp toàn bộ — nhỏ
        off, descr = _npy_data_offset(self.dir / "indptr.csc.index.npy")
        with open(self.dir / "indptr.csc.index.npy", "rb") as f:
            f.seek(off)
            buf = f.read()
        self.indptr = array.array("q")
        self.indptr.frombytes(buf[: (len(buf) // 8) * 8])
        # data (float32) + indices (int32): mở file, giữ offset để seek
        self.data_off, _ = _npy_data_offset(self.dir / "data.csc.index.npy")
        self.idx_off, _ = _npy_data_offset(self.dir / "indices.csc.index.npy")
        self.data_path = self.dir / "data.csc.index.npy"
        self.idx_path = self.dir / "indices.csc.index.npy"
        # seek()+read() trên 1 file handle DÙNG CHUNG không nguyên tử — nhiều luồng
        # search() đồng thời (vd retrieval_kpi.py --workers) có thể chen seek vào giữa
        # seek/read của nhau → đọc lệch offset → IndexError. Từng thử khoá toàn cục
        # quanh seek+read nhưng làm nghẽn nặng (mọi token của mọi query xếp hàng chờ
        # 1 khoá). Fix đúng: MỖI THREAD mở file handle RIÊNG (fd độc lập, seek position
        # không chia sẻ giữa các fd trỏ cùng 1 file) — không cần khoá, chạy song song thật.
        self._local = thread_local()

    def _handles(self):
        h = getattr(self._local, "h", None)
        if h is None:
            h = (open(self.data_path, "rb"), open(self.idx_path, "rb"))
            self._local.h = h
        return h

    def _col(self, token_id):
        s = self.indptr[token_id]
        e = self.indptr[token_id + 1]
        if e <= s:
            return [], []
        n = e - s
        data_f, idx_f = self._handles()
        data_f.seek(self.data_off + s * 4)
        vals = array.array("f")
        vals.frombytes(data_f.read(n * 4))
        idx_f.seek(self.idx_off + s * 4)
        rows = array.array("i")
        rows.frombytes(idx_f.read(n * 4))
        return rows, vals

    def tokenize(self, query):
        ids = []
        for tok in TOKEN_RE.findall(query.lower()):
            if tok in STOPWORDS:
                continue
            j = self.vocab.get(tok)
            if j is not None:
                ids.append(j)
        return ids

    def search(self, query, top_k):
        ids = self.tokenize(query)
        if not ids:
            return []
        scores = {}
        for tid in ids:
            rows, vals = self._col(tid)
            for k in range(len(rows)):
                d = rows[k]
                scores[d] = scores.get(d, 0.0) + vals[k]
        ranked = [(self.doc_ids[d], sc) for d, sc in scores.items() if sc > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


def find_bm25_dir():
    cands = []
    if os.environ.get("BM25_DIR"):
        cands.append(os.environ["BM25_DIR"])
    if os.environ.get("DATA_ROOT"):
        cands.append(os.path.join(os.environ["DATA_ROOT"], "knowledge_base", "bm25_cve_kb"))
    cands += ["/opt/secai/data/knowledge_base/bm25_cve_kb", "/data/knowledge_base/bm25_cve_kb"]
    for c in cands:
        if Path(c, "vocab.index.json").exists():
            return c
    return None


if __name__ == "__main__":
    d = find_bm25_dir()
    print("bm25 dir:", d)
    if d:
        idx = Bm25Index(d)
        print("vocab size:", len(idx.vocab), "docs:", len(idx.doc_ids))
        for q in ["CVE-2024-3272 hard-coded credentials D-Link", "sql injection"]:
            hits = idx.search(q, 5)
            print(f"  '{q[:40]}' -> {[(h[0][:12], round(h[1],1)) for h in hits]}")
