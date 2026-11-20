"""PoC artifact store reads (the /ai/exploit Section-1 display path was removed 2026-11-20).

The FE renders Section 1 from `description`/`exploit_guide`, which the injector builds from
the run's evidence — whose code was capped at fetch time for the LLM context. At injection
time (AFTER the model ran) `_fullen_evidence_for_display` swaps each file's truncated code for
its FULL store bytes, so the DISPLAYED PoC is complete while the model context stays bounded.
The load-bearing assertion: a file larger than the render cap comes back complete.
"""
from __future__ import annotations

import asyncio
import hashlib

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


def test_locate_falls_back_to_poc_github_store_on_source_root_mismatch(tmp_path) -> None:
    """Regression guard (2026-08 research pass): `_locate`'s poc_github-store fallback is
    real, shipped code (not a TODO) fixing a live incident — grounding a poc row with real
    fetched bytes writes into the shared content-addressed store regardless of the row's own
    `source`, so a `source='ghsa'` row whose bytes only exist in that shared store (not under
    `roots['ghsa']`) used to silently "not be found on disk" even though `_allow_code`
    permitted it (observed live: 9/44 grounded CVEs landed on a pre-existing ghsa/poc_github
    row and fell through to T2 synthesis instead of T1 real code purely from this path
    mismatch). No prior test exercised source_roots that actually DIFFER between the row's own
    source and poc_github — every existing fixture built both from the SAME dir."""
    ghsa_root = tmp_path / "ghsa_root"  # the row's OWN root — deliberately has NOTHING in it
    ghsa_root.mkdir()
    store = tmp_path / "store"          # the shared content-addressed store
    store.mkdir()
    cat = PocCatalog(tmp_path / "cat.db")
    be = LocalPocBackend(catalog=cat, source_roots={"ghsa": ghsa_root, "poc_github": store})

    cve = "CVE-2099-0099"
    cat.upsert(NormalizedPoc(source="ghsa", source_ref="advisory/GHSA-xxxx", kind="exploit",
                             title="advisory", cve_ids=[cve], verified=True, rank=10))
    pid = _poc_id(cat, "advisory/GHSA-xxxx")
    sha, storage_path = _store_blob(store, "print('grounded real poc')\n")
    cat.set_artifact(pid, sha, storage_path)  # artifact_path recorded, bytes only in `store`
    cat.commit()

    found = be._locate(be.roots.get("ghsa"), storage_path)
    assert found is not None, "must fall back to roots['poc_github'] when the row's own root misses"
    assert found.read_bytes() == b"print('grounded real poc')\n"
    # sanity: confirm the row's OWN root genuinely does NOT have it (proves this is testing
    # the fallback, not accidentally finding it under ghsa_root itself)
    assert not (ghsa_root / storage_path).is_file()
