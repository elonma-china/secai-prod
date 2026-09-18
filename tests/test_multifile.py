"""Deterministic reference-code injection + multi-file PoC contract + set picker."""
from __future__ import annotations

import asyncio

from linch.tools.base import ToolContext

from secai_gateway.poc.backend import LocalPocBackend
from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.collectors.exploitdb import ExploitDbCollector
from secai_gateway.poc.fetch_github import _pick_set
from secai_gateway.poc.inject import (
    enforce_reference_code,
    force_no_poc_notice,
    render_files,
    strip_system_narration,
)
from secai_gateway.poc.models import NormalizedPoc
from secai_gateway.poc.tools import build_poc_tools
from secai_gateway.state import SqliteLedger

_CSV_HEADER = ("id,file,description,date_published,author,type,platform,port,date_added,"
               "date_updated,verified,codes,tags,aliases,screenshot_url,application_url,source_url\n")


def _ctx(run_id: str, idem: str) -> ToolContext:
    return ToolContext(cwd=".", session_id="s", run_id=run_id, session_store=None, idempotency_key=idem)

# Defensive fixture: model wrote a stray Section 1 anyway, despite instructions not to.
_REPORT = (
    "## 1. Mã Khai thác (PoC) và Payload\n\n"
    "```python\nFAKE_MODEL_CODE = 1\n```\n\n"
    "## 2. Tổng quan Lỗ hổng\n\n- mô tả\n"
)
# The model is instructed to NEVER write Section 1 (prompts/exploit.md) and produces
# exactly one section — this is the actual expected shape of model output.
_MODEL_REPORT = "## 2. Tổng quan Lỗ hổng\n\n- mô tả\n"


# ── single-file injection: default path — model wrote no Section 1 at all ───────
def test_inject_prepends_section1_when_model_writes_none() -> None:
    # this is the NORMAL case now: the model is told never to write Section 1, so its
    # output starts at "## 2." and the injector must PREPEND Section 1 before it.
    ev = {
        "source": "exploitdb", "source_ref": "51234",
        "url": "https://www.exploit-db.com/exploits/51234", "sha256": "a" * 64,
        "source_code": "import socket\nprint('real exploit')\n",
        "static_indicators": {"language": "py"},
    }
    out, report = enforce_reference_code(_MODEL_REPORT, [ev])
    assert report["reference_code_injected"] is True
    assert out.startswith("## 1. Mã Khai thác (PoC) và Payload")
    assert "import socket\nprint('real exploit')" in out
    assert "```python" in out
    assert "sha256" in out and "51234" in out
    assert "PoC công khai" not in out  # no verbose provenance blockquote — UI clutter
    # model's own Section 2 preserved verbatim after the prepended Section 1
    assert out.index("## 1.") < out.index("## 2. Tổng quan Lỗ hổng")


def test_inject_noop_without_source_code() -> None:
    out, report = enforce_reference_code(_MODEL_REPORT, [{"source": "ghsa", "source_code": None}])
    assert report["reference_code_injected"] is False
    assert out == _MODEL_REPORT


# ── single-file injection: defensive path — model wrote "## 1." anyway ──────────
def test_inject_replaces_stray_section1_the_model_wrote_anyway() -> None:
    ev = {
        "source": "exploitdb", "source_ref": "51234",
        "url": "https://www.exploit-db.com/exploits/51234", "sha256": "a" * 64,
        "source_code": "import socket\nprint('real exploit')\n",
        "static_indicators": {"language": "py"},
    }
    out, report = enforce_reference_code(_REPORT, [ev])
    assert report["reference_code_injected"] is True
    assert "FAKE_MODEL_CODE" not in out           # model's code was discarded
    assert "import socket\nprint('real exploit')" in out   # real bytes injected
    assert "```python" in out                     # language from static_indicators
    assert "sha256" in out and "51234" in out
    assert "PoC công khai" not in out  # no verbose provenance blockquote — UI clutter
    # later section untouched, no duplicated Section 1
    assert out.count("## 1.") == 1
    assert "## 2. Tổng quan Lỗ hổng" in out


