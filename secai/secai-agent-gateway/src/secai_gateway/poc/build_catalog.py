"""(Re)build the unified PoC catalog from enabled collectors — reproducible & documented.

    python -m secai_gateway.poc.build_catalog [--reset] [sources...]

`--reset` rebuilds from an empty DB (deterministic state, not a hand-mutated one). After
ingest, a documented PRECISION prune runs on the community GitHub sources (poc_github /
trickest), because a raw GitHub↔CVE mention is NOT a per-CVE PoC. We drop, with reasons:

  1. aggregators/lists/scanners/mirrors — one repo linked to > MAX_CVES_PER_REPO CVEs
     (verified by sampling: nvd-json-data-feeds 21k, nuclei-templates mirror 21k, cvemon,
     PoC-in-GitHub index itself, CVE-LIST, …). Real PoC repos target 1–few CVEs.
  2. known auto-spam owners (e.g. Live-Hack-CVE bulk-generates empty CVE repos).
  3. bare generic repo names ("cve"/"poc"/"exploit"/"security"/… with no CVE-id/product) —
     junk/personal dumps, not a specific PoC.

Verified sources (exploitdb/metasploit/nuclei/vulhub) are never pruned. This keeps
precision high for dataset generation; provenance is retained so the rule can be re-tuned.
"""
from __future__ import annotations

import re
from typing import Any

from . import fetch_github
from .backend import LocalPocBackend

MAX_CVES_PER_REPO = 5
SPAM_OWNERS = {"live-hack-cve"}
GENERIC_NAMES = {"cve", "cves", "poc", "pocs", "exploit", "exploits", "exp", "security",
                 "vulnerability", "vulnerabilities", "vuln", "vulns", "writeup", "writeups",
                 "bug-bounty", "bugbounty", "research", "pentest", "payloads", "hacking",
                 "test", "demo", "poc-exploit", "cve-poc", "poc-cve"}
_GITHUB_SOURCES = ("poc_github",)  # trickest also yields source='poc_github'


def prune_github_noise(db) -> dict[str, int]:
    """Drop low-precision community GitHub rows. Reproducible (runs every build)."""
    marks = ",".join("?" * len(_GITHUB_SOURCES))
    # 1) aggregators by CVE-fan-out
    agg = {r[0] for r in db.execute(
        f"SELECT p.id FROM poc p JOIN poc_cve v ON v.poc_id=p.id WHERE p.source IN ({marks}) "
        "GROUP BY p.id HAVING count(*) > ?", (*_GITHUB_SOURCES, MAX_CVES_PER_REPO)).fetchall()}
    # 2) spam owners + 3) bare generic repo names (source_ref = 'owner/repo')
    spam_name = set()
    for pid, ref in db.execute(
            f"SELECT id, source_ref FROM poc WHERE source IN ({marks})", _GITHUB_SOURCES).fetchall():
        owner, _, repo = str(ref).partition("/")
        if owner.lower() in SPAM_OWNERS or repo.lower() in GENERIC_NAMES:
            spam_name.add(pid)
    drop = agg | spam_name
    if drop:
        rows = [(i,) for i in drop]
        db.executemany("DELETE FROM poc_cve WHERE poc_id=?", rows)
        db.executemany("DELETE FROM poc_fts WHERE rowid=?", rows)
        db.executemany("DELETE FROM poc WHERE id=?", rows)
        db.commit()
    return {"aggregators": len(agg), "spam_or_generic": len(spam_name), "dropped_total": len(drop)}


def dedup_fork_families(db) -> dict[str, int]:
    """TIER-1 dedup (pre-fetch, metadata-only). Within a CVE, community GitHub rows whose repo
    NAME matches (case-insensitive) but whose OWNER differs are a probable *fork family*: keep
    the highest-starred one as the fetch candidate and mark the rest `duplicate_of` it, so the
    fetch budget isn't spent re-downloading the same PoC under a different owner. The winner
    absorbs ALL of each loser's CVE links first, so demoting a fork never drops coverage.
    Reversible (rows are flagged, never deleted). A row that wins any bucket is never demoted.
    Heuristic: two unrelated repos that share a name AND a CVE could be merged — mitigated by
    the same-CVE constraint and the earlier generic-name prune; kept auditable via provenance."""
    from collections import defaultdict
    buckets: dict[tuple[str, str], list[tuple[int, int, str]]] = defaultdict(list)
    rows = db.execute(
        "SELECT p.id, p.source_ref, c.cve_id, "
        "  COALESCE(CAST(json_extract(p.provenance,'$.stars') AS INTEGER),0) AS stars "
        "FROM poc p JOIN poc_cve c ON c.poc_id=p.id "
        "WHERE p.source='poc_github' AND p.duplicate_of IS NULL").fetchall()
    for r in rows:
        owner, _, repo = str(r["source_ref"]).partition("/")
        buckets[(r["cve_id"], repo.lower())].append((r["stars"], r["id"], owner.lower()))

    winners: set[int] = set()
    loser_to_winner: dict[int, int] = {}
    family_count = 0
    for members in buckets.values():
        if len({owner for _, _, owner in members}) < 2:
            continue  # single row or same owner — not a fork family
        family_count += 1
        _stars, winner_id, _owner = max(members, key=lambda m: (m[0], -m[1]))  # most stars, low id
        winners.add(winner_id)
        for _s, pid, _o in members:
            if pid != winner_id and pid not in loser_to_winner:
                loser_to_winner[pid] = winner_id

    marked = 0
    for loser_id, winner_id in loser_to_winner.items():
        if loser_id in winners:
            continue  # canonical in another bucket — never demote a winner
        db.execute("INSERT OR IGNORE INTO poc_cve(poc_id, cve_id) "
                   "SELECT ?, cve_id FROM poc_cve WHERE poc_id=?", (winner_id, loser_id))
        cur = db.execute("UPDATE poc SET duplicate_of=? WHERE id=? AND duplicate_of IS NULL",
                         (winner_id, loser_id))
        marked += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    db.commit()
    return {"fork_families": family_count, "marked_dup": marked}


