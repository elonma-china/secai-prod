"""UC test suite for the Project -> Session management feature (`feat/session`).

Covers `src/secai_gateway/api/projects.py` (Project/Session CRUD), the durable
`ProjectStore` (`src/secai_gateway/state/project_store.py`), the session-scoped chat
routes (`POST /sessions/{sid}/chat[/stream]` in `src/secai_gateway/api/routes.py`), and
the linch session store wiring (memory + sqlite backends).

Hermetic, mirrors the style of `tests/test_gateway.py`: `create_app(runner)` +
`httpx.AsyncClient(ASGITransport)`. `app.state.gateway` is only set inside the app's
lifespan, so every test that touches `/projects*` or `/sessions/*` drives
`app.router.lifespan_context(app)` explicitly via the `_client()` helper below.

Owner-scoping is FIXED in the current source (see `api/projects.py` module docstring):
non-owner GETs return 404 (don't disclose existence), non-owner mutations return 403.
UC-17 below asserts that real behavior directly (no xfail — the gap this UC was
originally written against has already been closed).
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

import httpx

from linch.events import PartialAssistantEvent
from linch.sessions.memory import InMemorySessionStore

from secai_gateway.app import create_app
from secai_gateway.state import ProjectStore


class ProjectFakeRunner:
    """Runner double for project/session UCs: a REAL in-memory linch session store +
    a REAL sqlite-backed ProjectStore (on ``tmp_path``), plus a call log so tests can
    assert per-session routing the way ``FakeRunner`` does in ``test_gateway.py``."""

    def __init__(self, tmp_path) -> None:
        self.project_store = ProjectStore(str(tmp_path / "projects.db"))
        self.session_store = InMemorySessionStore()
        self.poc = None  # /health uses getattr(..., "poc", None)
        self.calls: list[tuple[str, str, str | None]] = []

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        self.calls.append((profile_name, prompt, session_id))
        return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        self.calls.append((profile_name, prompt, session_id))
        yield PartialAssistantEvent(delta={"kind": "text", "text": f"{profile_name}: {prompt}"})


@asynccontextmanager
async def _client(runner):
    """Build the app around ``runner``, drive its lifespan (so ``app.state.gateway`` is
    set), and yield an ASGI-transport client for the duration of the ``with`` block."""
    app = create_app(runner)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client


# ── UC-01 — Project create + get (happy path) ──────────────────────────────────────

async def test_uc01_project_create_and_get(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        created = await client.post("/projects", json={"name": "Recon Q3", "user_id": "alice"})
        assert created.status_code == 200
        body = created.json()
        assert body["project_id"]
        assert body["name"] == "Recon Q3"
        assert body["owner"] == "alice"
        # created_at/updated_at are ISO-8601 strings (owner-scoping round-2 fix also
        # normalized the project timestamp shape to match sessions — no epoch ints).
        assert isinstance(body["created_at"], str)
        assert isinstance(body["updated_at"], str)
        assert body["created_at"] == body["updated_at"]
        pid = body["project_id"]

        fetched = await client.get(f"/projects/{pid}", params={"user_id": "alice"})
        assert fetched.status_code == 200
        fbody = fetched.json()
        assert fbody["project_id"] == pid
        assert fbody["name"] == "Recon Q3"
        assert fbody["owner"] == "alice"


# ── UC-02 — Project create validation ───────────────────────────────────────────────

async def test_uc02_project_create_validation(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        blank = await client.post("/projects", json={"name": "   ", "user_id": "alice"})
        assert blank.status_code == 400
        assert blank.json()["detail"]["error"] == "bad_request"

        empty = await client.post("/projects", json={"name": "", "user_id": "alice"})
        assert empty.status_code == 400

        missing = await client.post("/projects", json={"user_id": "alice"})
        assert missing.status_code == 422


# ── UC-03 — GET /projects is owner-filtered ─────────────────────────────────────────

async def test_uc03_list_projects_owner_filtered(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        p_alice = (await client.post("/projects", json={"name": "PA", "user_id": "alice"})).json()
        p_bob = (await client.post("/projects", json={"name": "PB", "user_id": "bob"})).json()

        alice_list = await client.get("/projects", params={"user_id": "alice"})
        assert alice_list.status_code == 200
        alice_ids = {p["project_id"] for p in alice_list.json()}
        assert p_alice["project_id"] in alice_ids
        assert p_bob["project_id"] not in alice_ids

        bob_list = await client.get("/projects", params={"user_id": "bob"})
        assert bob_list.status_code == 200
        bob_ids = {p["project_id"] for p in bob_list.json()}
        assert bob_ids == {p_bob["project_id"]}

        anon_list = await client.get("/projects")
        assert anon_list.status_code == 200
        assert anon_list.json() == []


# ── UC-04 — Rename project + owner enforcement ──────────────────────────────────────

async def test_uc04_rename_project_owner_enforcement(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        created = (await client.post("/projects", json={"name": "Orig", "user_id": "alice"})).json()
        pid = created["project_id"]

        renamed = await client.patch(f"/projects/{pid}", json={"name": "Renamed", "user_id": "alice"})
        assert renamed.status_code == 200
        rbody = renamed.json()
        assert rbody["name"] == "Renamed"
        assert rbody["updated_at"] >= rbody["created_at"]
        followup = await client.get(f"/projects/{pid}", params={"user_id": "alice"})
        assert followup.json()["name"] == "Renamed"

        hijack = await client.patch(f"/projects/{pid}", json={"name": "Hijack", "user_id": "bob"})
        assert hijack.status_code == 403
        assert hijack.json()["detail"]["error"] == "forbidden"
        still = await client.get(f"/projects/{pid}", params={"user_id": "alice"})
        assert still.json()["name"] == "Renamed"

        missing = await client.patch("/projects/does-not-exist", json={"name": "X", "user_id": "alice"})
        assert missing.status_code == 404

        blank = await client.patch(f"/projects/{pid}", json={"name": "  ", "user_id": "alice"})
        assert blank.status_code == 400


# ── UC-05 — Delete project owner enforcement + not-found ───────────────────────────

async def test_uc05_delete_project_owner_enforcement(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        created = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = created["project_id"]

        hijack = await client.delete(f"/projects/{pid}", params={"user_id": "bob"})
        assert hijack.status_code == 403
        still = await client.get(f"/projects/{pid}", params={"user_id": "alice"})
        assert still.status_code == 200

        missing = await client.delete("/projects/unknown", params={"user_id": "alice"})
        assert missing.status_code == 404


# ── UC-06 — Multi-session creation + project linkage + list scoping ────────────────

async def test_uc06_multi_session_creation_and_listing(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]

        s1 = await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice", "title": "S1"})
        assert s1.status_code == 200
        s1body = s1.json()
        assert s1body["project_id"] == pid
        assert s1body["title"] == "S1"
        assert re.match(rf"^proj:{re.escape(pid)}:sess:[0-9a-f]+$", s1body["session_id"])

        s2 = await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice", "title": "S2"})
        assert s2.status_code == 200
        s2body = s2.json()
        assert s2body["session_id"] != s1body["session_id"]

        listing = await client.get(f"/projects/{pid}/sessions", params={"user_id": "alice"})
        assert listing.status_code == 200
        rows = listing.json()
        assert len(rows) == 2
        assert {r["session_id"] for r in rows} == {s1body["session_id"], s2body["session_id"]}
        for row in rows:
            assert row["title"] in {"S1", "S2"}
            assert isinstance(row["created_at"], str)
            assert isinstance(row["updated_at"], str)

        forbidden = await client.post(f"/projects/{pid}/sessions", json={"user_id": "bob", "title": "X"})
        assert forbidden.status_code == 403

        not_found = await client.post("/projects/unknown/sessions", json={"user_id": "alice"})
        assert not_found.status_code == 404

        bob_read = await client.get(f"/projects/{pid}/sessions", params={"user_id": "bob"})
        assert bob_read.status_code == 404


# ── UC-07 — Two sessions keep independent memory (per-session routing) ─────────────

async def test_uc07_two_sessions_route_independently(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid_a = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice", "title": "A"})).json()["session_id"]
        sid_b = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice", "title": "B"})).json()["session_id"]
        assert sid_a != sid_b

        r_a = await client.post(f"/sessions/{sid_a}/chat", json={"user_id": "alice", "message": "remember A"})
        assert r_a.status_code == 200
        r_b = await client.post(f"/sessions/{sid_b}/chat", json={"user_id": "alice", "message": "remember B"})
        assert r_b.status_code == 200

        call_a = next(c for c in runner.calls if c[2] == sid_a)
        call_b = next(c for c in runner.calls if c[2] == sid_b)
        assert call_a == ("chat", "remember A", sid_a)
        assert call_b == ("chat", "remember B", sid_b)


# ── UC-08 — Reopen a session loads its messages in seq order ───────────────────────

async def test_uc08_reopen_session_loads_messages_in_order(tmp_path) -> None:
    from linch.types import Message, TextBlock

    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]

        await runner.session_store.append_messages(sid, [
            Message(role="user", content=[TextBlock(text="m1")]),
            Message(role="assistant", content=[TextBlock(text="m2")]),
            Message(role="user", content=[TextBlock(text="m3")]),
        ])

        resp = await client.get(f"/sessions/{sid}/messages", params={"user_id": "alice"})
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert len(messages) == 3
        assert [m["seq"] for m in messages] == [1, 2, 3]
        assert messages[0]["message"]["role"] == "user"
        assert messages[0]["message"]["content"][0]["text"] == "m1"
        assert messages[1]["message"]["role"] == "assistant"
        assert messages[1]["message"]["content"][0]["text"] == "m2"
        assert messages[2]["message"]["content"][0]["text"] == "m3"
        for m in messages:
            assert isinstance(m["appended_at"], str)


# ── UC-09 — Durability against a REAL SqliteSessionStore (no TTL) ──────────────────

async def test_uc09_sqlite_session_store_durability(tmp_path) -> None:
    from linch.sessions.sqlite import SqliteSessionStore
    from linch.types import Message, TextBlock

    db = str(tmp_path / "sessions.db")
    s1 = SqliteSessionStore(db)
    await s1.create(id="proj:p1:sess:aaa", meta={
        "project_id": "p1", "owner": "alice", "title": "T", "kind": "session",
    })
    await s1.append_messages("proj:p1:sess:aaa", [Message(role="user", content=[TextBlock(text="hello")])])
    await s1.close()

    s2 = SqliteSessionStore(db)  # brand-new instance, same path
    rec = await s2.load("proj:p1:sess:aaa")
    msgs = await s2.load_messages("proj:p1:sess:aaa")
    assert rec is not None
    assert rec.meta["project_id"] == "p1"
    assert rec.meta["kind"] == "session"
    assert rec.meta["owner"] == "alice"
    assert len(msgs) == 1
    assert msgs[0].message.content[0].text == "hello"
    await s2.close()

    # Project durability: a new ProjectStore instance on the same sqlite path sees rows
    # created by a previous instance.
    db2 = str(tmp_path / "projects.db")
    ps1 = ProjectStore(db2)
    created = ps1.create(None, owner="alice", name="P")
    pid = created["project_id"]
    ps2 = ProjectStore(db2)
    got = ps2.get(pid)
    assert got is not None
    assert got["owner"] == "alice"
    assert got["name"] == "P"


# ── UC-10 — Cascade delete removes exactly the project's sessions ──────────────────

async def test_uc10_cascade_delete_removes_only_project_sessions(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj_p = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid_p = proj_p["project_id"]
        sid_a = (await client.post(f"/projects/{pid_p}/sessions", json={"user_id": "alice", "title": "A"})).json()["session_id"]
        sid_b = (await client.post(f"/projects/{pid_p}/sessions", json={"user_id": "alice", "title": "B"})).json()["session_id"]

        proj_q = (await client.post("/projects", json={"name": "Q", "user_id": "alice"})).json()
        pid_q = proj_q["project_id"]
        sid_q = (await client.post(f"/projects/{pid_q}/sessions", json={"user_id": "alice", "title": "Q1"})).json()["session_id"]

        await runner.session_store.create(id="chat:xyz", meta={"profile": "chat"})

        resp = await client.delete(f"/projects/{pid_p}", params={"user_id": "alice"})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2}

        assert await runner.session_store.load(sid_a) is None
        assert await runner.session_store.load(sid_b) is None
        assert await runner.session_store.load(sid_q) is not None
        assert await runner.session_store.load("chat:xyz") is not None
        assert runner.project_store.get(pid_p) is None
        assert runner.project_store.get(pid_q) is not None


# ── UC-11 — Unknown session -> 404 on chat AND stream, no runner call ──────────────

async def test_uc11_unknown_session_404_no_runner_call(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        r1 = await client.post("/sessions/proj:p:sess:missing/chat", json={"message": "hi"})
        assert r1.status_code == 404

        r2 = await client.post("/sessions/proj:p:sess:missing/chat/stream", json={"message": "hi"})
        assert r2.status_code == 404
        content_type = r2.headers.get("content-type", "")
        assert "text/event-stream" not in content_type
        assert "event: token" not in r2.text
        assert "data: [DONE]" not in r2.text

        assert runner.calls == []  # runner never invoked — no orphan auto-create


# ── UC-12 — Secret input still 422 on /sessions/{sid}/chat ─────────────────────────

async def test_uc12_secret_input_still_422_on_session_chat(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]

        resp = await client.post(f"/sessions/{sid}/chat",
                                 json={"user_id": "alice", "message": "password=super-secret-token-value"})
        assert resp.status_code == 422
        assert not any("super-secret-token-value" in prompt for _p, prompt, _s in runner.calls)


# ── UC-13 — Session-scoped chat happy path (shape + routing, non-stream + stream) ──

async def test_uc13_session_scoped_chat_happy_path(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]

        resp = await client.post(f"/sessions/{sid}/chat", json={"user_id": "alice", "message": "hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert {"response", "answer", "message", "metadata"} <= body.keys()
        assert body["response"].startswith("chat:")
        assert runner.calls[-1] == ("chat", "hello", sid)

        stream = await client.post(f"/sessions/{sid}/chat/stream", json={"user_id": "alice", "message": "hello"})
        assert stream.status_code == 200
        assert "event: token" in stream.text
        assert "chat:" in stream.text
        assert "event: final" in stream.text
        assert "data: [DONE]" in stream.text


# ── UC-14 — Delete session owner enforcement ────────────────────────────────────────

async def test_uc14_delete_session_owner_enforcement(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]

        hijack = await client.delete(f"/sessions/{sid}", params={"user_id": "bob"})
        assert hijack.status_code == 403
        assert hijack.json()["detail"]["error"] == "forbidden"
        still = await client.get(f"/sessions/{sid}", params={"user_id": "alice"})
        assert still.status_code == 200

        deleted = await client.delete(f"/sessions/{sid}", params={"user_id": "alice"})
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        gone = await client.get(f"/sessions/{sid}", params={"user_id": "alice"})
        assert gone.status_code == 404

        missing = await client.delete("/sessions/proj:p:sess:nope", params={"user_id": "alice"})
        assert missing.status_code == 404


# ── UC-15 — CORS preflight allows DELETE and PATCH ──────────────────────────────────

async def test_uc15_cors_preflight_allows_delete_and_patch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://ui.test")
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]

        preflight_delete = await client.options(
            f"/projects/{pid}",
            headers={"Origin": "http://ui.test", "Access-Control-Request-Method": "DELETE"},
        )
        assert preflight_delete.status_code == 200
        assert "DELETE" in preflight_delete.headers.get("access-control-allow-methods", "")

        preflight_patch = await client.options(
            f"/projects/{pid}",
            headers={"Origin": "http://ui.test", "Access-Control-Request-Method": "PATCH"},
        )
        assert preflight_patch.status_code == 200
        assert "PATCH" in preflight_patch.headers.get("access-control-allow-methods", "")


# ── UC-16 — /health surfaces the session backend ────────────────────────────────────

async def test_uc16_health_surfaces_session_backend(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_backend"] == "memory"  # hermetic_env sets SESSION_STORE_BACKEND=memory
        assert body["redis"] == "ok"  # InMemorySessionStore has no ping() -> not "connected"/"unreachable"
        assert "status" in body and "input_sanitize" in body and "llm" in body


# ── UC-17 — Cross-owner isolation on GET reads + session chat (FIXED, not xfail) ───
# These endpoints used to skip owner-scoping (review F1-F3). That gap is now closed in
# api/projects.py / routes.py, so these assert the real, secure behavior directly.

async def test_uc17a_get_project_non_owner_404(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]

        non_owner = await client.get(f"/projects/{pid}", params={"user_id": "bob"})
        assert non_owner.status_code == 404

        owner = await client.get(f"/projects/{pid}", params={"user_id": "alice"})
        assert owner.status_code == 200


async def test_uc17b_get_session_non_owner_404(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]

        non_owner = await client.get(f"/sessions/{sid}", params={"user_id": "bob"})
        assert non_owner.status_code == 404

        owner = await client.get(f"/sessions/{sid}", params={"user_id": "alice"})
        assert owner.status_code == 200


async def test_uc17c_get_session_messages_non_owner_404(tmp_path) -> None:
    from linch.types import Message, TextBlock

    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]
        await runner.session_store.append_messages(sid, [
            Message(role="user", content=[TextBlock(text="secret chat")]),
        ])

        non_owner = await client.get(f"/sessions/{sid}/messages", params={"user_id": "bob"})
        assert non_owner.status_code == 404  # chat-history confidentiality — no leak to a non-owner

        owner = await client.get(f"/sessions/{sid}/messages", params={"user_id": "alice"})
        assert owner.status_code == 200
        assert len(owner.json()["messages"]) == 1


async def test_uc17d_session_chat_non_owner_403(tmp_path) -> None:
    runner = ProjectFakeRunner(tmp_path)
    async with _client(runner) as client:
        proj = (await client.post("/projects", json={"name": "P", "user_id": "alice"})).json()
        pid = proj["project_id"]
        sid = (await client.post(f"/projects/{pid}/sessions", json={"user_id": "alice"})).json()["session_id"]

        non_owner_chat = await client.post(
            f"/sessions/{sid}/chat", json={"user_id": "bob", "message": "summarize our conversation"})
        assert non_owner_chat.status_code == 403

        non_owner_stream = await client.post(
            f"/sessions/{sid}/chat/stream", json={"user_id": "bob", "message": "summarize our conversation"})
        assert non_owner_stream.status_code == 403

        owner_chat = await client.post(f"/sessions/{sid}/chat", json={"user_id": "alice", "message": "hello"})
        assert owner_chat.status_code == 200

        # The non-owner's 403s never reached the runner (no injected turn / no context
        # exfiltrated into a reply) — the only recorded call is alice's own "hello" turn.
        assert not any("summarize our conversation" in prompt for _p, prompt, _s in runner.calls)
        assert runner.calls == [("chat", "hello", sid)]