def test_inject_escapes_embedded_fence() -> None:
    ev = {"source": "x", "source_ref": "y", "sha256": "c" * 64,
          "source_code": "s = '''```'''\n", "static_indicators": {"language": "py"}}
    out, _ = enforce_reference_code(_REPORT, [ev])
    assert "````python" in out                    # fence longer than the embedded ```


# ── multi-file contract ──────────────────────────────────────────────────────
def test_inject_multifile_preserves_hierarchy_and_order() -> None:
    ev = {
        "source": "poc_github", "source_ref": "acme/wp2shell/wp2shell/exploit.py",
        "url": "https://github.com/acme/wp2shell", "sha256": "d" * 64,
        "source_files": [
            {"path": "wp2shell/exploit.py", "code": "print('main')\n", "sha256": "1" * 64,
             "language": "py", "role": "entry"},
            {"path": "wp2shell/sqli.py", "code": "print('sqli')\n", "sha256": "2" * 64,
             "language": "py", "role": "support"},
            {"path": "requirements.txt", "code": "requests\n", "sha256": "3" * 64,
             "language": None, "role": "support"},
        ],
    }
    out, report = enforce_reference_code(_REPORT, [ev])
    assert report["reference_code_injected"] is True and report["files"] == 3
    assert "Cấu trúc PoC" in out                              # repo tree header
    assert "**`wp2shell/exploit.py`**" in out                # hierarchy preserved, not flattened
    assert "**`wp2shell/sqli.py`**" in out
    # manifest sorts last; entry PoC first
    assert out.index("`wp2shell/exploit.py`") < out.index("`wp2shell/sqli.py`")
    assert out.index("`wp2shell/sqli.py`") < out.index("`requirements.txt`")


def test_render_files_orders_manifest_last() -> None:
    files = [
        {"path": "requirements.txt", "code": "requests\n"},
        {"path": "pkg/main.py", "code": "x=1\n"},
    ]
    rendered = render_files(files)
    assert rendered.index("`pkg/main.py`") < rendered.index("`requirements.txt`")


def test_render_files_excludes_readme_body_but_keeps_it_in_tree() -> None:
    # README's real content isn't dumped as a raw-English fenced block in Mục 1 — the model
    # translates it into "Cách chạy PoC" in its own Mục 2 (see prompts/exploit.md). The tree
    # still lists it for transparency about the real repo structure.
    files = [
        {"path": "exploit.py", "code": "print(1)\n", "role": "entry"},
        {"path": "README.md", "code": "## Usage\nrun: python exploit.py --host X\n", "role": "readme"},
    ]
    r = render_files(files)
    assert "README.md" in r                       # listed in the tree
    assert "run: python exploit.py" not in r       # but its raw content is NOT dumped as a block
    assert "**`README.md`**" not in r              # no fenced block header for it either


def test_render_files_suppresses_tree_when_show_tree_false() -> None:
    # companion-doc case (metasploit/vulhub): 2 disparate paths from a huge shared repo,
    # not this PoC's own dedicated repo — a tree would misleadingly imply otherwise.
    files = [
        {"path": "modules/exploits/multi/http/foo.rb", "code": "class X\nend\n", "role": "entry"},
        {"path": "documentation/modules/exploit/multi/http/foo.md", "code": "## Steps\n...\n", "role": "readme"},
    ]
    r = render_files(files, show_tree=False)
    assert "Cấu trúc PoC" not in r
    assert "**`modules/exploits/multi/http/foo.rb`**" in r   # file blocks still render


