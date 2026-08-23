"""Dedup/merge heuristics — tier-1 fork-family (metadata) and tier-2 content-hash (cross
source). All rows are hand-built via PocCatalog/NormalizedPoc like tests/test_poc_core.py;
no network, no real source repos."""
from __future__ import annotations

import hashlib
from pathlib import Path

from secai_gateway.poc.backend import LocalPocBackend
from secai_gateway.poc.build_catalog import (
    backfill_offline_hashes,
    dedup_fork_families,
    dedup_same_repo,
    merge_duplicate_artifacts,
)
from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.models import NormalizedPoc


def _gh(owner_repo: str, cves: list[str], stars: int) -> NormalizedPoc:
    return NormalizedPoc(
        source="poc_github", source_ref=owner_repo, kind="exploit",
        title=owner_repo.split("/")[-1], cve_ids=cves,
        url=f"https://github.com/{owner_repo}", license="varies", redistributable=False, rank=5,
        provenance={"collector": "poc_github", "repo": owner_repo, "stars": stars,
                    "default_branch": "master"})


def _dupof(cat: PocCatalog, source_ref: str) -> int | None:
    return cat._db.execute(
        "SELECT duplicate_of FROM poc WHERE source_ref=?", (source_ref,)).fetchone()[0]


def _id(cat: PocCatalog, source_ref: str) -> int:
    return cat._db.execute("SELECT id FROM poc WHERE source_ref=?", (source_ref,)).fetchone()[0]


def test_fork_family_keeps_highest_star(tmp_path: Path) -> None:
    cat = PocCatalog(tmp_path / "c.db")
    for p in (_gh("alice/log4shell", ["CVE-2021-44228"], 100),
              _gh("bob/log4shell", ["CVE-2021-44228"], 5),
              _gh("carol/log4shell", ["CVE-2021-44228"], 50)):
        cat.upsert(p)
    cat.commit()

    res = dedup_fork_families(cat._db)
    assert res == {"fork_families": 1, "marked_dup": 2}

    canon = _id(cat, "alice/log4shell")
    assert _dupof(cat, "alice/log4shell") is None
    assert _dupof(cat, "bob/log4shell") == canon
    assert _dupof(cat, "carol/log4shell") == canon

    # search surfaces only the canonical fork
    hits = cat.search("CVE-2021-44228")
    assert [h["source_ref"] for h in hits] == ["alice/log4shell"]


def test_fork_family_absorbs_exclusive_cve(tmp_path: Path) -> None:
    # bob/tool is demoted (loses the CVE-1 fork family) but also uniquely covers CVE-2 —
    # the winner must absorb CVE-2 so coverage isn't silently dropped.
    cat = PocCatalog(tmp_path / "c.db")
    cat.upsert(_gh("alice/tool", ["CVE-2021-0001"], 100))
    cat.upsert(_gh("bob/tool", ["CVE-2021-0001", "CVE-2021-0002"], 5))
    cat.commit()

    dedup_fork_families(cat._db)

    canon = _id(cat, "alice/tool")
    cves = {r[0] for r in cat._db.execute("SELECT cve_id FROM poc_cve WHERE poc_id=?", (canon,))}
    assert cves == {"CVE-2021-0001", "CVE-2021-0002"}
    assert _dupof(cat, "bob/tool") == canon
    # the exclusive CVE is still reachable, now via the canonical row
    hits = cat.search("CVE-2021-0002")
    assert [h["source_ref"] for h in hits] == ["alice/tool"]


def test_different_owner_same_repo_but_different_cve_not_merged(tmp_path: Path) -> None:
    # same repo name, different owners, but NO shared CVE → not a fork family, both kept.
    cat = PocCatalog(tmp_path / "c.db")
    cat.upsert(_gh("alice/scanner", ["CVE-2020-1111"], 10))
    cat.upsert(_gh("bob/scanner", ["CVE-2020-2222"], 10))
    cat.commit()

    res = dedup_fork_families(cat._db)
    assert res == {"fork_families": 0, "marked_dup": 0}
    assert _dupof(cat, "alice/scanner") is None
    assert _dupof(cat, "bob/scanner") is None


def test_backfill_and_content_merge_cross_source(tmp_path: Path) -> None:
    body = b"import socket\n# shared PoC body\n"
    sha = hashlib.sha256(body).hexdigest()

    edb_root = tmp_path / "exploitdb"
    (edb_root / "exploits").mkdir(parents=True)
    (edb_root / "exploits" / "1.py").write_bytes(body)

    gh_root = tmp_path / "store"
    (gh_root / sha[:2]).mkdir(parents=True)
    (gh_root / sha[:2] / sha).write_bytes(body)

    cat = PocCatalog(tmp_path / "c.db")
    # offline exploitdb row: verified, higher rank, sha256 NULL (must be backfilled)
    cat.upsert(NormalizedPoc(
        source="exploitdb", source_ref="EDB-1", kind="exploit", title="edb",
        cve_ids=["CVE-2021-44228"], verified=True, rank=25, redistributable=True,
        artifact_path="exploits/1.py", language="py"))
    # community github row: already fetched (sha256 + path set), lower rank
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="alice/x", kind="exploit", title="gh",
        cve_ids=["CVE-2021-44228"], rank=5, redistributable=False,
        artifact_sha256=sha, artifact_path=f"{sha[:2]}/{sha}", language="py",
        provenance={"stars": 10}))
    cat.commit()

    be = LocalPocBackend(catalog=cat, source_roots={"exploitdb": edb_root, "poc_github": gh_root})

    assert backfill_offline_hashes(be) == 1  # only the exploitdb row needed hashing
    edb_sha = cat._db.execute(
        "SELECT artifact_sha256 FROM poc WHERE source='exploitdb'").fetchone()[0]
    assert edb_sha == sha

    res = merge_duplicate_artifacts(cat._db)
    assert res == {"hash_groups": 1, "merged": 1}

    # canonical = exploitdb (verified + higher rank beats the github row); github demoted to it
    assert _dupof(cat, "EDB-1") is None
    assert _dupof(cat, "alice/x") == _id(cat, "EDB-1")

    hits = cat.search("CVE-2021-44228")
    assert [h["source"] for h in hits] == ["exploitdb"]


