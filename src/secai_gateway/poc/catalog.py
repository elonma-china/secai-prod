"""Unified PoC catalog — normalized multi-source store (SQLite + FTS5).

Sources (exploitdb/metasploit/nuclei/poc_github/vulhub/vendor) are collected into one
`poc` table (many-to-many with CVEs), deduped, ranked. The backend queries this; adding a
source never changes the tool contract. See docs/unified-poc-db-design.md.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .models import NormalizedPoc

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS poc (
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
  platform TEXT, language TEXT, verified INTEGER DEFAULT 0, rank INTEGER DEFAULT 0,
  url TEXT, license TEXT, redistributable INTEGER DEFAULT 0,
  artifact_sha256 TEXT, artifact_path TEXT,
  date_published TEXT, date_collected TEXT, provenance TEXT,
  -- NULL = canonical/standalone; non-NULL = a duplicate (fork-family or same content
  -- hash) pointing at the canonical poc.id. Duplicates are NEVER deleted (provenance is
  -- kept so the heuristic can be re-tuned); search/pending_github filter them out.
  duplicate_of INTEGER REFERENCES poc(id),
  UNIQUE(source, source_ref)
);
CREATE TABLE IF NOT EXISTS poc_cve (poc_id INTEGER, cve_id TEXT, PRIMARY KEY(poc_id, cve_id));
CREATE INDEX IF NOT EXISTS idx_poc_cve_cve ON poc_cve(cve_id);
CREATE TABLE IF NOT EXISTS artifact (
  sha256 TEXT PRIMARY KEY, size INTEGER, language TEXT, license TEXT,
  stored INTEGER DEFAULT 0, storage_path TEXT, first_source TEXT
);
CREATE TABLE IF NOT EXISTS collector_run (
  id INTEGER PRIMARY KEY, source TEXT, started TEXT, finished TEXT,
  added INTEGER, updated INTEGER, errors INTEGER, note TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS poc_fts USING fts5(title, description);
"""


