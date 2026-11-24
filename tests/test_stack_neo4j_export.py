"""stack/neo4j-export.py voi Neo4j GIA: http.server tra loi /db/<db>/tx/commit theo chuoi con cua statement.

Tai dung (Task 4): `from test_stack_neo4j_export import fake_neo4j, sample_graph, run_export`.
`fake_neo4j(graph)` la context manager tra ve (url, state); state["reqs"] ghi (Authorization, [statement...]).
Graph: {"nodes": {id: (labels, props)}, "rels": [(start, end, type, props)], "constraints": [createStatement],
        "indexes": [(createStatement, owningConstraint|None, type)], "drift": {label: +n tu lan dem thu 2}}.
"""
from __future__ import annotations

import contextlib
import csv
import gzip
import io
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EXPORT = Path(__file__).resolve().parents[1] / "stack" / "neo4j-export.py"
PW = "pw-SECRET-123"


def sample_graph() -> dict:
    return {
        "nodes": {
            0: (["CVE"], {"id": "CVE-1", "cvss_score": 9.8, "is_in_kev": True, "severity": "HIGH", "title": 'a,"b"\nc'}),
            1: (["CVE"], {"id": "CVE-2", "cvss_score": 5.0, "is_in_kev": False, "title": "ü"}),
            # id 2 la khoang trong
            3: (["CVE"], {"id": "CVE-3", "cvss_score": 7.5, "is_in_kev": False, "severity": "LOW", "title": ""}),
            4: (["CWE"], {"id": "CWE-79", "name": "XSS"}),
            5: (["CWE"], {"id": "CWE-89", "name": "SQLi"}),
        },
        "rels": [(0, 4, "HAS_CWE", {}), (1, 5, "HAS_CWE", {}), (3, 4, "HAS_CWE", {}), (4, 5, "CHILD_OF", {"weight": 2})],
        "constraints": ["CREATE CONSTRAINT `cve_id` FOR (n:`CVE`) REQUIRE (n.`id`) IS UNIQUE"],
        "indexes": [
            ("CREATE RANGE INDEX `cve_id` FOR (n:`CVE`) ON (n.`id`)", "cve_id", "RANGE"),
            ("CREATE LOOKUP INDEX `nl` FOR (n) ON EACH labels(n)", None, "LOOKUP"),
            ("CREATE RANGE INDEX `cwe_name` FOR (n:`CWE`) ON (n.`name`)", None, "RANGE"),
        ],
    }


def _answer(g: dict, st: dict, stmt: str, p: dict) -> list:
    nodes, rels = g["nodes"], g["rels"]
    if "SHOW CONSTRAINTS" in stmt:
        return [[s] for s in g.get("constraints", [])]
    if "SHOW INDEXES" in stmt:
        ix = g.get("indexes", [])
        if "owningConstraint IS NULL" in stmt:
            ix = [x for x in ix if x[1] is None]
        if "type <> 'LOOKUP'" in stmt:
            ix = [x for x in ix if x[2] != "LOOKUP"]
        return [list(x) for x in ix]
    if "RETURN labels(n), count(*)" in stmt:
        st["label_counts"] += 1
        c: dict = {}
        for labels, _ in nodes.values():
            c[tuple(labels)] = c.get(tuple(labels), 0) + 1
        if st["label_counts"] >= 2:
            for lab, d in g.get("drift", {}).items():
                c[(lab,)] = c.get((lab,), 0) + d
        return [[list(k), v] for k, v in c.items()]
    if "RETURN type(r), count(*)" in stmt:
        c = {}
        for r in rels:
            c[r[2]] = c.get(r[2], 0) + 1
        return [[k, v] for k, v in c.items()]
    if "max(id(n))" in stmt:
        return [[max(nodes) if nodes else None]]
    m = re.search(r"MATCH \(n:`?(\w+)`?\) UNWIND keys\(n\)", stmt)
    if m:
        ks = {k for labels, props in nodes.values() if m.group(1) in labels for k in props}
        return [[k] for k in sorted(ks)]
    m = re.search(r"MATCH \(\)-\[r:`?(\w+)`?\]->\(\) UNWIND keys\(r\)", stmt)
    if m:
        ks = {k for r in rels if r[2] == m.group(1) for k in r[3]}
        return [[k] for k in sorted(ks)]
    if "MATCH (n) WHERE id(n)=i" in stmt:
        return [[i, nodes[i][0], nodes[i][1]] for i in range(p["lo"], p["hi"] + 1) if i in nodes]
    if "MATCH (a)-[r]->(b)" in stmt:
        return [[a, b, t, props] for i in range(p["lo"], p["hi"] + 1) for a, b, t, props in rels if a == i]
    raise KeyError(stmt)