def test_dedup_same_repo_twin(tmp_path: Path) -> None:
    # Same repo, two rows: the fetched one (source_ref retargeted to the code file) and the
    # link-only twin (source_ref still the repo root) re-inserted by a later re-collect.
    # Tier-1 misses it (same owner), tier-2 misses it (link-only has no hash).
    cat = PocCatalog(tmp_path / "c.db")
    body = b"import socket\n# real PoC\n"
    sha = hashlib.sha256(body).hexdigest()
    # fetched twin — source_ref points at the picked code file (as set_artifact does)
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="alice/log4shell/main.py", kind="exploit",
        title="log4shell", cve_ids=["CVE-2021-44228"], rank=5, redistributable=False,
        artifact_sha256=sha, artifact_path=f"{sha[:2]}/{sha}", language="py",
        provenance={"repo": "alice/log4shell", "stars": 100, "collector": "poc_github"}))
    # link-only twin — same repo, no artifact
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="alice/log4shell", kind="exploit",
        title="log4shell", cve_ids=["CVE-2021-44228"], rank=5, redistributable=False,
        provenance={"repo": "alice/log4shell", "stars": 100, "collector": "poc_github"}))
    cat.commit()

    res = dedup_same_repo(cat._db)
    assert res == {"same_repo_groups": 1, "marked_dup": 1}

    fetched_id = _id(cat, "alice/log4shell/main.py")
    assert _dupof(cat, "alice/log4shell/main.py") is None  # fetched is canonical
    assert _dupof(cat, "alice/log4shell") == fetched_id

    # search surfaces only the canonical fetched row
    hits = cat.search("CVE-2021-44228")
    assert [h["source_ref"] for h in hits] == ["alice/log4shell/main.py"]


def test_dedup_same_repo_absorbs_exclusive_cve(tmp_path: Path) -> None:
    # The link-only twin may carry a CVE the fetched row lacks — the winner must absorb it.
    cat = PocCatalog(tmp_path / "c.db")
    body = b"payload = 1\n"
    sha = hashlib.sha256(body).hexdigest()
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="bob/exploit/poc.py", kind="exploit",
        title="exploit", cve_ids=["CVE-2021-0001"], rank=5, redistributable=False,
        artifact_sha256=sha, artifact_path=f"{sha[:2]}/{sha}", language="py",
        provenance={"repo": "bob/exploit", "stars": 10, "collector": "poc_github"}))
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="bob/exploit", kind="exploit",
        title="exploit", cve_ids=["CVE-2021-0001", "CVE-2021-0002"], rank=5, redistributable=False,
        provenance={"repo": "bob/exploit", "stars": 10, "collector": "poc_github"}))
    cat.commit()

    dedup_same_repo(cat._db)

    fetched_id = _id(cat, "bob/exploit/poc.py")
    cves = {r[0] for r in cat._db.execute("SELECT cve_id FROM poc_cve WHERE poc_id=?", (fetched_id,))}
    assert cves == {"CVE-2021-0001", "CVE-2021-0002"}
    assert _dupof(cat, "bob/exploit") == fetched_id
    hits = cat.search("CVE-2021-0002")
    assert [h["source_ref"] for h in hits] == ["bob/exploit/poc.py"]


def test_dedup_same_repo_leaves_all_fetched_or_all_link_only(tmp_path: Path) -> None:
    # Two FETCHED rows under one repo = two distinct PoCs — must NOT be merged (no link-only
    # twin to demote).
    cat = PocCatalog(tmp_path / "c.db")
    body = b"x = 1\n"
    sha = hashlib.sha256(body).hexdigest()
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="carol/repo/a.py", kind="exploit",
        title="a", cve_ids=["CVE-2020-1111"], rank=5, redistributable=False,
        artifact_sha256=sha, artifact_path=f"{sha[:2]}/{sha}", language="py",
        provenance={"repo": "carol/repo", "stars": 5, "collector": "poc_github"}))
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="carol/repo/b.py", kind="exploit",
        title="b", cve_ids=["CVE-2020-2222"], rank=5, redistributable=False,
        artifact_sha256=sha, artifact_path=f"{sha[:2]}/{sha}", language="py",
        provenance={"repo": "carol/repo", "stars": 5, "collector": "poc_github"}))
    cat.commit()

    res = dedup_same_repo(cat._db)
    assert res == {"same_repo_groups": 0, "marked_dup": 0}
    assert _dupof(cat, "carol/repo/a.py") is None
    assert _dupof(cat, "carol/repo/b.py") is None
