"""Small SQLite store for editable templates and immutable report revisions."""
from __future__ import annotations

import json
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS templates (
            id TEXT PRIMARY KEY, version INTEGER NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL, sections TEXT NOT NULL, instructions TEXT NOT NULL,
            example TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
            revision INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            source_data TEXT NOT NULL, template_sections TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS revisions (
            report_id TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(report_id, revision)
        );
    """)
    return db


@contextmanager
def _db(path: str):
    db = _connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def _template(row: sqlite3.Row) -> dict:
    result = dict(row)
    result['sections'] = json.loads(result['sections'])
    return result


def list_templates(path: str) -> list[dict]:
    with _db(path) as db:
        return [_template(row) for row in db.execute('SELECT * FROM templates ORDER BY updated_at DESC')]


def get_template(path: str, template_id: str) -> dict | None:
    with _db(path) as db:
        row = db.execute('SELECT * FROM templates WHERE id=?', (template_id,)).fetchone()
        return _template(row) if row else None


def create_template(path: str, data: dict) -> dict:
    template_id, now = str(uuid4()), _now()
    with _db(path) as db:
        db.execute('INSERT INTO templates VALUES (?,?,?,?,?,?,?,?,?)',
                   (template_id, 1, data['name'], data['description'], json.dumps(data['sections']),
                    data['instructions'], data['example'], now, now))
    return get_template(path, template_id)


def update_template(path: str, template_id: str, data: dict, expected_version: int) -> tuple[str, dict | None]:
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT version FROM templates WHERE id=?', (template_id,)).fetchone()
        if not row:
            return 'missing', None
        if row['version'] != expected_version:
            return 'conflict', None
        db.execute('UPDATE templates SET version=version+1,name=?,description=?,sections=?,instructions=?,example=?,updated_at=? WHERE id=?',
                   (data['name'], data['description'], json.dumps(data['sections']),
                    data['instructions'], data['example'], _now(), template_id))
        result = _template(db.execute('SELECT * FROM templates WHERE id=?', (template_id,)).fetchone())
    return 'ok', result


def _report(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ('id', 'template_id', 'template_version', 'revision', 'content', 'created_at')}


def get_report(path: str, report_id: str) -> dict | None:
    with _db(path) as db:
        row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        return _report(row) if row else None


def create_report(path: str, template: dict, source_data: str, content: str) -> dict:
    report_id, now = str(uuid4()), _now()
    with _db(path) as db:
        db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?)',
                   (report_id, template['id'], template['version'], 1, content, now,
                    source_data, json.dumps(template['sections'])))
        db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (report_id, 1, content, now))
    return get_report(path, report_id)


def get_report_context(path: str, report_id: str) -> dict | None:
    with _db(path) as db:
        row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['template_sections'] = json.loads(result['template_sections'])
        return result


def edit_report(path: str, report_id: str, content: str, expected_revision: int) -> tuple[str, dict | None]:
    with _db(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT revision FROM reports WHERE id=?', (report_id,)).fetchone()
        if not row:
            return 'missing', None
        if row['revision'] != expected_revision:
            return 'conflict', None
        revision, now = expected_revision + 1, _now()
        db.execute('UPDATE reports SET revision=?,content=? WHERE id=?', (revision, content, report_id))
        db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (report_id, revision, content, now))
        result = _report(db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone())
    return 'ok', result


def list_revisions(path: str, report_id: str) -> list[dict] | None:
    with _db(path) as db:
        if not db.execute('SELECT 1 FROM reports WHERE id=?', (report_id,)).fetchone():
            return None
        return [dict(row) for row in db.execute(
            'SELECT revision,content,created_at FROM revisions WHERE report_id=? ORDER BY revision',
            (report_id,))]
