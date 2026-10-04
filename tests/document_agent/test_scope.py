"""SQLite scope and turn replay behavior, with no external services."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from secai_gateway.document_agent.scope import (
    DocumentScopeStore,
    ExpiredContext,
    InvalidContext,
    TaskNotFound,
    TooManyContexts,
    TurnInProgress,
    TurnPayloadConflict,
    TurnReservationLost,
    TurnResponseTooLarge,
)
from secai_gateway.document_agent.settings import (
    CONTEXT_TTL_SECONDS,
    MAX_CONTEXTS_PER_PEER,
    MAX_TURN_RESPONSE_BYTES,
    TURN_LEASE_SECONDS,
)


def _store(tmp_path):
    return DocumentScopeStore(tmp_path / "scopes.sqlite3")


def _scope(store, now):
    return store.resolve(store.create("192.0.2.10", now).context_token, now)


def test_context_token_is_hashed_and_repr_is_redacted(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    grant = store.create("192.0.2.10", now)
    assert len(grant.context_token) == 64
    assert grant.context_token == grant.context_token.lower()
    assert all(ch in "0123456789abcdef" for ch in grant.context_token)
    assert grant.expires_at == now + CONTEXT_TTL_SECONDS
    assert grant.context_token not in repr(grant)
    assert store.resolve(grant.context_token, now).expires_at == grant.expires_at

    raw_database = (tmp_path / "scopes.sqlite3").read_bytes()
    assert grant.context_token.encode() not in raw_database
    with sqlite3.connect(tmp_path / "scopes.sqlite3") as conn:
        stored_hash, = conn.execute("SELECT token_hash FROM document_contexts").fetchone()
    assert stored_hash == hashlib.sha256(bytes.fromhex(grant.context_token)).hexdigest()


def test_exact_expiry_boundary_and_invalid_credentials(tmp_path):
    store = _store(tmp_path)
    grant = store.create("192.0.2.10", 1000.0)
    assert store.resolve(grant.context_token, grant.expires_at - 0.001)
    with pytest.raises(ExpiredContext):
        store.resolve(grant.context_token, grant.expires_at)
    with pytest.raises(InvalidContext):
        store.resolve("0" * 64, 1000.0)
    with pytest.raises(InvalidContext):
        store.resolve("bad-token", 1000.0)


def test_context_deadline_must_remain_finite(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.create("192.0.2.10", 1e308)


def test_peer_context_cap_counts_only_live_contexts(tmp_path):
    store = _store(tmp_path)
    grants = [store.create("192.0.2.10", 1000.0) for _ in range(MAX_CONTEXTS_PER_PEER)]
    with pytest.raises(TooManyContexts):
        store.create("192.0.2.10", 1001.0)
    assert store.create("192.0.2.11", 1001.0)
    assert store.create("192.0.2.10", grants[0].expires_at)
    with pytest.raises(ExpiredContext):
        store.resolve(grants[0].context_token, grants[0].expires_at)


def test_task_handle_is_stable_scoped_and_survives_restart(tmp_path):
    path = tmp_path / "scopes.sqlite3"
    store = DocumentScopeStore(path)
    now = time.time()
    first = _scope(store, now)
    second = _scope(store, now)
    handle = store.bind_task(first, "private-v2-job-id")
    assert len(handle) == 32
    assert all(ch in "0123456789abcdef" for ch in handle)
    assert "private-v2-job-id" not in handle
    assert store.bind_task(first, "private-v2-job-id") == handle
    with pytest.raises(TaskNotFound):
        store.resolve_task(second, handle)
    assert DocumentScopeStore(path).resolve_task(first, handle) == "private-v2-job-id"
    with pytest.raises(TaskNotFound):
        store.resolve_task(first, "private-v2-job-id")


def test_concurrent_binding_uses_one_handle(tmp_path):
    path = tmp_path / "scopes.sqlite3"
    store = DocumentScopeStore(path)
    scope = _scope(store, time.time())
    instances = [DocumentScopeStore(path) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        handles = list(pool.map(lambda s: s.bind_task(scope, "same-job"), instances))
    assert len(set(handles)) == 1
    with sqlite3.connect(path) as conn:
        count, = conn.execute("SELECT count(*) FROM document_tasks").fetchone()
    assert count == 1


def test_turn_replay_running_duplicate_and_changed_payload(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    scope = _scope(store, now)
    key, digest = "a" * 32, "b" * 64
    start = store.begin_turn(scope, key, digest, now)
    assert start.reservation and start.response is None
    with pytest.raises(TurnInProgress):
        store.begin_turn(scope, key, digest, now + 1)
    with pytest.raises(TurnPayloadConflict):
        store.begin_turn(scope, key, "c" * 64, now + 1)

    response = {"answer": "OCR queued", "tasks": [{"task_handle": "d" * 32, "status": "queued", "needs_review": False}], "request_id": "r1"}
    store.complete_turn(scope, key, start.reservation, response, now + 2)
    replay = DocumentScopeStore(tmp_path / "scopes.sqlite3").begin_turn(scope, key, digest, now + 3)
    assert replay.reservation is None
    assert replay.response == response
    with pytest.raises(TurnPayloadConflict):
        store.begin_turn(scope, key, "c" * 64, now + 3)


def test_abort_releases_admission_failure_without_changing_payload(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    scope = _scope(store, now)
    key, digest = "a" * 32, "b" * 64
    first = store.begin_turn(scope, key, digest, now)
    store.abort_turn(scope, key, first.reservation, now + 1)
    second = store.begin_turn(scope, key, digest, now + 2)
    assert second.reservation != first.reservation
    with pytest.raises(TurnPayloadConflict):
        store.begin_turn(scope, key, "c" * 64, now + 2)
    with pytest.raises(TurnReservationLost):
        store.complete_turn(scope, key, first.reservation, {"answer": "late"}, now + 3)


def test_stale_turn_recovers_after_restart_and_old_reservation_cannot_win(tmp_path):
    path = tmp_path / "scopes.sqlite3"
    store = DocumentScopeStore(path)
    now = time.time()
    scope = _scope(store, now)
    key, digest = "a" * 32, "b" * 64
    first = store.begin_turn(scope, key, digest, now)
    restarted = DocumentScopeStore(path)
    with pytest.raises(TurnInProgress):
        restarted.begin_turn(scope, key, digest, now + TURN_LEASE_SECONDS - 0.001)
    second = restarted.begin_turn(scope, key, digest, now + TURN_LEASE_SECONDS)
    assert second.reservation != first.reservation
    with pytest.raises(TurnReservationLost):
        store.abort_turn(scope, key, first.reservation, now + TURN_LEASE_SECONDS + 1)
    restarted.complete_turn(scope, key, second.reservation, {"answer": "new"}, now + TURN_LEASE_SECONDS + 2)
    assert restarted.begin_turn(scope, key, digest, now + TURN_LEASE_SECONDS + 3).response == {"answer": "new"}


def test_turn_response_is_bounded_and_never_persists_failed_oversize(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    scope = _scope(store, now)
    key, digest = "a" * 32, "b" * 64
    start = store.begin_turn(scope, key, digest, now)
    with pytest.raises(TurnResponseTooLarge):
        store.complete_turn(scope, key, start.reservation, {"answer": "x" * MAX_TURN_RESPONSE_BYTES}, now + 1)
    with pytest.raises(TurnInProgress):
        store.begin_turn(scope, key, digest, now + 2)
    store.complete_turn(scope, key, start.reservation, {"answer": "safe"}, now + 3)
    assert store.begin_turn(scope, key, digest, now + 4).response == {"answer": "safe"}
    with sqlite3.connect(tmp_path / "scopes.sqlite3") as conn:
        payload, = conn.execute("SELECT response_json FROM document_turns").fetchone()
    assert json.loads(payload) == {"answer": "safe"}


def test_expired_context_cannot_bind_or_start_new_turn(tmp_path):
    store = _store(tmp_path)
    grant = store.create("192.0.2.10", time.time() - CONTEXT_TTL_SECONDS - 1)
    # A stale scope from a prior request must not bypass the database expiry check.
    from secai_gateway.document_agent.scope import DocumentScope

    stale = DocumentScope("forged", grant.expires_at)
    with pytest.raises(ExpiredContext):
        store.bind_task(stale, "job")
    with pytest.raises(ExpiredContext):
        store.begin_turn(stale, "a" * 32, "b" * 64, time.time())