@contextlib.contextmanager
def fake_neo4j(graph: dict, database: str = "neo4j"):
    st: dict = {"reqs": [], "label_counts": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            stmts = body["statements"]
            st["reqs"].append((self.headers.get("Authorization"), [s["statement"] for s in stmts]))
            res, errs = [], []
            if self.path != f"/db/{database}/tx/commit":
                errs.append({"code": "Fake.BadPath", "message": self.path})
            else:
                for s in stmts:
                    try:
                        res.append({"columns": [], "data": [{"row": r} for r in _answer(graph, st, s["statement"], s.get("parameters") or {})]})
                    except KeyError:
                        errs.append({"code": "Fake.Unknown", "message": s["statement"]})
            b = json.dumps({"results": res, "errors": errs}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", st
    finally:
        srv.shutdown()
        srv.server_close()


def run_export(url: str, out: Path, *args: str, password: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}
    env["NEO4J_URL"] = url
    if password is not None:
        env["NEO4J_PASSWORD"] = password
    return subprocess.run([sys.executable, "-I", str(EXPORT), *args, str(out)], env=env, capture_output=True, text=True, timeout=60)


def gz(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes())


def export(tmp_path: Path, graph: dict, *args: str, **kw):
    out = tmp_path / "out"
    with fake_neo4j(graph) as (url, st):
        r = run_export(url, out, "--node-page", "2", "--rel-page", "2", *args, **kw)
    return r, out, st


def test_export_writes_import_files(tmp_path):
    r, out, _ = export(tmp_path, sample_graph())
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted([
        "nodes-CVE.header.csv", "nodes-CVE.csv.gz", "nodes-CWE.header.csv", "nodes-CWE.csv.gz",
        "rels-HAS_CWE.header.csv", "rels-HAS_CWE.csv.gz", "rels-CHILD_OF.header.csv", "rels-CHILD_OF.csv.gz",
        "schema.cypher", "counts.json"])
    assert (out / "nodes-CVE.header.csv").read_text() == ":ID,cvss_score:double,id:string,is_in_kev:boolean,severity:string,title:string,:LABEL\n"
    assert (out / "nodes-CWE.header.csv").read_text() == ":ID,id:string,name:string,:LABEL\n"
    assert (out / "rels-HAS_CWE.header.csv").read_text() == ":START_ID,:END_ID,:TYPE\n"
    assert (out / "rels-CHILD_OF.header.csv").read_text() == ":START_ID,:END_ID,:TYPE,weight:long\n"
    assert gz(out / "nodes-CWE.csv.gz") == b'4,"CWE-79","XSS",CWE\n5,"CWE-89","SQLi",CWE\n'
    assert gz(out / "rels-HAS_CWE.csv.gz") == b"0,4,HAS_CWE\n1,5,HAS_CWE\n3,4,HAS_CWE\n"
    assert gz(out / "rels-CHILD_OF.csv.gz") == b"4,5,CHILD_OF,2\n"
    c = json.loads((out / "counts.json").read_text())
    assert c["nodes"] == {"CVE": 3, "CWE": 2} and c["relationships"] == {"HAS_CWE": 3, "CHILD_OF": 1}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.*(Z|\+00:00)", c["exported_at"])


def test_csv_encoding_round_trip(tmp_path):
    r, out, _ = export(tmp_path, sample_graph())
    assert r.returncode == 0, r.stderr
    raw = gz(out / "nodes-CVE.csv.gz")
    assert raw == ('0,9.8,"CVE-1",true,"HIGH","a,""b""\nc",CVE\n'
                   '1,5.0,"CVE-2",false,,"ü",CVE\n'
                   '3,7.5,"CVE-3",false,"LOW","",CVE\n').encode()
    rows = list(csv.reader(io.StringIO(raw.decode(), newline="")))
    assert [row[5] for row in rows] == ['a,"b"\nc', "ü", ""]
    assert rows[1][4] == ""


def test_count_drift_exits_3(tmp_path):
    g = sample_graph()
    g["drift"] = {"CVE": 1}
    r, out, _ = export(tmp_path, g)
    assert r.returncode == 3, r.stderr
    assert not (out / "counts.json").exists()
    assert "CVE" in r.stderr


@pytest.mark.parametrize("mutate", [
    lambda g: g["nodes"].__setitem__(9, (["CVE", "KEV"], {"id": "CVE-9"})),
    lambda g: g["nodes"].__setitem__(9, (["CVE"], {"id": "CVE-9", "refs": ["a", "b"]})),
    lambda g: g["nodes"].__setitem__(9, (["CVE"], {"id": "CVE-9", "meta": {"a": 1}})),
    lambda g: g["nodes"].__setitem__(9, (["Bad-Label"], {"id": "x"})),
    lambda g: g["rels"].append((0, 5, "BAD TYPE", {})),
    lambda g: g["nodes"].__setitem__(9, (["CVE"], {"id": 9})),
    lambda g: g["rels"].append((5, 4, "CHILD_OF", {"weight": "two"})),
], ids=["multilabel", "list", "map", "bad-label", "bad-type", "string-and-long", "rel-conflict"])
def test_multilabel_and_list_property_exit_2(tmp_path, mutate):
    g = sample_graph()
    mutate(g)
    r, out, _ = export(tmp_path, g)
    assert r.returncode == 2 and ("không hỗ trợ" in r.stderr or "xung đột" in r.stderr), r.stderr
    assert not (out / "counts.json").exists()


def test_int_and_float_mix_is_double(tmp_path):
    g = sample_graph()
    g["nodes"][1][1]["cvss_score"] = 5
    r, out, _ = export(tmp_path, g)
    assert r.returncode == 0, r.stderr
    assert ",cvss_score:double," in (out / "nodes-CVE.header.csv").read_text()
    assert b'\n1,5,"CVE-2",' in gz(out / "nodes-CVE.csv.gz")


def test_schema_cypher_from_show_constraints(tmp_path):
    r, out, _ = export(tmp_path, sample_graph())
    assert r.returncode == 0, r.stderr
    assert (out / "schema.cypher").read_text() == (
        "CREATE CONSTRAINT `cve_id` FOR (n:`CVE`) REQUIRE (n.`id`) IS UNIQUE;\n"
        "CREATE RANGE INDEX `cwe_name` FOR (n:`CWE`) ON (n.`name`);\n")


def test_password_not_in_output(tmp_path):
    r, out, st = export(tmp_path, sample_graph(), password=PW)
    assert r.returncode == 0, r.stderr
    assert st["reqs"] and all(a and a.startswith("Basic ") for a, _ in st["reqs"])
    assert PW not in r.stdout + r.stderr
    for p in out.iterdir():
        data = gz(p) if p.suffix == ".gz" else p.read_bytes()
        assert PW.encode() not in data


def test_no_password_sends_no_auth_and_only_reads(tmp_path):
    r, _, st = export(tmp_path, sample_graph())
    assert r.returncode == 0, r.stderr
    assert all(a is None for a, _ in st["reqs"])
    for _, stmts in st["reqs"]:
        for s in stmts:
            assert not re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|CALL|LOAD)\b", s), s


def test_neo4j_error_exits_1(tmp_path):
    out = tmp_path / "out"
    with fake_neo4j(sample_graph(), database="other") as (url, _):
        r = run_export(url, out)
    assert r.returncode == 1
    assert "Fake.BadPath" in r.stderr
    assert not (out / "counts.json").exists()


def test_out_dir_not_empty_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with fake_neo4j(sample_graph()) as (url, st):
        r = run_export(url, out)
    assert r.returncode == 1
    assert st["reqs"] == []
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]