def test_render_files_shows_tree_by_default_for_dedicated_multifile() -> None:
    files = [
        {"path": "wp2shell/exploit.py", "code": "print(1)\n", "role": "entry"},
        {"path": "wp2shell/sqli.py", "code": "print(2)\n", "role": "support"},
    ]
    r = render_files(files)   # show_tree defaults True
    assert "Cấu trúc PoC" in r


def test_blocks_for_respects_companion_doc_only_flag() -> None:
    ev = {
        "source": "metasploit", "source_ref": "exploit/multi/http/foo", "sha256": "a" * 64,
        "companion_doc_only": True,
        "source_files": [
            {"path": "modules/exploits/multi/http/foo.rb", "code": "class X\nend\n", "role": "entry"},
            {"path": "documentation/modules/exploit/multi/http/foo.md", "code": "## Steps\n", "role": "readme"},
        ],
    }
    out, report = enforce_reference_code(_MODEL_REPORT, [ev])
    assert report["reference_code_injected"] is True
    assert "Cấu trúc PoC" not in out


def test_render_files_entry_first_not_alphabetical() -> None:
    # regression: the entry PoC must lead even when a support file sorts earlier by name
    files = [
        {"path": "aaa_docker.sh", "code": "x\n", "role": "support"},
        {"path": "sploit.py", "code": "y\n", "role": "entry"},
        {"path": "requirements.txt", "code": "z\n", "role": "support"},
        {"path": "start.sh", "code": "w\n", "role": "support"},
    ]
    r = render_files(files)
    assert r.index("`sploit.py`") < r.index("`aaa_docker.sh`")     # entry leads
    assert r.index("`aaa_docker.sh`") < r.index("`start.sh`")      # supports keep input order
    assert r.index("`start.sh`") < r.index("`requirements.txt`")   # manifest last


# ── set picker ───────────────────────────────────────────────────────────────
def test_pick_set_gathers_package_subtree_and_root_manifest() -> None:
    files = [
        ("wp2shell/exploit.py", b"import socket\n# entry\n"),
        ("wp2shell/sqli.py", b"# sqli helper\n"),
        ("wp2shell/__init__.py", b"# pkg\n"),
        ("requirements.txt", b"requests\n"),
        ("tests/test_x.py", b"# test\n"),      # skip-dir → excluded
        ("README.md", b"# how to run\n"),      # real usage doc → included (translated in Mục 2)
        ("LICENSE", b"MIT\n"),                 # not usage-relevant → excluded
    ]
    got = _pick_set(files, "wp2shell/exploit.py")
    paths = [p for p, _ in got]
    assert paths[0] == "wp2shell/exploit.py"                 # entry first
    assert "wp2shell/sqli.py" in paths and "requirements.txt" in paths and "README.md" in paths
    assert "tests/test_x.py" not in paths and "LICENSE" not in paths


def test_is_doc_file_recognizes_common_doc_naming_conventions() -> None:
    from secai_gateway.poc.fetch_github import _is_doc_file
    for name in ("README.md", "report.md", "USAGE.md", "writeup.md", "walkthrough.rst",
                "NOTES.md", "readme", "README.txt"):
        assert _is_doc_file(name), name
    for name in ("requirements.txt", "exploit.py", "setup.sh", "config.json", "poc.yaml"):
        assert not _is_doc_file(name), name


def test_pick_set_recognizes_doc_files_beyond_literal_readme() -> None:
    # regression: report.md (DepthFirstDisclosures/Nginx-Rift's actual doc filename) was
    # invisible to the old exact-name-only detection — authors call these README, report,
    # usage, writeup, walkthrough, NOTES... .md/.rst is the reliable signal, not the name.
    files = [
        ("CVE-2026-42533/poc.py", b"# entry\n"),
        ("CVE-2026-42533/report.md", b"## How to run\n...\n"),
        ("CVE-2026-42533/setup.sh", b"#!/bin/bash\n"),
        ("requirements.txt", b"requests\n"),  # .txt manifest — must NOT be treated as a doc
    ]
    got = _pick_set(files, "CVE-2026-42533/poc.py")
    paths = [p for p, _ in got]
    assert "CVE-2026-42533/report.md" in paths
    assert "requirements.txt" in paths  # still included, just as a manifest, not misfiled as doc


