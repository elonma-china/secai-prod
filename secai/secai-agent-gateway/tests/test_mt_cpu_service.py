"""Private Hy-MT CPU service contract and resource safety tests (no model loads)."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import struct
import sys
import time
from pathlib import Path

import httpx
import pytest

from deploy.mt_cpu_service.service import (
    CANDIDATES, Candidate, OwnedLlamaServer, ServiceSettings, TranslationRuntime,
    attest_artifacts, create_app, ServiceFailure, _owns_listen_port,
)


PROFILE = Candidate(
    key="test", model="tencent/Hy-MT2-7B", revision="pinned",
    gguf_sha256="", server_sha256="", bos_token_id=127958, gguf_eos_token_id=3,
    terminal_token_ids=frozenset({3}), chat_template_sha256="",
)


class FakeBackend:
    def __init__(self, *, terminal=3, content="Bản dịch", ignore_cancel=False, profile=PROFILE):
        self.calls = []
        self.terminal = terminal
        self.content = content
        self.live = True
        self.reaped = 0
        self.completion_started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail_reap = False
        self.fail_starts = 0
        self.start_calls = 0
        self.ignore_cancel = ignore_cancel
        self.profile = profile

    async def post_json(self, path, body, deadline):
        self.calls.append((path, body, deadline))
        if path == "/apply-template":
            return {"prompt": "<|startoftext|>" + body["messages"][0]["content"] + "<|extra_0|>"}
        if path == "/tokenize":
            return {"tokens": [127958, 10, 11]}
        assert path == "/completion"
        self.completion_started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if not self.ignore_cancel:
                raise
            await self.release.wait()
        return {"content": self.content, "stop_type": "eos", "truncated": False,
                "tokens": [42, self.terminal],
                "model": f"{self.profile.model}@{self.profile.revision}"}

    async def reap(self):
        self.reaped += 1
        if self.fail_reap:
            return False
        self.live = False
        self.release.set()
        return True

    async def start(self, deadline=None):
        self.start_calls += 1
        assert deadline is not None and deadline > time.monotonic()
        if self.fail_starts:
            self.fail_starts -= 1
            raise RuntimeError("fake startup failed")
        self.live = True


def settings(tmp_path):
    token_file = tmp_path / "secret"
    token_file.write_text("private-token")
    token_file.chmod(0o600)
    return ServiceSettings(candidate=PROFILE, model_path=tmp_path / "m.gguf",
                           server_path=tmp_path / "llama-server", token="private-token",
                           token_file=token_file,
                           timeout_seconds=0.3, max_waiters=1)


@pytest.mark.asyncio
async def test_private_wire_envelope_auth_and_prompt(tmp_path):
    backend = FakeBackend()
    cfg = settings(tmp_path)
    runtime = TranslationRuntime(cfg, backend)
    transport = httpx.ASGITransport(app=create_app(cfg, runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        body = {"text": "Xin chào", "source_lang": "vi", "target_lang": "zh"}
        assert (await client.post("/v1/translate", json=body)).status_code == 401
        headers = {"Authorization": "Bearer private-token"}
        response = await client.post("/v1/translate", json=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"translated_text": "Bản dịch", "model": PROFILE.model}
        assert "Simplified Chinese" in backend.calls[0][1]["messages"][0]["content"]
        assert [c[0] for c in backend.calls] == ["/apply-template", "/tokenize", "/completion"]
        assert backend.calls[1][1] == {"content": backend.calls[0][1]["messages"][0]["content"].join(["<|startoftext|>", "<|extra_0|>"]),
                                      "add_special": False, "parse_special": True}
        assert backend.calls[2][1]["n_predict"] == 512
        assert backend.calls[2][1]["prompt"] == [127958, 10, 11]
        assert (await client.post("/v1/translate", json={**body, "extra": 1}, headers=headers)).status_code == 400
        assert (await client.post("/v1/translate", json={**body, "source_lang": "ja", "target_lang": "en"}, headers=headers)).status_code == 400
        assert (await client.post("/v1/translate", json={**body, "text": " "}, headers=headers)).status_code == 400
        invalid_unicode = b'{"text":"\\ud800","source_lang":"vi","target_lang":"ja"}'
        assert (await client.post("/v1/translate", content=invalid_unicode, headers={**headers, "Content-Type": "application/json"})).status_code == 400
        duplicate = b'{"text":"one","text":"two","source_lang":"vi","target_lang":"ja"}'
        assert (await client.post("/v1/translate", content=duplicate, headers={**headers, "Content-Type": "application/json"})).status_code == 400
        oversized = b'{"text":"' + b"a" * (128 * 1024) + b'","source_lang":"vi","target_lang":"ja"}'
        assert (await client.post("/v1/translate", content=oversized, headers={**headers, "Content-Type": "application/json"})).status_code == 413
        same = await client.post("/v1/translate", json={**body, "target_lang": "vi"}, headers=headers)
        assert same.json() == {"translated_text": "Xin chào", "model": PROFILE.model}
        assert len(backend.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("response_patch", [
    {"stop_type": "limit"}, {"truncated": True}, {"tokens": [42, 9]},
    {"tokens": []}, {"content": "  "}, {"content": "x" * (256 * 1024)},
    {"model": "wrong"},
])
async def test_only_eos_complete_bounded_output_returns_200(tmp_path, response_patch):
    backend = FakeBackend()
    runtime = TranslationRuntime(settings(tmp_path), backend)
    original = backend.post_json

    async def patched(path, body, deadline):
        response = await original(path, body, deadline)
        if path == "/completion":
            response.update(response_patch)
        return response

    backend.post_json = patched
    with pytest.raises(Exception) as exc:
        await runtime.translate("a", "vi", "ja", deadline=time.monotonic() + 1)
    assert type(exc.value).__name__ == "ServiceFailure"
    assert backend.reaped == 0  # Fully received malformed result is drained.


@pytest.mark.asyncio
async def test_single_generation_bounded_wait_and_absolute_deadline(tmp_path):
    cfg = settings(tmp_path)
    backend = FakeBackend()
    backend.release.clear()
    runtime = TranslationRuntime(cfg, backend)
    first = asyncio.create_task(runtime.translate("one", "vi", "ja", deadline=time.monotonic() + 1))
    await backend.completion_started.wait()
    second = asyncio.create_task(runtime.translate("two", "vi", "ja", deadline=time.monotonic() + .08))
    await asyncio.sleep(.01)
    with pytest.raises(Exception) as exc:
        await runtime.translate("three", "vi", "ja", deadline=time.monotonic() + 1)
    assert getattr(exc.value, "status", None) == 429
    with pytest.raises(Exception) as exc:
        await second
    assert getattr(exc.value, "status", None) == 504
    assert backend.reaped == 0  # This waiter never owned the active generation.
    assert len([c for c in backend.calls if c[0] == "/completion"]) == 1
    backend.release.set()
    assert await first == "Bản dịch"


@pytest.mark.asyncio
async def test_disconnect_reaps_owned_child_before_slot_reuse(tmp_path):
    backend = FakeBackend()
    backend.release.clear()
    runtime = TranslationRuntime(settings(tmp_path), backend)
    disconnected = asyncio.Event()
    task = asyncio.create_task(runtime.translate("one", "vi", "ja", deadline=time.monotonic() + 1,
                                                 disconnected=disconnected))
    await backend.completion_started.wait()
    disconnected.set()
    with pytest.raises(Exception) as exc:
        await task
    assert getattr(exc.value, "status", None) == 503
    assert backend.reaped == 1
    assert await runtime.translate("two", "vi", "ja", deadline=time.monotonic() + 1) == "Bản dịch"
    assert backend.start_calls == 1
    assert len([c for c in backend.calls if c[0] == "/completion"]) == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_reap_active_generation(tmp_path):
    backend = FakeBackend()
    backend.release.clear()
    runtime = TranslationRuntime(settings(tmp_path), backend)
    first = asyncio.create_task(runtime.translate("one", "vi", "ja", deadline=time.monotonic() + 1))
    await backend.completion_started.wait()
    disconnected = asyncio.Event()
    second = asyncio.create_task(runtime.translate("two", "vi", "ja",
                                                   deadline=time.monotonic() + 1,
                                                   disconnected=disconnected))
    await asyncio.sleep(.01)
    disconnected.set()
    done, _ = await asyncio.wait({second}, timeout=.15)
    assert done, "disconnected waiter held the only queue position"
    assert runtime.waiting == 0
    assert backend.reaped == 0
    with pytest.raises(ServiceFailure) as exc:
        await second
    assert exc.value.status == 503
    backend.release.set()
    assert await first == "Bản dịch"
    assert backend.reaped == 0
    assert backend.start_calls == 0


@pytest.mark.asyncio
async def test_timeout_reap_failure_poison_and_keep_reservation(tmp_path):
    backend = FakeBackend()
    backend.release.clear()
    backend.fail_reap = True
    runtime = TranslationRuntime(settings(tmp_path), backend)
    with pytest.raises(Exception) as exc:
        await runtime.translate("one", "vi", "ja", deadline=time.monotonic() + .06)
    assert getattr(exc.value, "status", None) == 503
    assert backend.reaped == 1
    assert runtime.poisoned
    assert runtime.slot.locked()
    with pytest.raises(ServiceFailure):
        await runtime.translate("two", "vi", "ja", deadline=time.monotonic() + 1)
    assert backend.start_calls == 0


@pytest.mark.asyncio
async def test_restart_failure_backoff_is_bounded_and_not_poisoned(tmp_path):
    backend = FakeBackend()
    backend.live = False  # A previous request already reaped this owned child.
    backend.fail_starts = 2
    runtime = TranslationRuntime(settings(tmp_path), backend)
    with pytest.raises(ServiceFailure) as exc:
        await runtime.translate("one", "vi", "ja", deadline=time.monotonic() + 1)
    assert exc.value.status == 503
    assert backend.start_calls == 1
    assert not runtime.poisoned
    first_wait = runtime.restart_after - time.monotonic()
    assert 0 < first_wait <= 2
    with pytest.raises(ServiceFailure):
        await runtime.translate("two", "vi", "ja", deadline=time.monotonic() + 1)
    assert backend.start_calls == 1  # Backoff rejects without another spawn.
    runtime.restart_after = time.monotonic() - 1
    with pytest.raises(ServiceFailure):
        await runtime.translate("three", "vi", "ja", deadline=time.monotonic() + 1)
    assert backend.start_calls == 2
    assert 0 < runtime.restart_after - time.monotonic() <= 4
    runtime.restart_after = time.monotonic() - 1
    assert await runtime.translate("four", "vi", "ja", deadline=time.monotonic() + 1) == "Bản dịch"
    assert backend.start_calls == 3
    assert runtime.restart_failures == 0


@pytest.mark.asyncio
async def test_uncancellable_http_task_does_not_delay_owned_process_reap(tmp_path):
    backend = FakeBackend(ignore_cancel=True)
    backend.release.clear()
    runtime = TranslationRuntime(settings(tmp_path), backend)
    task = asyncio.create_task(runtime.translate("one", "vi", "ja", deadline=time.monotonic() + .04))
    await backend.completion_started.wait()
    done, _ = await asyncio.wait({task}, timeout=.18)
    if not done:
        backend.release.set()  # cleanup even if regression occurs
        await task
    assert done, "request waited for remote task before reaping its own child"
    assert backend.reaped == 1


def _write_gguf(path: Path, *, bos=127958, eos=3, template="template"):
    entries = {"tokenizer.ggml.bos_token_id": (4, struct.pack("<I", bos)),
               "tokenizer.ggml.eos_token_id": (4, struct.pack("<I", eos)),
               "tokenizer.chat_template": (8, struct.pack("<Q", len(template.encode())) + template.encode()),
               "test.array": (9, struct.pack("<IQ", 4, 2) + struct.pack("<II", 1, 2))}
    with path.open("wb") as f:
        f.write(b"GGUF" + struct.pack("<IQQ", 3, 0, len(entries)))
        for key, (kind, value) in entries.items():
            f.write(struct.pack("<Q", len(key)) + key.encode() + struct.pack("<I", kind) + value)


def test_hash_and_gguf_metadata_attestation_fail_closed(tmp_path):
    model = tmp_path / "m.gguf"
    binary = tmp_path / "llama-server"
    _write_gguf(model)
    binary.write_bytes(b"fake binary")
    cfg = settings(tmp_path)
    candidate = Candidate(key="test", model=PROFILE.model, revision="pinned",
                          gguf_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                          server_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                          bos_token_id=127958, gguf_eos_token_id=3,
                          terminal_token_ids=frozenset({3}),
                          chat_template_sha256=hashlib.sha256(b"template").hexdigest())
    attest_artifacts(model, binary, candidate)
    with pytest.raises(ValueError):
        attest_artifacts(model, binary, Candidate(**{**candidate.__dict__, "gguf_eos_token_id": 120020}))
    with pytest.raises(ValueError):
        attest_artifacts(model, binary, Candidate(**{**candidate.__dict__, "chat_template_sha256": "0" * 64}))
    binary.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        attest_artifacts(model, binary, candidate)


def test_7b_official_eog_override_is_pinned_separately_from_raw_gguf(tmp_path):
    cfg = settings(tmp_path)
    cfg = ServiceSettings(**{**cfg.__dict__, "candidate": CANDIDATES["7b-q8"]})
    command = OwnedLlamaServer(cfg).command()
    assert command.count("--override-kv") == 1
    assert command[command.index("--override-kv") + 1] == (
        "tokenizer.ggml.eos_token_id=int:127960,tokenizer.ggml.eot_token_id=int:127967")
    assert "--n-gpu-layers" in command and command[command.index("--n-gpu-layers") + 1] == "0"
    assert command[command.index("--api-key-file") + 1] == str(cfg.token_file)
    assert cfg.token not in command
    assert CANDIDATES["7b-q8"].gguf_eos_token_id == 3
    assert CANDIDATES["7b-q8"].terminal_token_ids == {127960, 127967}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal,accepted", [(3, False), (127960, True), (127967, True)])
async def test_7b_dollar_token_is_not_eog(tmp_path, terminal, accepted):
    candidate = CANDIDATES["7b-q8"]
    cfg = settings(tmp_path)
    cfg = ServiceSettings(**{**cfg.__dict__, "candidate": candidate})
    backend = FakeBackend(terminal=terminal, profile=candidate)
    runtime = TranslationRuntime(cfg, backend)
    if accepted:
        assert await runtime.translate("a", "vi", "ja", deadline=time.monotonic() + 1) == "Bản dịch"
    else:
        with pytest.raises(ServiceFailure) as exc:
            await runtime.translate("a", "vi", "ja", deadline=time.monotonic() + 1)
        assert exc.value.code == "INCOMPLETE_GENERATION"


@pytest.mark.asyncio
async def test_hash_mismatch_prevents_owned_child_spawn(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    cfg = ServiceSettings(**{**cfg.__dict__, "lock_path": tmp_path / "owner.lock"})
    cfg.model_path.write_bytes(b"not the pinned model")
    cfg.server_path.write_bytes(b"not the pinned binary")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("child started before attestation"))
    server = OwnedLlamaServer(cfg)
    with pytest.raises(ValueError):
        await server.start()
    assert server.proc is None
    assert server.lock_file is None


@pytest.mark.asyncio
async def test_reap_waits_for_exact_owned_child(tmp_path):
    server = OwnedLlamaServer(settings(tmp_path))
    lock_file = (tmp_path / "owner.lock").open("a+b")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    server.lock_file = lock_file
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    server.proc = child
    server.live = True
    try:
        assert await server.reap()
        assert child.poll() is not None
        assert not server.live
        assert server.lock_file is lock_file
        probe = (tmp_path / "owner.lock").open("a+b")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            probe.close()
        await server.shutdown()
        assert server.lock_file is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


@pytest.mark.asyncio
async def test_expired_restart_deadline_never_spawns(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    cfg = ServiceSettings(**{**cfg.__dict__, "lock_path": tmp_path / "owner.lock"})
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned after deadline"))
    server = OwnedLlamaServer(cfg)
    with pytest.raises(TimeoutError):
        await server.start(deadline=time.monotonic() - 1)
    await server.shutdown()


@pytest.mark.asyncio
async def test_dead_child_proof_survives_http_client_close_error(tmp_path):
    server = OwnedLlamaServer(settings(tmp_path))
    await server.client.aclose()
    class BrokenClose:
        async def aclose(self):
            raise OSError("client close failed")
    server.client = BrokenClose()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    server.proc = child
    server.live = True
    try:
        assert await server.reap()
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


def test_listener_proof_is_owned_socket_inode():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert _owns_listen_port(os.getpid(), port)
        assert not _owns_listen_port(os.getpid(), port + 1)


@pytest.mark.parametrize("ignore_term", [True, False])
def test_abrupt_parent_death_retains_lock_until_child_death(tmp_path, ignore_term):
    """Tiny Python process only: never launches llama-server or loads weights."""
    helper = Path(__file__).parents[1] / "deploy" / "mt_cpu_service" / "child_supervisor.py"
    lock = tmp_path / "owner.lock"
    ready = tmp_path / "ready"
    child_code = (
        "import pathlib,signal,time;"
        + ("signal.signal(signal.SIGTERM,signal.SIG_IGN);" if ignore_term else "")
        + f"pathlib.Path({str(ready)!r}).write_text('ready');time.sleep(30)"
    )
    parent_code = (
        "import fcntl,os,pathlib,subprocess,sys,time;"
        + f"f=open({str(lock)!r},'a+b');fcntl.flock(f,fcntl.LOCK_EX);"
        + f"p=subprocess.Popen([sys.executable,{str(helper)!r},str(os.getpid()),"
        + "sys.executable,'-c',"
        + repr(child_code) + "],pass_fds=(f.fileno(),),close_fds=True);"
        + f"r=pathlib.Path({str(ready)!r});"
        + "\nwhile not r.exists():\n time.sleep(.01)\n"
        + "print(p.pid,flush=True);time.sleep(30)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    child_pid = None
    try:
        readable, _, _ = select.select([parent.stdout], [], [], 4)
        assert readable, "tiny child did not become ready"
        child_pid = int(parent.stdout.readline())
        parent.kill()
        parent.wait(timeout=3)
        probe = lock.open("a+b")
        try:
            if ignore_term:
                os.kill(child_pid, 0)  # Still alive after parent SIGKILL.
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                end = time.monotonic() + 3
                while time.monotonic() < end:
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(.02)
                else:
                    pytest.fail("PDEATHSIG did not terminate tiny child")
        finally:
            probe.close()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_backend_redirect_is_not_followed(tmp_path):
    server = OwnedLlamaServer(settings(tmp_path))
    paths = []

    def responder(request):
        paths.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer private-token"
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:18865/other"})

    await server.client.aclose()
    server.client = httpx.AsyncClient(transport=httpx.MockTransport(responder),
                                      base_url="http://127.0.0.1:18865",
                                      trust_env=False, follow_redirects=False)
    class Running:
        def poll(self):
            return None
    server.proc = Running()
    server.live = True
    with pytest.raises(ServiceFailure) as exc:
        await server.post_json("/completion", {"prompt": [1]}, time.monotonic() + 1)
    assert exc.value.uncertain
    assert paths == ["/completion"]
    await server.client.aclose()
