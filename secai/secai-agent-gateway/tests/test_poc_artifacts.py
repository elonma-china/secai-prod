"""Full PoC in the existing /ai/exploit Section 1 (no new endpoint, no FE change).

The FE renders Section 1 from `description`/`exploit_guide`, which the injector builds from
the run's evidence — whose code was capped at fetch time for the LLM context. At injection
time (AFTER the model ran) `_fullen_evidence_for_display` swaps each file's truncated code for
its FULL store bytes, so the DISPLAYED PoC is complete while the model context stays bounded.
The load-bearing assertion: a file larger than the render cap comes back complete.
"""
from __future__ import annotations

import asyncio
import hashlib

from secai_gateway.api.routes import _fullen_evidence_for_display
from secai_gateway.poc.backend import (
    LocalPocBackend,
    PocBadRequest,
    PocNotFound,
    _MAX_SET_FILE_CHARS,
)
from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.models import NormalizedPoc


def _store_blob(store, text: str) -> tuple[str, str]:
    """Write `text` into the content-addressed store; return (sha256, 'aa/aaaa…')."""
    b = text.encode()
    sha = hashlib.sha256(b).hexdigest()
    d = store / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    (d / sha).write_bytes(b)
    return sha, f"{sha[:2]}/{sha}"


def _poc_id(cat: PocCatalog, source_ref: str) -> int:
    return cat._db.execute("SELECT id FROM poc WHERE source_ref=?", (source_ref,)).fetchone()[0]