def test_pick_set_root_entry_excludes_nested_dirs() -> None:
    files = [
        ("exploit.py", b"# entry\n"), ("helper.py", b"# helper\n"),
        ("lib/deep.py", b"# nested\n"), ("requirements.txt", b"req\n"),
    ]
    paths = [p for p, _ in _pick_set(files, "exploit.py")]
    assert "exploit.py" in paths and "helper.py" in paths and "requirements.txt" in paths
    assert "lib/deep.py" not in paths                        # entry at root → root-level only


# ── ledger read-back feeds the injector ──────────────────────────────────────
def test_evidence_for_run_roundtrip(tmp_path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.db")
    ledger.save_evidence("ev:1", "run-A", {"sha256": "a" * 64, "source_code": "print(1)\n"})
    ledger.save_evidence("ev:2", "run-A", {"sha256": "b" * 64, "source_code": "print(2)\n"})
    ledger.save_evidence("ev:3", "run-B", {"sha256": "c" * 64, "source_code": "print(3)\n"})
    rows = ledger.evidence_for_run("run-A")
    assert [r["sha256"][0] for r in rows] == ["a", "b"]      # oldest-first, run-scoped
    assert ledger.evidence_for_run(None) == []


# ── evidence_ledger must be run-scoped, not content-scoped ──────────────────────
def test_evidence_recorded_for_every_run_even_when_artifact_already_seen(tmp_path) -> None:
    # regression: evidence_ledger.evidence_id used to be `f"evidence:{sha256[:24]}"` (content
    # only) — a PRIMARY KEY, so `INSERT OR IGNORE` silently dropped every run's write once
    # ANY earlier run had fetched the same popular artifact, starving evidence_for_run() for
    # that later run even though ITS poc_fetch genuinely succeeded (observed live on
    # CVE-2026-42533: real fetch succeeded, but injection + false-claim correction both saw
    # zero evidence for the run because someone else had fetched it earlier).
    root = tmp_path / "exploitdb"
    (root / "exploits" / "linux").mkdir(parents=True)
    (root / "exploits" / "linux" / "1.py").write_text("import socket\n", encoding="utf-8")
    (root / "files_exploits.csv").write_text(
        _CSV_HEADER + "1,exploits/linux/1.py,Demo RCE,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n",
        encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    for poc in ExploitDbCollector(root).collect():
        cat.upsert(poc)
    cat.commit()
    be = LocalPocBackend(catalog=cat, source_roots={"exploitdb": root})
    ledger = SqliteLedger(tmp_path / "ledger.db")
    tools = build_poc_tools(be, ledger)

    for run_id in ("run-A", "run-B"):  # two independent runs fetch the SAME artifact
        hits = asyncio.run(tools["poc_search"].fn("CVE-2021-44228", _ctx(run_id, f"{run_id}:search"), 10))
        ref = __import__("json").loads(hits.content)["hits"][0]["ref"]
        result = asyncio.run(tools["poc_fetch"].fn(ref, _ctx(run_id, f"{run_id}:fetch")))
        assert not result.is_error

    assert len(ledger.evidence_for_run("run-A")) == 1   # first-ever fetch: recorded
    assert len(ledger.evidence_for_run("run-B")) == 1   # SAME artifact, LATER run: still recorded


# ── force "no PoC info" when the run has no real evidence ──────────────────────
# Even though the model is told never to write Section 1, it can still slip and write
# fabricated code there (or, more commonly under the new contract, write nothing — its
# output starts straight at "## 2."). Either way, with zero real evidence the response
# must show the canonical "no info" sentence, never any code.
_FABRICATED_REPORT = (
    "## 1. Mã Khai thác (PoC) và Payload\n\n"
    "PoC công khai (nguồn: Exploit-DB, sha256: a1b2c3d4e5f6)\n\n"
    "```c\nint main(){/* fabricated */ return 0;}\n```\n\n"
    "## 2. Tổng quan Lỗ hổng\n\n- mô tả\n"
)
_NO_POC_SENTENCE = "Chưa có thông tin PoC được xác thực trong dữ liệu nội bộ cho lỗ hổng này."


def test_force_no_poc_notice_default_path_prepends_when_no_evidence() -> None:
    # the common case under the new contract: model wrote no Section 1 at all
    out, report = force_no_poc_notice(_MODEL_REPORT, [])
    assert report["no_poc_forced"] is True
    assert out.startswith(f"## 1. Mã Khai thác (PoC) và Payload\n\n{_NO_POC_SENTENCE}")
    assert "## 2. Tổng quan Lỗ hổng" in out


def test_force_no_poc_notice_strips_fabricated_code_entirely() -> None:
    # defensive path: model wrote a stray Section 1 with fabricated code anyway — the
    # code must be DROPPED, not kept-and-relabeled.
    out, report = force_no_poc_notice(_FABRICATED_REPORT, [])
    assert report["no_poc_forced"] is True
    assert "fabricated" not in out and "int main" not in out and "a1b2c3d4e5f6" not in out
    assert out.count("## 1.") == 1
    section1_body = out.split("## 1. Mã Khai thác (PoC) và Payload", 1)[1].split("## 2.", 1)[0].strip()
    assert section1_body == _NO_POC_SENTENCE
    assert "## 2. Tổng quan Lỗ hổng" in out


def test_force_no_poc_notice_noop_with_real_evidence() -> None:
    ev = [{"source": "exploitdb", "source_ref": "1", "sha256": "a" * 64, "source_code": "x=1\n"}]
    out, report = force_no_poc_notice(_FABRICATED_REPORT, ev)
    assert report["no_poc_forced"] is False and report["reason"] == "has_real_evidence"
    assert out == _FABRICATED_REPORT


def test_force_no_poc_notice_is_idempotent() -> None:
    once, r1 = force_no_poc_notice(_MODEL_REPORT, [])
    assert r1["no_poc_forced"] is True
    twice, r2 = force_no_poc_notice(once, [])
    assert r2["no_poc_forced"] is False and r2["reason"] == "already_canonical"
    assert twice == once
    # also idempotent on the defensive (stray-Section-1) path
    once2, _ = force_no_poc_notice(_FABRICATED_REPORT, [])
    twice2, r2b = force_no_poc_notice(once2, [])
    assert r2b["no_poc_forced"] is False and r2b["reason"] == "already_canonical"
    assert twice2 == once2


def test_inject_reference_code_falls_back_to_no_poc_notice() -> None:
    # enforce_reference_code no-ops (no evidence) → force_no_poc_notice must still run
    out, report = enforce_reference_code(_MODEL_REPORT, [])
    assert report["reference_code_injected"] is False
    final, follow_up = force_no_poc_notice(out, [])
    assert follow_up["no_poc_forced"] is True
    assert _NO_POC_SENTENCE in final and "```" not in final


# ── multi-file backfill (re-fetch) query ─────────────────────────────────────
def test_pending_github_refetch_targets_fetched_without_fileset(tmp_path) -> None:
    cat = PocCatalog(tmp_path / "poc.db")
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="acme/repo", kind="exploit", title="t",
        cve_ids=["CVE-2026-1"], provenance={"stars": 10, "default_branch": "main"}))
    poc_id = cat._db.execute("SELECT id FROM poc").fetchone()[0]
    # never fetched → normal pending only, not backfill
    assert len(cat.pending_github(10, 0, refetch=False)) == 1
    assert len(cat.pending_github(10, 0, refetch=True)) == 0
    # single-file fetched (artifact set, no file set) → backfill target, not normal pending
    cat.set_artifact(poc_id, "d" * 64, "dd/" + "d" * 64, source_ref="acme/repo/exploit.py")
    assert len(cat.pending_github(10, 0, refetch=False)) == 0
    assert len(cat.pending_github(10, 0, refetch=True)) == 1
    # once it has a multi-file set, backfill skips it
    cat.set_files(poc_id, [
        {"rel_path": "exploit.py", "sha256": "e" * 64, "storage_path": "ee/" + "e" * 64},
        {"rel_path": "helper.py", "sha256": "f" * 64, "storage_path": "ff/" + "f" * 64}])
    assert len(cat.pending_github(10, 0, refetch=True)) == 0