class PocCatalog:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        # WAL: concurrent readers (the serving gateway) + one writer (a rebuild) coexist;
        # busy_timeout retries instead of raising "database is locked" under contention.
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        # Lightweight migration for catalogs built before `duplicate_of` existed: the CREATE
        # TABLE above is IF NOT EXISTS, so a pre-existing `poc` table won't gain the column.
        # Fresh DBs already have it from _SCHEMA; this ALTER only backfills older ones.
        try:
            self._db.execute("ALTER TABLE poc ADD COLUMN duplicate_of INTEGER")
        except sqlite3.OperationalError:
            pass  # column already present
        self._db.commit()

    # ── ingest (collectors) ─────────────────────────────────────────────────
    def upsert(self, poc: NormalizedPoc) -> None:
        cur = self._db.execute(
            """INSERT INTO poc(source,source_ref,kind,title,description,platform,language,
                 verified,rank,url,license,redistributable,artifact_sha256,artifact_path,
                 date_published,date_collected,provenance)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?)
               ON CONFLICT(source,source_ref) DO UPDATE SET
                 title=excluded.title, description=excluded.description, rank=excluded.rank,
                 -- preserve a previously sandbox-fetched artifact when the collector re-yields
                 -- NULL (link-only sources) — re-collect must NOT wipe fetch work.
                 artifact_sha256=COALESCE(excluded.artifact_sha256, poc.artifact_sha256),
                 artifact_path=COALESCE(excluded.artifact_path, poc.artifact_path),
                 date_collected=excluded.date_collected
               RETURNING id""",
            (poc.source, poc.source_ref, poc.kind, poc.title, poc.description, poc.platform,
             poc.language, int(poc.verified), poc.rank, poc.url, poc.license,
             int(poc.redistributable), poc.artifact_sha256, poc.artifact_path,
             poc.date_published, json.dumps(poc.provenance or {})),
        )
        poc_id = cur.fetchone()[0]
        # standalone FTS5 (no external content) → manage rows by rowid: delete-then-insert
        # keeps re-collect idempotent (UPSERT isn't supported on FTS5 virtual tables).
        self._db.execute("DELETE FROM poc_fts WHERE rowid=?", (poc_id,))
        self._db.execute("INSERT INTO poc_fts(rowid,title,description) VALUES(?,?,?)",
                         (poc_id, poc.title, poc.description))
        for cve in {c.upper() for c in poc.cve_ids}:
            self._db.execute("INSERT OR IGNORE INTO poc_cve(poc_id,cve_id) VALUES(?,?)", (poc_id, cve))
        if poc.artifact_sha256:
            self._db.execute(
                "INSERT OR IGNORE INTO artifact(sha256,storage_path,stored,license,first_source) "
                "VALUES(?,?,?,?,?)",
                (poc.artifact_sha256, poc.artifact_path, int(bool(poc.artifact_path)),
                 poc.license, poc.source))

    def commit(self) -> None:
        self._db.commit()

    def log_run(self, source: str, started: str, added: int, updated: int, errors: int, note: str = "") -> None:
        self._db.execute(
            "INSERT INTO collector_run(source,started,finished,added,updated,errors,note) "
            "VALUES(?,?,datetime('now'),?,?,?,?)", (source, started, added, updated, errors, note))
        self._db.commit()

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM poc").fetchone()[0]

    # ── query (backend) ─────────────────────────────────────────────────────
    def _rows_to_dicts(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            cves = [x[0] for x in self._db.execute(
                "SELECT cve_id FROM poc_cve WHERE poc_id=? ORDER BY cve_id", (r["id"],)).fetchall()]
            d = dict(r); d["cve_ids"] = cves
            out.append(d)
        return out

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(limit, 50))
        cves = {m.upper() for m in _CVE.findall(query)}
        if cves:  # CVE-exact path
            marks = ",".join("?" * len(cves))
            rows = self._db.execute(
                f"""SELECT p.* FROM poc p JOIN poc_cve c ON c.poc_id=p.id
                    WHERE c.cve_id IN ({marks}) AND p.duplicate_of IS NULL
                    ORDER BY p.rank DESC, p.verified DESC LIMIT ?""",
                (*cves, limit)).fetchall()
            return self._rows_to_dicts(rows)
        # keyword FTS path (sanitize to a safe MATCH expression)
        terms = re.findall(r"[A-Za-z0-9_.\-]+", query)[:8]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        # Rank by TEXT RELEVANCE (FTS5 bm25 — more negative = better match; docs matching
        # more/rarer terms score higher) with a gentle source-tier boost, so the most
        # on-topic PoC surfaces first instead of ordering purely by source. Fetch a wider
        # candidate window then trim, so bm25 (not the LIMIT) decides the top hits.
        rows = self._db.execute(
            """SELECT p.* FROM poc p JOIN poc_fts f ON f.rowid=p.id
               WHERE poc_fts MATCH ? AND p.duplicate_of IS NULL
               ORDER BY bm25(poc_fts) - (p.rank * 0.15) ASC, p.verified DESC
               LIMIT ?""",
            (match, limit)).fetchall()
        return self._rows_to_dicts(rows)

    def get(self, poc_id: int) -> dict[str, Any] | None:
        # Fetches by exact id (used to resolve a search-issued ref). Duplicates are excluded
        # from search(), so a ref never points at a duplicate row — no redirect needed here.
        r = self._db.execute("SELECT * FROM poc WHERE id=?", (poc_id,)).fetchone()
        if not r:
            return None
        return self._rows_to_dicts([r])[0]

    # ── sandbox fetcher support (link-only sources) ─────────────────────────
    def pending_github(self, limit: int, min_stars: int = 0) -> list[sqlite3.Row]:
        """poc_github rows not yet fetched, highest-star first. Fork-family duplicates
        (marked by dedup_fork_families before fetch) are skipped so we don't spend the
        fetch budget re-downloading the same PoC under a different owner."""
        return self._db.execute(
            "SELECT id, source_ref, url, provenance FROM poc "
            "WHERE source='poc_github' AND artifact_path IS NULL AND duplicate_of IS NULL "
            "AND CAST(json_extract(provenance,'$.stars') AS INTEGER) >= ? "
            "ORDER BY CAST(json_extract(provenance,'$.stars') AS INTEGER) DESC LIMIT ?",
            (min_stars, limit)).fetchall()

    def set_artifact(self, poc_id: int, sha256: str, rel_path: str,
                     license: str | None = None, language: str | None = None) -> None:
        """Attach a fetched, content-addressed artifact to a poc row (make it fetchable offline).
        `language` = the picked file's extension, so fetch reports it (store name is hash-only)."""
        sets = ["artifact_sha256=?", "artifact_path=?", "redistributable=0"]
        vals: list[Any] = [sha256, rel_path]
        if license:
            sets.append("license=?"); vals.append(license)
        if language:
            sets.append("language=?"); vals.append(language)
        vals.append(poc_id)
        self._db.execute(f"UPDATE poc SET {', '.join(sets)} WHERE id=?", vals)
        self._db.execute(
            "INSERT OR IGNORE INTO artifact(sha256,storage_path,stored,language,first_source) "
            "VALUES(?,?,1,?,'poc_github')", (sha256, rel_path, language))
        self._db.commit()