def backfill_offline_hashes(be) -> int:
    """TIER-2 precondition: persist a sha256 for every locally-stored artifact so content
    duplicates can be found across sources. poc_github rows are hashed by the fetcher; the
    offline sources (exploitdb/metasploit/nuclei/vulhub) only set `artifact_path` at collect
    time (sha256 stayed lazy at serve time), so hash them here off the local file. Reuses the
    backend's path resolver (root-escape guarded). Rows whose file is missing are left as-is."""
    import hashlib
    db = be.catalog._db
    rows = db.execute("SELECT id, source, artifact_path FROM poc "
                      "WHERE artifact_path IS NOT NULL AND artifact_sha256 IS NULL").fetchall()
    n = 0
    for r in rows:
        try:
            path = be._artifact_path({"source": r["source"], "artifact_path": r["artifact_path"]})
            blob = path.read_bytes()
        except Exception:
            continue
        sha = hashlib.sha256(blob).hexdigest()
        db.execute("UPDATE poc SET artifact_sha256=? WHERE id=?", (sha, r["id"]))
        db.execute("INSERT OR IGNORE INTO artifact(sha256,size,storage_path,stored,first_source) "
                   "VALUES(?,?,?,0,?)", (sha, len(blob), r["artifact_path"], r["source"]))
        n += 1
    db.commit()
    return n


def merge_duplicate_artifacts(db) -> dict[str, int]:
    """TIER-2 dedup (post-fetch, exact content hash). Group all still-canonical rows by
    `artifact_sha256`; rows sharing a hash are byte-identical PoCs (even across different
    sources). Pick ONE canonical per group — most authoritative first: verified, then higher
    source rank, then more stars, then most-recently collected, id as a deterministic
    tiebreak. The canonical absorbs the others' CVE links; the rest are marked `duplicate_of`.
    Preferring the higher-rank/redistributable source means fetch() can still return real
    source_code for the surviving row. v1 is EXACT-hash only (no near-duplicate similarity)."""
    from collections import defaultdict
    groups: dict[str, list[int]] = defaultdict(list)
    # ORDER BY groups each hash together and puts the canonical row FIRST within its group.
    rows = db.execute(
        "SELECT id, artifact_sha256 FROM poc "
        "WHERE artifact_sha256 IS NOT NULL AND duplicate_of IS NULL "
        "ORDER BY artifact_sha256, verified DESC, rank DESC, "
        "  COALESCE(CAST(json_extract(provenance,'$.stars') AS INTEGER),0) DESC, "
        "  date_collected DESC, id ASC").fetchall()
    for r in rows:
        groups[r["artifact_sha256"]].append(r["id"])

    merged = dup_groups = 0
    for ids in groups.values():
        if len(ids) < 2:
            continue
        dup_groups += 1
        canonical, *losers = ids  # first = canonical (ORDER BY above)
        for loser_id in losers:
            db.execute("INSERT OR IGNORE INTO poc_cve(poc_id, cve_id) "
                       "SELECT ?, cve_id FROM poc_cve WHERE poc_id=?", (canonical, loser_id))
            cur = db.execute("UPDATE poc SET duplicate_of=? WHERE id=? AND duplicate_of IS NULL",
                             (canonical, loser_id))
            merged += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    db.commit()
    return {"hash_groups": dup_groups, "merged": merged}


