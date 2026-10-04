"""SQLite queue with transactional admission, claim fencing and logical expiry."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .settings import TranslationSettings
from .types import TranslationError

logger = logging.getLogger(__name__)


class JobStore:
    def __init__(self, settings: TranslationSettings):
        self.settings = settings
        self.db_path = Path(settings.db_path)
        self.artifact_dir = Path(settings.artifact_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path.parent.chmod(0o700)
        self.artifact_dir.chmod(0o700)
        with self._tx() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, extension TEXT NOT NULL,
              status TEXT NOT NULL,
              stage TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
              total INTEGER, source_lang TEXT NOT NULL, target_lang TEXT NOT NULL,
              ip TEXT, input_path TEXT, input_bytes INTEGER NOT NULL,
              result_json TEXT, warnings_json TEXT NOT NULL DEFAULT '[]',
              error_code TEXT, created REAL NOT NULL, updated REAL NOT NULL,
              deadline REAL NOT NULL, expires REAL, tombstone_until REAL,
              generation INTEGER NOT NULL DEFAULT 0, reclaim_count INTEGER NOT NULL DEFAULT 0,
              lease_until REAL);
            CREATE TABLE IF NOT EXISTS keys (
              kind TEXT NOT NULL, key_hash TEXT NOT NULL, fingerprint TEXT NOT NULL,
              job_id TEXT NOT NULL, PRIMARY KEY(kind,key_hash));
            CREATE TABLE IF NOT EXISTS tombstones (
              id TEXT PRIMARY KEY, status TEXT NOT NULL,
              expires REAL NOT NULL, tombstone_until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS rates (
              ip TEXT NOT NULL, bucket TEXT NOT NULL, window INTEGER NOT NULL,
              count INTEGER NOT NULL, PRIMARY KEY(ip,bucket,window));
            """)
        self.db_path.chmod(0o600)

    @contextmanager
    def _tx(self, rollback_paths: list[Path] | None = None) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            for path in rollback_paths or ():
                path.unlink(missing_ok=True)
            raise
        finally:
            db.close()
        # Filesystem unlink is irreversible. Run it only after the terminal
        # state has committed, so a later exception cannot strand an active
        # job with a missing input file.
        self._cleanup_inputs()

    def _cleanup_inputs(self) -> None:
        try:
            with sqlite3.connect(self.db_path, timeout=30) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute("SELECT id,input_path FROM jobs WHERE status IN "
                                  "('succeeded','failed','cancelled') AND input_path IS NOT NULL").fetchall()
                for row in rows:
                    path = Path(row["input_path"])
                    if path != self.artifact_dir / row["id"]:
                        logger.warning("Translation v2 input path is outside its artifact slot")
                        continue
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        # Keep the byte charge and retry on the next operation.
                        continue
                    db.execute("UPDATE jobs SET input_path=NULL,input_bytes=0 WHERE id=?", (row["id"],))
        except sqlite3.Error:
            logger.warning("Translation v2 input cleanup database update failed; retrying")

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["job_id"] = value["id"]
        return value

    def _expire_overdue_jobs(self, db: sqlite3.Connection, now: float) -> None:
        # Reads and admissions run this even when no worker is alive. The job's
        # deadline includes its queue wait, and terminalization releases IP quota.
        overdue = db.execute("SELECT id,status FROM jobs WHERE status IN "
                             "('queued','running','cancelling') AND "
                             "(deadline<=? OR (status='cancelling' AND updated+13<=?))",
                             (now, now)).fetchall()
        for row in overdue:
            if row["status"] == "cancelling":
                self._terminal(db, row["id"], "cancelled", now)
            else:
                self._terminal(db, row["id"], "failed", now, "PROCESSING_TIMEOUT")

    def _lookup(self, db: sqlite3.Connection, job_id: str, now: float) -> sqlite3.Row:
        self._expire_overdue_jobs(db, now)
        well_formed = len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id)
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or not well_formed:
            tombstone = db.execute("SELECT tombstone_until FROM tombstones WHERE id=?",
                                   (job_id,)).fetchone()
            if tombstone and now < tombstone["tombstone_until"] and well_formed:
                raise TranslationError("JOB_EXPIRED", "Job has expired.")
            raise TranslationError("JOB_NOT_FOUND", "Job was not found.")
        if row["expires"] is not None and now >= row["expires"]:
            if now < row["tombstone_until"]:
                raise TranslationError("JOB_EXPIRED", "Job has expired.")
            raise TranslationError("JOB_NOT_FOUND", "Job was not found.")
        return row

    def get(self, job_id: str, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._tx() as db:
            return self._row(self._lookup(db, job_id, now))

    def rate(self, ip: str, bucket: str, limit: int, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        window = int(now // 60)
        with self._tx() as db:
            db.execute("DELETE FROM rates WHERE window<?", (window - 1,))
            db.execute("INSERT INTO rates VALUES (?,?,?,1) ON CONFLICT(ip,bucket,window) "
                       "DO UPDATE SET count=count+1", (ip, bucket, window))
            count = db.execute("SELECT count FROM rates WHERE ip=? AND bucket=? AND window=?",
                               (ip, bucket, window)).fetchone()[0]
            if count > limit:
                raise TranslationError("RATE_LIMITED", "Rate limit exceeded.")

    def admit(self, kind: str, payload: bytes, extension: str, source_lang: str,
              target_lang: str, ip: str, key: str | None, *, now: float | None = None
              ) -> dict[str, Any]:
        now = time.time() if now is None else now
        if not payload:
            raise TranslationError("EMPTY_FILE", "File is empty.")
        if len(payload) > self.settings.max_upload_bytes:
            raise TranslationError("FILE_TOO_LARGE", "File is too large.")
        self.reap(now=now)
        fingerprint = hashlib.sha256(payload + b"\0" + source_lang.encode() + b"\0"
                                     + target_lang.encode() + b"\0" + extension.lower().encode()).hexdigest()
        key_hash = hashlib.sha256(key.encode()).hexdigest() if key else None
        created_paths: list[Path] = []
        with self._tx(created_paths) as db:
            self._expire_overdue_jobs(db, now)
            if key_hash:
                found = db.execute("SELECT fingerprint,job_id FROM keys WHERE kind=? AND key_hash=?",
                                   (kind, key_hash)).fetchone()
                if found:
                    previous = db.execute("SELECT expires,tombstone_until FROM jobs WHERE id=?",
                                          (found["job_id"],)).fetchone()
                    if previous is None:
                        previous = db.execute("SELECT expires,tombstone_until FROM tombstones WHERE id=?",
                                              (found["job_id"],)).fetchone()
                    if previous and previous["tombstone_until"] is not None and now >= previous["tombstone_until"]:
                        db.execute("DELETE FROM keys WHERE kind=? AND key_hash=?", (kind, key_hash))
                    else:
                        if previous and previous["expires"] is not None and now >= previous["expires"]:
                            raise TranslationError("JOB_EXPIRED", "Job has expired.")
                        if found["fingerprint"] != fingerprint:
                            raise TranslationError("IDEMPOTENCY_CONFLICT", "Idempotency key conflicts.")
                        return self._row(self._lookup(db, found["job_id"], now))
            queued = db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]
            active_ip = db.execute("SELECT count(*) FROM jobs WHERE ip=? AND status IN "
                                   "('queued','running','cancelling')", (ip,)).fetchone()[0]
            used = db.execute("SELECT coalesce(sum(input_bytes),0) FROM jobs WHERE input_path IS NOT NULL"
                              ).fetchone()[0]
            if queued >= self.settings.max_queued_jobs or active_ip >= self.settings.max_active_per_ip:
                raise TranslationError("QUEUE_FULL", "Translation queue is full.")
            if used + len(payload) > self.settings.storage_quota_bytes:
                raise TranslationError("STORAGE_UNAVAILABLE", "Translation storage is unavailable.")
            job_id = secrets.token_hex(16)
            path = self.artifact_dir / job_id
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_paths.append(path)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                directory_fd = os.open(self.artifact_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                db.execute("INSERT INTO jobs(id,kind,extension,status,stage,source_lang,target_lang,ip,"
                           "input_path,input_bytes,created,updated,deadline) VALUES "
                           "(?,?,?,'queued','queued',?,?,?,?,?,?,?,?)",
                           (job_id, kind, extension, source_lang, target_lang, ip, str(path), len(payload),
                            now, now, now + self.settings.job_deadline_seconds))
                if key_hash:
                    db.execute("INSERT INTO keys VALUES (?,?,?,?)",
                               (kind, key_hash, fingerprint, job_id))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return self._row(self._lookup(db, job_id, now))

    def claim(self, *, now: float | None = None) -> dict[str, Any] | None:
        now = time.time() if now is None else now
        with self._tx() as db:
            self._expire_overdue_jobs(db, now)
            for cancelled in db.execute("SELECT id FROM jobs WHERE status='cancelling' "
                                        "AND lease_until<?", (now,)).fetchall():
                self._terminal(db, cancelled["id"], "cancelled", now)
            running = db.execute("SELECT count(*) FROM jobs WHERE status='running' AND lease_until>=?",
                                 (now,)).fetchone()[0]
            if running >= self.settings.max_running_jobs:
                return None
            row = db.execute("SELECT * FROM jobs WHERE status='queued' OR "
                             "(status='running' AND lease_until<?) ORDER BY created LIMIT 1",
                             (now,)).fetchone()
            if row is None:
                return None
            if row["deadline"] <= now or row["reclaim_count"] >= self.settings.max_reclaims:
                code = "PROCESSING_TIMEOUT" if row["deadline"] <= now else "WORKER_LOST"
                self._terminal(db, row["id"], "failed", now, code)
                return None
            reclaimed = row["status"] == "running"
            db.execute("UPDATE jobs SET status='running',stage='extracting',generation=generation+1,"
                       "reclaim_count=reclaim_count+?,lease_until=?,updated=? WHERE id=?",
                       (int(reclaimed), now + self.settings.lease_seconds, now, row["id"]))
            return self._row(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, job_id: str, generation: int, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            changed = db.execute("UPDATE jobs SET lease_until=?,updated=? WHERE id=? AND "
                                 "generation=? AND status='running' AND lease_until>? AND deadline>?",
                                 (now + self.settings.lease_seconds, now, job_id, generation, now, now))
            return changed.rowcount == 1

    def progress(self, job_id: str, generation: int, stage: str, completed: int,
                 total: int | None, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            changed = db.execute("UPDATE jobs SET stage=?,completed=max(completed,?),"
                                 "total=coalesce(total,?),updated=? WHERE id=? AND generation=? "
                                 "AND status='running' AND lease_until>? AND deadline>?",
                                 (stage, completed, total, now, job_id, generation, now, now))
            return changed.rowcount == 1

    def _terminal(self, db: sqlite3.Connection, job_id: str, status: str, now: float,
                  error_code: str | None = None, result: dict | None = None) -> None:
        db.execute("UPDATE jobs SET status=?,stage='done',error_code=?,result_json=?,"
                   "warnings_json=?,updated=?,expires=?,tombstone_until=?,ip=NULL WHERE id=?",
                   (status, error_code, json.dumps(result, ensure_ascii=False) if result else None,
                    json.dumps(result.get("warnings", []), ensure_ascii=False) if result else "[]",
                    now, now + self.settings.result_ttl_seconds,
                    now + self.settings.result_ttl_seconds + self.settings.tombstone_ttl_seconds, job_id))

    def finish(self, job_id: str, generation: int, *, result: dict | None = None,
               error_code: str | None = None, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            row = db.execute("SELECT status,generation,lease_until,deadline FROM jobs WHERE id=?",
                             (job_id,)).fetchone()
            if row is not None and row["status"] == "cancelling" and row["generation"] == generation:
                self._terminal(db, job_id, "cancelled", now)
                return False
            if row is None or row["status"] != "running" or row["generation"] != generation:
                return False
            if row["lease_until"] <= now or row["deadline"] <= now:
                return False
            self._terminal(db, job_id, "failed" if error_code else "succeeded", now, error_code, result)
            return True

    def cancel(self, job_id: str, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._tx() as db:
            row = self._lookup(db, job_id, now)
            if row["status"] in {"succeeded", "failed"}:
                raise TranslationError("JOB_ALREADY_FINISHED", "Job has already finished.")
            if row["status"] == "queued":
                self._terminal(db, job_id, "cancelled", now)
            elif row["status"] == "running":
                db.execute("UPDATE jobs SET status='cancelling',updated=? WHERE id=?", (now, job_id))
            return self._row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def reap(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._tx() as db:
            self._expire_overdue_jobs(db, now)
            expired = db.execute("SELECT id,status,expires,tombstone_until,input_path FROM jobs "
                                 "WHERE expires IS NOT NULL AND expires<=? AND input_path IS NULL",
                                 (now,)).fetchall()
            for row in expired:
                db.execute("INSERT OR REPLACE INTO tombstones VALUES (?,?,?,?)",
                           (row["id"], row["status"], row["expires"], row["tombstone_until"]))
                db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
            db.execute("DELETE FROM keys WHERE job_id IN "
                       "(SELECT id FROM tombstones WHERE tombstone_until<=?)", (now,))
            db.execute("DELETE FROM tombstones WHERE tombstone_until<=?", (now,))