def _backend(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    cat = PocCatalog(tmp_path / "cat.db")
    be = LocalPocBackend(catalog=cat, source_roots={"poc_github": store})
    return be, cat, store


# ── read_artifact: the untruncated guarantee ─────────────────────────────────
def test_read_artifact_returns_full_content_beyond_render_budget(tmp_path) -> None:
    be, cat, store = _backend(tmp_path)
    cve = "CVE-2099-0002"
    big = "A" * (_MAX_SET_FILE_CHARS * 3)          # 3× the per-file render cap
    cat.upsert(NormalizedPoc(source="poc_github", source_ref="big/repo", kind="exploit",
                             title="big/repo", cve_ids=[cve], verified=True, rank=10))
    pid = _poc_id(cat, "big/repo")
    sha_b, sp_b = _store_blob(store, big)
    cat.set_artifact(pid, sha_b, sp_b)
    cat.set_files(pid, [{"rel_path": "big.py", "sha256": sha_b, "storage_path": sp_b, "language": "py", "role": "entry", "size": len(big)}])
    cat.commit()

    r = asyncio.run(be.read_artifact(sha_b))
    assert r["truncated"] is False
    assert len(r["content"]) == len(big) == _MAX_SET_FILE_CHARS * 3   # FULL, not the 12k slice
    assert r["path"] == "big.py" and r["sha256"] == sha_b and r["size"] == len(big)


def test_read_artifact_singlefile_by_artifact_hash(tmp_path) -> None:
    be, cat, store = _backend(tmp_path)
    cat.upsert(NormalizedPoc(source="poc_github", source_ref="solo/two", kind="exploit",
                             title="solo/two", cve_ids=["CVE-2099-0003"], verified=True, rank=10))
    pid = _poc_id(cat, "solo/two")
    sha_s, sp_s = _store_blob(store, "print('hi')\n")
    cat.set_artifact(pid, sha_s, sp_s)
    cat.commit()
    r = asyncio.run(be.read_artifact(sha_s))
    assert r["content"] == "print('hi')\n" and r["truncated"] is False


def test_read_artifact_bad_and_unknown_sha(tmp_path) -> None:
    be, _, _ = _backend(tmp_path)
    for bad in ("zzz", "", "../etc"):
        try:
            asyncio.run(be.read_artifact(bad))
            assert False, "expected PocBadRequest"
        except PocBadRequest:
            pass
    try:
        asyncio.run(be.read_artifact("0" * 64))
        assert False, "expected PocNotFound"
    except PocNotFound:
        pass


def test_read_artifact_path_escape_guarded(tmp_path) -> None:
    be, cat, store = _backend(tmp_path)
    cat.upsert(NormalizedPoc(source="poc_github", source_ref="esc/repo", kind="exploit",
                             title="esc/repo", cve_ids=["CVE-2099-0005"], verified=True, rank=1))
    pid = _poc_id(cat, "esc/repo")
    sha_ok, sp_ok = _store_blob(store, "ok\n")
    cat.set_artifact(pid, sha_ok, sp_ok)          # cleared artifact → _allow_code passes
    sha_bad = "b" * 64
    cat.set_files(pid, [{"rel_path": "evil", "sha256": sha_bad, "storage_path": "../../etc/passwd", "language": None, "role": "entry", "size": 1}])
    cat.commit()
    try:
        asyncio.run(be.read_artifact(sha_bad))
        assert False, "expected PocNotFound (escape guarded)"
    except PocNotFound:
        pass


# ── fullen evidence for display (the Section-1 path) ─────────────────────────
def _repo_backend(tmp_path, cve: str, ref: str, files: dict[str, str]):
    """A multi-file PoC whose complete set is `files` {rel_path: content}; returns
    (backend, {rel_path: sha256}). First file is the entry."""
    be, cat, store = _backend(tmp_path)
    cat.upsert(NormalizedPoc(source="poc_github", source_ref=ref, kind="exploit",
                             title=ref, cve_ids=[cve], verified=True, rank=1))
    pid = _poc_id(cat, ref)
    shas, rows = {}, []
    for i, (rel, content) in enumerate(files.items()):
        sha, sp = _store_blob(store, content)
        shas[rel] = sha
        rows.append({"rel_path": rel, "sha256": sha, "storage_path": sp, "language": "py",
                     "role": "entry" if i == 0 else "support", "size": len(content)})
    cat.set_artifact(pid, rows[0]["sha256"], rows[0]["storage_path"])   # entry → makes _allow_code pass
    cat.set_files(pid, rows)
    cat.commit()
    return be, shas


def test_fullen_expands_to_complete_fileset_full(tmp_path) -> None:
    # The repo has 3 files but the model only fetched the entry (truncated). Fullening must
    # render ALL 3, each full — not just the fetched subset.
    entry = "Z" * (_MAX_SET_FILE_CHARS * 2)   # entry > per-file cap
    files = {"pkg/cli.py": entry, "pkg/sqli.py": "def sqli(): ...\n", "pkg/shell.py": "def sh(): ...\n"}
    be, shas = _repo_backend(tmp_path, "CVE-2099-0006", "emb/repo", files)
    # evidence as the ledger holds it: ONLY the entry, truncated
    evidence = [{"source_files": [{"path": "pkg/cli.py", "sha256": shas["pkg/cli.py"],
                                   "role": "entry", "code": entry[:_MAX_SET_FILE_CHARS], "truncated": True}]}]
    out = asyncio.run(_fullen_evidence_for_display(evidence, be))
    sf = out[0]["source_files"]
    got = {f["path"]: f["code"] for f in sf}
    assert set(got) == set(files)                     # all 3 files present
    assert got["pkg/cli.py"] == entry                 # entry now FULL
    assert got["pkg/sqli.py"] == "def sqli(): ...\n"  # sibling the model never fetched
    assert all(f["truncated"] is False for f in sf)


def test_fullen_swaps_singlefile_source_code_for_full(tmp_path) -> None:
    big = "S" * (_MAX_SET_FILE_CHARS + 100)
    be, cat, store = _backend(tmp_path)
    cat.upsert(NormalizedPoc(source="poc_github", source_ref="solo/x", kind="exploit",
                             title="solo/x", cve_ids=["CVE-2099-0008"], verified=True, rank=1))
    pid = _poc_id(cat, "solo/x")
    sha, sp = _store_blob(store, big)
    cat.set_artifact(pid, sha, sp)
    cat.commit()
    evidence = [{"sha256": sha, "source_code": big[:_MAX_SET_FILE_CHARS], "code_truncated": True}]
    out = asyncio.run(_fullen_evidence_for_display(evidence, be))
    assert out[0]["source_code"] == big and out[0]["code_truncated"] is False


def test_fullen_respects_budget_keeps_truncated(tmp_path) -> None:
    big = "Y" * 5000
    be, shas = _repo_backend(tmp_path, "CVE-2099-0007", "bud/repo", {"e.py": big})
    trunc = big[:100]
    evidence = [{"source_files": [{"path": "e.py", "sha256": shas["e.py"], "code": trunc, "truncated": True}]}]
    out = asyncio.run(_fullen_evidence_for_display(evidence, be, budget=100))  # too small
    assert out[0]["source_files"][0]["code"] == trunc        # unchanged (kept truncated)


def test_fullen_none_backend_is_noop() -> None:
    evidence = [{"source_files": [{"path": "x", "sha256": "a" * 64, "code": "t", "truncated": True}]}]
    assert asyncio.run(_fullen_evidence_for_display(evidence, None)) == evidence
