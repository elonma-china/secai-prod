#!/usr/bin/env python3
"""Xuất đồ thị Neo4j đang chạy ra CSV cho `neo4j-admin database import full`, CHỈ ĐỌC, theo trang. Chỉ stdlib.

    python3 -I stack/neo4j-export.py [--node-page 20000] [--rel-page 5000] OUT_DIR

Env: NEO4J_URL (mặc định http://127.0.0.1:7474), NEO4J_USER (neo4j), NEO4J_PASSWORD (không đặt: không gửi auth),
NEO4J_DATABASE (neo4j). Gọi POST {NEO4J_URL}/db/{NEO4J_DATABASE}/tx/commit, chỉ MATCH/WITH/RETURN/SHOW/UNWIND.
OUT_DIR phải chưa có hoặc rỗng. Ghi nodes-<Label>.header.csv + .csv.gz, rels-<TYPE>.header.csv + .csv.gz,
schema.cypher và cuối cùng counts.json (chỉ khi thành công).
Thoát 0 ok; 2 dữ liệu không hỗ trợ (nhiều nhãn, kiểu thuộc tính ngoài string/integer/float/boolean hoặc NaN/Infinity
theo `valueType` kiểm trước khi ghi, tên nhãn/kiểu lạ, kiểu thuộc tính xung đột);
3 số đếm lệch (đồ thị bị ghi trong lúc xuất); 1 lỗi HTTP/khác. Không in mật khẩu.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BAD_KEY = re.compile(r'[,:"\r\n]')  # ký tự làm vỡ dòng header của neo4j-admin import
TYPES = {bool: "boolean", int: "long", float: "double", str: "string"}
VALUE_TYPES = {"STRING", "INTEGER", "FLOAT", "BOOLEAN"}  # valueType() của Cypher, bỏ hậu tố " NOT NULL"


class Fail(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


class Neo4j:
    def __init__(self) -> None:
        base = os.environ.get("NEO4J_URL", "http://127.0.0.1:7474").rstrip("/")
        self.url = f"{base}/db/{os.environ.get('NEO4J_DATABASE', 'neo4j')}/tx/commit"
        self.headers = {"Content-Type": "application/json", "Accept": "application/json;charset=UTF-8"}
        pw = os.environ.get("NEO4J_PASSWORD")
        if pw is not None:
            tok = base64.b64encode(f"{os.environ.get('NEO4J_USER', 'neo4j')}:{pw}".encode()).decode()
            self.headers["Authorization"] = f"Basic {tok}"
        # Neo4j nằm trên loopback/LAN: không đi qua http_proxy của môi trường.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def rows(self, statement: str, **params) -> list[list]:
        body = json.dumps({"statements": [{"statement": statement, "parameters": params}]}).encode()
        req = urllib.request.Request(self.url, data=body, headers=self.headers, method="POST")
        try:
            with self.opener.open(req, timeout=600) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            raise Fail(1, f"HTTP {e.code} từ {self.url}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Fail(1, f"không gọi được {self.url}: {e}") from None
        if resp.get("errors"):
            raise Fail(1, "Neo4j lỗi: " + "; ".join(f"{x.get('code')}: {x.get('message')}" for x in resp["errors"]))
        return [d["row"] for d in resp["results"][0]["data"]]


def counts(db: Neo4j) -> tuple[dict, dict]:
    nodes = {}
    for labels, n in db.rows("MATCH (n) RETURN labels(n), count(*)"):
        if len(labels) != 1 or not NAME.fullmatch(labels[0]):
            raise Fail(2, f"nhãn không hỗ trợ (cần đúng một nhãn {NAME.pattern}): {labels} x{n}")
        nodes[labels[0]] = n
    rels = {}
    for t, n in db.rows("MATCH ()-[r]->() RETURN type(r), count(*)"):
        if not NAME.fullmatch(t):
            raise Fail(2, f"kiểu quan hệ không hỗ trợ: {t!r}")
        rels[t] = n
    return nodes, rels


def check_value_types(db: Neo4j) -> None:
    """HTTP JSON biến date/point/... thành chuỗi và NaN/Infinity thành "NaN"/"Infinity": phải hỏi kiểu thật trước khi xuất."""
    for kind, pat, v in (("nút", "(n)", "n"), ("quan hệ", "()-[r]->()", "r")):
        for t, nonfinite in db.rows(f"MATCH {pat} UNWIND keys({v}) AS k WITH {v}[k] AS v, valueType({v}[k]) AS t "
                                    "RETURN DISTINCT t, t STARTS WITH 'FLOAT' AND (isNaN(v) OR abs(v) = 1.0/0.0)"):
            if t.removesuffix(" NOT NULL") not in VALUE_TYPES:
                raise Fail(2, f"thuộc tính {kind} kiểu {t} không hỗ trợ (chỉ STRING/INTEGER/FLOAT/BOOLEAN)")
            if nonfinite:
                raise Fail(2, f"thuộc tính {kind} FLOAT có NaN/Infinity: không hỗ trợ")


def cell(v) -> str:
    if v is None:
        return ""  # thiếu thuộc tính: ô rỗng không nháy
    if isinstance(v, str):
        return '"' + v.replace('"', '""') + '"'
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v)


class Out:
    """Một tệp .csv.gz cho một nhãn/kiểu: cột thuộc tính cố định trước, kiểu suy ra trong lúc ghi."""

    def __init__(self, path: Path, name: str, keys: list[str]):
        for k in keys:
            if BAD_KEY.search(k) or not k:
                raise Fail(2, f"tên thuộc tính không hỗ trợ trên {name}: {k!r}")
        self.name, self.keys, self.n = name, keys, 0
        self.types: dict[str, set] = {k: set() for k in keys}
        self.f = gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6)

    def write(self, prefix: list[str], props: dict, suffix: list[str]) -> None:
        extra = set(props) - set(self.keys)
        if extra:
            raise Fail(3, f"{self.name}: thuộc tính mới xuất hiện trong lúc xuất: {sorted(extra)}")
        for k, v in props.items():
            if type(v) not in TYPES:
                raise Fail(2, f"{self.name}.{k}: kiểu {type(v).__name__} không hỗ trợ (chỉ string/long/double/boolean)")
            self.types[k].add(TYPES[type(v)])
        self.f.write(",".join(prefix + [cell(props.get(k)) for k in self.keys] + suffix) + "\n")
        self.n += 1

    def header(self) -> list[str]:
        cols = []
        for k in self.keys:
            t = self.types[k]
            if t == {"long", "double"}:
                t = {"double"}
            if len(t) > 1:
                raise Fail(2, f"{self.name}.{k}: kiểu xung đột {sorted(t)}")
            cols.append(f"{k}:{t.pop() if t else 'string'}")
        return cols


def progress(phase: str, lo: int, top: int, state: dict) -> None:
    pct = min(100, (lo + 1) * 100 // (top + 1)) // 10 * 10
    if pct > state.get(phase, -1):
        state[phase] = pct
        print(f"neo4j-export: {phase} {pct}% (id {lo}/{top})", file=sys.stderr, flush=True)


def export(db: Neo4j, out: Path, node_page: int, rel_page: int) -> dict:
    n_before, r_before = counts(db)
    check_value_types(db)
    schema = [r[0] for r in db.rows("SHOW CONSTRAINTS YIELD createStatement")]
    schema += [r[0] for r in db.rows("SHOW INDEXES YIELD createStatement, owningConstraint, type "
                                     "WHERE owningConstraint IS NULL AND type <> 'LOOKUP'")]
    (out / "schema.cypher").write_text("".join(s.rstrip().rstrip(";") + ";\n" for s in schema), encoding="utf-8")

    nouts = {lab: Out(out / f"nodes-{lab}.csv.gz", lab, [r[0] for r in db.rows(
        f"MATCH (n:`{lab}`) UNWIND keys(n) AS k RETURN DISTINCT k")]) for lab in n_before}
    routs = {t: Out(out / f"rels-{t}.csv.gz", t, [r[0] for r in db.rows(
        f"MATCH ()-[r:`{t}`]->() UNWIND keys(r) AS k RETURN DISTINCT k")]) for t in r_before}
    for o in [*nouts.values(), *routs.values()]:
        o.keys.sort()
    top = db.rows("MATCH (n) RETURN max(id(n))")[0][0]
    top = -1 if top is None else top
    shown: dict = {}

    for lo in range(0, top + 1, node_page):
        for i, labels, props in db.rows("UNWIND range($lo,$hi) AS i MATCH (n) WHERE id(n)=i "
                                        "RETURN id(n), labels(n), properties(n)", lo=lo, hi=lo + node_page - 1):
            if len(labels) != 1:
                raise Fail(2, f"nút {i} có {len(labels)} nhãn: {labels}")
            if labels[0] not in nouts:
                raise Fail(3, f"nút {i}: nhãn {labels[0]} không có trong số đếm trước")
            nouts[labels[0]].write([str(i)], props, [labels[0]])
        progress("nodes", lo + node_page - 1, top, shown)

    for lo in range(0, top + 1, rel_page):
        for a, b, t, props in db.rows("UNWIND range($lo,$hi) AS i MATCH (a) WHERE id(a)=i MATCH (a)-[r]->(b) "
                                      "RETURN id(a), id(b), type(r), properties(r)", lo=lo, hi=lo + rel_page - 1):
            if t not in routs:
                raise Fail(3, f"quan hệ {a}->{b}: kiểu {t} không có trong số đếm trước")
            routs[t].write([str(a), str(b), t], props, [])
        progress("relationships", lo + rel_page - 1, top, shown)

    for o in [*nouts.values(), *routs.values()]:
        o.f.close()
    for lab, o in nouts.items():
        (out / f"nodes-{lab}.header.csv").write_text(",".join([":ID", *o.header(), ":LABEL"]) + "\n", encoding="utf-8")
    for t, o in routs.items():
        (out / f"rels-{t}.header.csv").write_text(",".join([":START_ID", ":END_ID", ":TYPE", *o.header()]) + "\n", encoding="utf-8")

    n_after, r_after = counts(db)
    diff = []
    for kind, before, after, outs in (("nodes", n_before, n_after, nouts), ("relationships", r_before, r_after, routs)):
        for k in sorted(set(before) | set(after)):
            b, a, e = before.get(k, 0), after.get(k, 0), outs[k].n if k in outs else 0
            if not b == a == e:
                diff.append(f"{kind} {k}: trước={b} sau={a} đã xuất={e}")
    if diff:
        raise Fail(3, "số đếm lệch (đồ thị bị ghi trong lúc xuất?):\n  " + "\n  ".join(diff))
    return {"nodes": {k: o.n for k, o in nouts.items()}, "relationships": {k: o.n for k, o in routs.items()},
            "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--node-page", type=int, default=20000)
    ap.add_argument("--rel-page", type=int, default=5000)
    ap.add_argument("out_dir", type=Path)
    a = ap.parse_args()
    if a.node_page < 1 or a.rel_page < 1:
        ap.error("--node-page/--rel-page phải >= 1")
    out = a.out_dir
    try:
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise Fail(1, f"{out} đã tồn tại và không rỗng; không ghi đè")
        out.mkdir(parents=True, exist_ok=True)
        result = export(Neo4j(), out, a.node_page, a.rel_page)
    except Fail as e:
        print(f"neo4j-export: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"neo4j-export: {e}", file=sys.stderr)
        return 1
    tmp = out / "counts.json.tmp"
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.rename(out / "counts.json")
    print(f"neo4j-export: xong {sum(result['nodes'].values())} nút, {sum(result['relationships'].values())} quan hệ -> {out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