# ── companion "how to run" doc for single-artifact sources ─────────────────────
# metasploit/vulhub have no poc_file multi-file set (that mechanism is poc_github-only),
# but each has a REAL doc alongside its main artifact that's otherwise never surfaced:
# metasploit's documentation/modules/exploit/.../name.md (real Verification Steps with
# actual `use`/`run` commands) and vulhub's README.md (real step-by-step HTTP walkthrough,
# currently read by the collector only for a title string, then discarded).
def test_companion_doc_path_metasploit() -> None:
    be = LocalPocBackend.__new__(LocalPocBackend)  # path derivation needs no I/O/catalog
    got = be._companion_doc_path("metasploit", "modules/exploits/multi/http/foo_rce.rb")
    assert got == "documentation/modules/exploit/multi/http/foo_rce.md"


def test_companion_doc_path_vulhub() -> None:
    be = LocalPocBackend.__new__(LocalPocBackend)
    got = be._companion_doc_path("vulhub", "log4j/CVE-2021-44228/docker-compose.yml")
    assert got == "log4j/CVE-2021-44228/README.md"


def test_companion_doc_path_none_for_other_sources() -> None:
    be = LocalPocBackend.__new__(LocalPocBackend)
    assert be._companion_doc_path("exploitdb", "exploits/linux/1.py") is None
    assert be._companion_doc_path("nuclei", "http/cves/2025/CVE-2025-1.yaml") is None