def dedup_same_repo(db) -> dict[str, int]:
    """TIER-3 dedup (post-fetch, repo identity). A fetched PoC retargets ``source_ref`` from
    the link-only ``owner/repo`` to the picked code file ``owner/repo/path/file`` (see
    fetch_github.py's ``set_artifact``), so a later re-collect that re-yields the link-only
    ``owner/repo`` no longer collides on UNIQUE(source, source_ref) and inserts a SECOND row
    for the SAME repo: one fetched (code-path ref), one link-only (repo-root ref). Tier-1
    can't see it (needs a DIFFERENT owner — fork family), Tier-2 can't see it (needs an exact
    ``artifact_sha256`` on BOTH rows — the link-only twin has none). Result: ``github_candidates``
    re-downloads an already-fetched repo and search returns the same PoC twice.

    Groups still-canonical ``poc_github`` rows by their normalized repo identity
    (``provenance.repo``, always ``owner/repo`` regardless of the code-path retarget). A
    group is only collapsed when it contains BOTH a fetched row and a link-only row — the
    twin signature. The highest-starred fetched row is canonical; every link-only row is
    marked ``duplicate_of`` it, absorbing its CVE links first. Groups that are all-fetched
    (two legitimately distinct PoCs under one repo) or all-link-only (impossible — the
    collector's ON CONFLICT merges them) are left to the other tiers. Reversible (flag only,
    never deletes)."""
    from collections import defaultdict

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = db.execute(
        "SELECT p.id, p.artifact_path, "
        "  COALESCE(CAST(json_extract(p.provenance,'$.stars') AS INTEGER),0) AS stars, "
        "  COALESCE(json_extract(p.provenance,'$.repo'),'') AS repo "
        "FROM poc p "
        "WHERE p.source='poc_github' AND p.duplicate_of IS NULL").fetchall()
    for r in rows:
        repo = str(r["repo"]).strip().lower()
        if not repo:
            continue
        groups[repo].append({"id": r["id"], "stars": r["stars"],
                             "fetched": r["artifact_path"] is not None})

    marked = same_repo_groups = 0
    for members in groups.values():
        fetched = [m for m in members if m["fetched"]]
        link_only = [m for m in members if not m["fetched"]]
        if not fetched or not link_only:
            continue  # twin needs both; leave the rest to tier-1/tier-2
        same_repo_groups += 1
        winner = max(fetched, key=lambda m: (m["stars"], -m["id"]))
        for m in link_only:
            db.execute("INSERT OR IGNORE INTO poc_cve(poc_id, cve_id) "
                       "SELECT ?, cve_id FROM poc_cve WHERE poc_id=?", (winner["id"], m["id"]))
            cur = db.execute("UPDATE poc SET duplicate_of=? WHERE id=? AND duplicate_of IS NULL",
                             (winner["id"], m["id"]))
            marked += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    db.commit()
    return {"same_repo_groups": same_repo_groups, "marked_dup": marked}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="(Re)build the unified PoC catalog.")
    ap.add_argument("sources", nargs="*", help="restrict to these collector names (default: all)")
    ap.add_argument("--reset", action="store_true", help="rebuild from an empty DB")
    ap.add_argument("--fetch-limit", type=int, default=0,
                    help="raw-tarball-fetch up to N pending github PoCs after dedup (0 = skip)")
    ap.add_argument("--min-stars", type=int, default=5, help="min stars to fetch")
    ap.add_argument("--fetch-timeout", type=int, default=fetch_github._REPO_TIMEOUT)
    a = ap.parse_args()

    be = LocalPocBackend()
    if a.reset:
        for suf in ("", "-wal", "-shm"):
            p = be.catalog.path.with_name(be.catalog.path.name + suf)
            if p.exists():
                p.unlink()
        be = LocalPocBackend()  # fresh connection on the empty DB
        print("reset: rebuilding from an empty catalog")
    print(f"building catalog at {be.catalog.path}")

    counts = be.rebuild(a.sources or None)
    for name, n in counts.items():
        print(f"  {name}: {n}")

    print(f"  prune github noise: {prune_github_noise(be.catalog._db)}")
    print(f"  dedup fork families: {dedup_fork_families(be.catalog._db)}")

    if a.fetch_limit > 0:
        print(f"  fetching up to {a.fetch_limit} github PoCs (min_stars={a.min_stars})...")
        fetch_github.run(a.fetch_limit, a.min_stars, a.fetch_timeout)

    print(f"  backfill offline hashes: {backfill_offline_hashes(be)}")
    print(f"  merge duplicate artifacts: {merge_duplicate_artifacts(be.catalog._db)}")
    print(f"  dedup same repo: {dedup_same_repo(be.catalog._db)}")

    canonical = be.catalog._db.execute(
        "SELECT COUNT(*) FROM poc WHERE duplicate_of IS NULL").fetchone()[0]
    print(f"catalog total: {be.catalog.count()} rows ({canonical} canonical after dedup)")


if __name__ == "__main__":
    main()