def test_fetch_attaches_metasploit_companion_doc(tmp_path) -> None:
    root = tmp_path / "msf"
    mod = root / "modules" / "exploits" / "multi" / "http"
    mod.mkdir(parents=True)
    (mod / "foo_rce.rb").write_text("class MetasploitModule\nend\n", encoding="utf-8")
    doc = root / "documentation" / "modules" / "exploit" / "multi" / "http"
    doc.mkdir(parents=True)
    (doc / "foo_rce.md").write_text(
        "## Verification Steps\n1. Do: `use exploit/multi/http/foo_rce`\n"
        "2. Do: `run rhost=<target>`\n", encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    cat.upsert(NormalizedPoc(
        source="metasploit", source_ref="exploit/multi/http/foo_rce", kind="exploit",
        title="Foo RCE", cve_ids=["CVE-2099-1001"], verified=True, redistributable=True,
        language="rb", artifact_path="modules/exploits/multi/http/foo_rce.rb"))
    be = LocalPocBackend(catalog=cat, source_roots={"metasploit": root})
    ref = asyncio.run(be.search("CVE-2099-1001", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["source_code"] and "MetasploitModule" in fetched["source_code"]
    assert fetched["companion_doc_only"] is True   # 2 disparate paths, not a dedicated repo
    sf = fetched["source_files"]
    assert sf and len(sf) == 2
    roles = {f["path"]: f["role"] for f in sf}
    assert roles["modules/exploits/multi/http/foo_rce.rb"] == "entry"
    assert roles["documentation/modules/exploit/multi/http/foo_rce.md"] == "readme"
    readme = next(f for f in sf if f["role"] == "readme")
    assert "Verification Steps" in readme["code"] and "use exploit/multi/http/foo_rce" in readme["code"]


def test_fetch_attaches_vulhub_readme(tmp_path) -> None:
    root = tmp_path / "vulhub"
    d = root / "log4j" / "CVE-2021-44228"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text("services:\n  solr:\n    image: solr:8.11.0\n", encoding="utf-8")
    (d / "README.md").write_text(
        "# Log4Shell\nRun `docker compose up -d` then send the JNDI payload.\n", encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    cat.upsert(NormalizedPoc(
        source="vulhub", source_ref="log4j/CVE-2021-44228", kind="lab",
        title="Log4Shell lab", cve_ids=["CVE-2021-44228"], verified=True,
        language="yaml", artifact_path="log4j/CVE-2021-44228/docker-compose.yml"))
    be = LocalPocBackend(catalog=cat, source_roots={"vulhub": root})
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["companion_doc_only"] is True
    sf = fetched["source_files"]
    assert sf and len(sf) == 2
    readme = next(f for f in sf if f["role"] == "readme")
    assert readme["path"] == "log4j/CVE-2021-44228/README.md"
    assert "docker compose up -d" in readme["code"]


def test_fetch_no_companion_doc_falls_back_gracefully(tmp_path) -> None:
    # module exists but has no documentation/ counterpart — must not error, just no source_files
    root = tmp_path / "msf"
    mod = root / "modules" / "exploits" / "linux" / "http"
    mod.mkdir(parents=True)
    (mod / "bar.rb").write_text("class MetasploitModule\nend\n", encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    cat.upsert(NormalizedPoc(
        source="metasploit", source_ref="exploit/linux/http/bar", kind="exploit", title="Bar",
        cve_ids=["CVE-2099-2002"], verified=True, redistributable=True, language="rb",
        artifact_path="modules/exploits/linux/http/bar.rb"))
    be = LocalPocBackend(catalog=cat, source_roots={"metasploit": root})
    ref = asyncio.run(be.search("CVE-2099-2002", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["source_code"] and fetched["source_files"] is None


# ── strip_system_narration ───────────────────────────────────────────────────
def test_strip_system_narration_removes_leaked_instruction() -> None:
    # observed live: the model echoed exploit.md's OWN internal instruction wording
    # ("hệ thống tự chèn... Mục 1") back into the visible answer instead of silently
    # letting Section 1 be inserted.
    text = (
        "## 3. Tổng quan Lỗ hổng\n\n- mô tả\n\n"
        "Hệ thống sẽ chèn Mã Khai thác/PoC (Mục 1) ngay sau phần này.\n"
    )
    out = strip_system_narration(text)
    assert "Hệ thống sẽ chèn" not in out
    assert "## 3. Tổng quan Lỗ hổng" in out and "- mô tả" in out  # rest untouched


def test_strip_system_narration_tolerates_word_order_and_phrasing() -> None:
    text = "Mã Khai thác (Mục 1) sẽ được hệ thống bổ sung ngay sau đây.\n## 3. Tổng quan\n"
    out = strip_system_narration(text)
    assert "hệ thống" not in out.lower() or "bổ sung" not in out.lower()
    assert "## 3. Tổng quan" in out


def test_strip_system_narration_does_not_touch_legitimate_system_mentions() -> None:
    # "hệ thống" alone, or paired with unrelated words, must survive untouched —
    # only the specific (hệ thống + insertion verb + "Mục 1") co-occurrence is stripped.
    text = (
        "- Hệ thống mục tiêu chạy Nginx phiên bản cũ, chưa vá lỗ hổng.\n"
        "- Kẻ tấn công có thể chiếm quyền hệ thống sau khi khai thác thành công.\n"
    )
    out = strip_system_narration(text)
    assert out == text


def test_strip_system_narration_noop_on_clean_text() -> None:
    text = "## 2. Cách chạy PoC\n\n- chạy: python poc.py --host X\n"
    assert strip_system_narration(text) == text
