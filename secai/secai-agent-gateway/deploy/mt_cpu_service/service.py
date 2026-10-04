"""Owned llama-server sidecar for the existing structured translation clients.

Importing this module never starts a model. Use the FastAPI lifespan to attest and
start one owned CPU subprocess; a failed/uncertain request reaps that subprocess.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import struct
import subprocess
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

_LANGS = frozenset(("vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"))
_NAMES = {"vi": "Vietnamese", "en": "English", "zh": "Simplified Chinese",
          "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German",
          "es": "Spanish", "ru": "Russian", "ar": "Modern Standard Arabic"}
_MAX_BODY = 128 * 1024
_MAX_RESULT = 256 * 1024
_MAX_TEXT = 6000
_MAX_INPUT_TOKENS = 1024
_MAX_OUTPUT_TOKENS = 512
_CONTEXT = 4096
_PROMPT = ("Translate the following text into {target}. Note that you should only "
           "output the translated result without any additional explanation:\n\n{source}")
_SERVER_SHA = "e8253f2a2ad34cc36b4a2b874a6bf19c45ba867b89223e8a627f1ca0083db7b7"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


@dataclass(frozen=True)
class Candidate:
    key: str
    model: str  # exact value expected by TranslationAgent / SpecialistLlm
    revision: str
    gguf_sha256: str
    server_sha256: str
    bos_token_id: int
    gguf_eos_token_id: int
    terminal_token_ids: frozenset[int]
    chat_template_sha256: str
    runtime_overrides: tuple[str, ...] = ()


# Activation requires full-file hash and GGUF metadata attestation at startup.
# 7B GGUF has raw eos=3 ('$'); the pinned runtime must explicitly correct its
# EOG mapping to the official model's eos/eot IDs before any request is served.
CANDIDATES = {
    "1.8b-q8": Candidate(
        key="1.8b-q8", model="tencent/Hy-MT2-1.8B",
        revision="a0c709d9fac510f2c807aa3af52872340dc37a4a",
        gguf_sha256="5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4",
        server_sha256=_SERVER_SHA, bos_token_id=120000, gguf_eos_token_id=120020,
        terminal_token_ids=frozenset({120020}),
        chat_template_sha256="b7491ec0e9c869dfce20f2176758099bf248d979dd05530ede99deb21698acee"),
    "7b-q8": Candidate(
        key="7b-q8", model="tencent/Hy-MT2-7B",
        revision="ab8472660ac61fac25f1af43fac2599d52a8a775",
        gguf_sha256="58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0",
        server_sha256=_SERVER_SHA, bos_token_id=127958, gguf_eos_token_id=3,
        terminal_token_ids=frozenset({127960, 127967}),
        chat_template_sha256="788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677",
        runtime_overrides=("tokenizer.ggml.eos_token_id=int:127960",
                           "tokenizer.ggml.eot_token_id=int:127967")),
}


def _read_exact(handle, size: int) -> bytes:
    if size < 0 or size > 100_000_000:
        raise ValueError("GGUF metadata exceeds bound")
    value = handle.read(size)
    if len(value) != size:
        raise ValueError("truncated GGUF metadata")
    return value


def _integer(handle, fmt: str) -> int:
    return struct.unpack(fmt, _read_exact(handle, struct.calcsize(fmt)))[0]


def _gguf_string(handle) -> str:
    return _read_exact(handle, _integer(handle, "<Q")).decode("utf-8")


def _gguf_value(handle, kind: int, *, keep: bool = False) -> Any:
    sizes = {0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2),
             4: ("<I", 4), 5: ("<i", 4), 6: ("<f", 4), 7: ("<?", 1),
             10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8)}
    if kind in sizes:
        fmt, size = sizes[kind]
        if keep:
            return _integer(handle, fmt)
        handle.seek(size, os.SEEK_CUR)
        return None
    if kind == 8:
        length = _integer(handle, "<Q")
        if keep:
            return _read_exact(handle, length).decode("utf-8")
        if length > 100_000_000:
            raise ValueError("GGUF string exceeds bound")
        handle.seek(length, os.SEEK_CUR)
        return None
    if kind == 9:
        element_kind, count = _integer(handle, "<I"), _integer(handle, "<Q")
        if count > 10_000_000 or element_kind == 9:
            raise ValueError("GGUF array exceeds bound")
        if element_kind in sizes:
            handle.seek(sizes[element_kind][1] * count, os.SEEK_CUR)
        elif element_kind == 8:
            for _ in range(count):
                _gguf_value(handle, 8)
        else:
            raise ValueError("unsupported GGUF metadata type")
        return None
    raise ValueError("unsupported GGUF metadata type")


def gguf_metadata(path: Path) -> dict[str, Any]:
    """Read only the GGUF header, without materializing tensors or token arrays."""
    desired = {"tokenizer.ggml.bos_token_id", "tokenizer.ggml.eos_token_id",
               "tokenizer.chat_template"}
    found: dict[str, Any] = {}
    with path.open("rb") as handle:
        if _read_exact(handle, 4) != b"GGUF" or _integer(handle, "<I") != 3:
            raise ValueError("unsupported GGUF header")
        _integer(handle, "<Q")  # tensor count
        count = _integer(handle, "<Q")
        if count > 1_000_000:
            raise ValueError("GGUF metadata count exceeds bound")
        for _ in range(count):
            key = _gguf_string(handle)
            kind = _integer(handle, "<I")
            value = _gguf_value(handle, kind, keep=key in desired)
            if key in desired:
                if key in found:
                    raise ValueError("duplicate GGUF metadata")
                found[key] = value
    if set(found) != desired:
        raise ValueError("missing GGUF tokenizer metadata")
    return found


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def attest_artifacts(model_path: Path, server_path: Path, candidate: Candidate) -> None:
    """Fail closed before spawning if either artifact or tokenizer metadata differs."""
    if (_sha256_file(model_path) != candidate.gguf_sha256 or
            _sha256_file(server_path) != candidate.server_sha256):
        raise ValueError("artifact hash mismatch")
    meta = gguf_metadata(model_path)
    template = meta["tokenizer.chat_template"]
    if (not candidate.chat_template_sha256 or candidate.bos_token_id < 0 or
            not candidate.terminal_token_ids or
            meta["tokenizer.ggml.bos_token_id"] != candidate.bos_token_id or
            meta["tokenizer.ggml.eos_token_id"] != candidate.gguf_eos_token_id or
            not isinstance(template, str) or
            hashlib.sha256(template.encode()).hexdigest() != candidate.chat_template_sha256):
        raise ValueError("GGUF tokenizer metadata mismatch")


@dataclass(frozen=True)
class ServiceSettings:
    candidate: Candidate
    model_path: Path
    server_path: Path
    token: str
    token_file: Path | None = None
    timeout_seconds: float = 105.0
    max_waiters: int = 1
    server_port: int = 18865
    server_threads: int = 8
    lock_path: Path = Path("/tmp/secai-hymt-cpu-service.lock")

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        key = os.getenv("MT_CPU_CANDIDATE", "")
        if key not in CANDIDATES:
            raise ValueError("choose a pinned Hy-MT candidate")
        token_file = Path(os.environ["MT_CPU_TOKEN_FILE"])
        if token_file.stat().st_mode & 0o077:
            raise ValueError("token file must be private")
        token = token_file.read_text(encoding="utf-8").removesuffix("\n")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("invalid private token")
        port = int(os.getenv("MT_CPU_SERVER_PORT", "18865"))
        threads = int(os.getenv("MT_CPU_THREADS", "8"))
        if not 1024 <= port <= 65535 or not 1 <= threads <= 32:
            raise ValueError("invalid CPU service settings")
        return cls(candidate=CANDIDATES[key], model_path=Path(os.environ["MT_CPU_MODEL_PATH"]),
                   server_path=Path(os.environ["MT_CPU_LLAMA_SERVER_PATH"]), token=token,
                   token_file=token_file,
                   server_port=port, server_threads=threads,
                   lock_path=Path(os.getenv("MT_CPU_LOCK_PATH", "/tmp/secai-hymt-cpu-service.lock")))


class ServiceFailure(Exception):
    def __init__(self, status: int, code: str, *, uncertain: bool = False):
        super().__init__(code)
        self.status = status
        self.code = code
        self.uncertain = uncertain


class OwnedLlamaServer:
    """One owner PID, one model; never kills unrelated server processes."""

    def __init__(self, settings: ServiceSettings):
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self.lock_file = None
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{settings.server_port}",
                                        trust_env=False, follow_redirects=False)
        self.live = False
        self._attest_task: asyncio.Task | None = None

    def command(self) -> list[str]:
        cfg = self.settings
        if cfg.token_file is None:
            raise ValueError("private child token file is required")
        argv = [str(cfg.server_path), "--model", str(cfg.model_path), "--host", "127.0.0.1",
                "--port", str(cfg.server_port), "--threads", str(cfg.server_threads),
                "--threads-batch", str(cfg.server_threads), "--parallel", "1",
                "--ctx-size", str(_CONTEXT), "--n-gpu-layers", "0", "--no-webui",
                "--alias", f"{cfg.candidate.model}@{cfg.candidate.revision}",
                "--api-key-file", str(cfg.token_file)]
        if cfg.candidate.runtime_overrides:
            argv.extend(("--override-kv", ",".join(cfg.candidate.runtime_overrides)))
        return argv

    async def start(self, deadline: float | None = None) -> None:
        if self.settings.token_file is None:
            raise ValueError("private child token file is required")
        if self.proc is not None or self.live:
            raise RuntimeError("owned model process has not been reaped")
        ready_deadline = min(deadline if deadline is not None else float("inf"),
                             time.monotonic() + 120)
        if ready_deadline <= time.monotonic():
            raise TimeoutError("restart deadline expired")
        had_lock = self.lock_file is not None
        try:
            if self.lock_file is None:
                lock_path = self.settings.lock_path
                lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                lock_file = lock_path.open("a+b")
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BaseException:
                    lock_file.close()
                    raise
                self.lock_file = lock_file
            if self._attest_task is None:
                self._attest_task = asyncio.create_task(asyncio.to_thread(
                    attest_artifacts, self.settings.model_path,
                    self.settings.server_path, self.settings.candidate))
            attestation = self._attest_task
            try:
                await asyncio.wait_for(asyncio.shield(attestation),
                                       timeout=max(0, ready_deadline - time.monotonic()))
            finally:
                if attestation.done():
                    self._attest_task = None
            if ready_deadline <= time.monotonic():
                raise TimeoutError("restart deadline expired")
            if self.client.is_closed:
                self.client = httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{self.settings.server_port}",
                    trust_env=False, follow_redirects=False)
            self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, close_fds=True,
                                         env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                              "LC_ALL": "C", "CUDA_VISIBLE_DEVICES": ""})
            while time.monotonic() < ready_deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError("owned model process exited before readiness")
                try:
                    response = await self.client.get("/health", timeout=min(1, ready_deadline - time.monotonic()), headers={
                        "Authorization": f"Bearer {self.settings.token}"})
                    if response.status_code == 200:
                        self.live = True
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(min(.2, max(0, ready_deadline - time.monotonic())))
            raise RuntimeError("owned model process did not become ready")
        except BaseException:
            cleanup = asyncio.create_task(self.reap())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if (not had_lock and cleanup.done() and not cleanup.cancelled() and
                    cleanup.exception() is None and cleanup.result()):
                self._unlock()
            raise

    async def post_json(self, path: str, body: dict, deadline: float) -> dict:
        if not self.live or self.proc is None or self.proc.poll() is not None:
            raise ServiceFailure(503, "UNAVAILABLE")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        async with self.client.stream("POST", path, content=payload, timeout=remaining,
                                      headers={"Content-Type": "application/json",
                                               "Authorization": f"Bearer {self.settings.token}"}) as response:
            if response.status_code != 200:
                raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > _MAX_RESULT:
                    raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
        try:
            result = json.loads(data, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError):
            raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True) from None
        if not isinstance(result, dict):
            raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
        return result

    async def reap(self) -> bool:
        self.live = False
        proc = self.proc
        if proc is None:
            with suppress(Exception):
                await self.client.aclose()
            return True
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    await asyncio.to_thread(proc.wait, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            return False
        self.proc = None
        with suppress(Exception):
            await self.client.aclose()
        return True

    async def shutdown(self) -> bool:
        """Release the cross-instance lock only after the owned PID is gone."""
        reaped = await self.reap()
        if reaped:
            self._unlock()
        return reaped

    def _unlock(self) -> None:
        if self.lock_file is not None:
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None


class TranslationRuntime:
    def __init__(self, settings: ServiceSettings, backend: Any):
        self.settings = settings
        self.backend = backend
        self.slot = asyncio.Lock()
        self.admission = asyncio.Lock()
        self.waiting = 0
        self.poisoned = False
        self.restart_failures = 0
        self.restart_after = 0.0

    async def _reap_after_uncertain_call(self) -> None:
        cleanup = asyncio.create_task(self.backend.reap())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if cleanup.cancelled() or cleanup.exception() or not cleanup.result():
            self.poisoned = True

    async def _ensure_live(self, deadline: float) -> None:
        if self.backend.live:
            return
        now = time.monotonic()
        if now < self.restart_after:
            raise ServiceFailure(503, "UNAVAILABLE")
        if now >= deadline:
            raise ServiceFailure(504, "TIMEOUT")
        try:
            await self.backend.start(deadline=deadline)
            if time.monotonic() >= deadline:
                raise TimeoutError("restart exceeded request deadline")
        except (Exception, asyncio.CancelledError):
            await self._reap_after_uncertain_call()
            if self.poisoned:
                raise ServiceFailure(503, "UNAVAILABLE") from None
            self.restart_failures = min(self.restart_failures + 1, 6)
            self.restart_after = time.monotonic() + min(2 ** (self.restart_failures - 1), 30)
            if time.monotonic() >= deadline:
                raise ServiceFailure(504, "TIMEOUT") from None
            raise ServiceFailure(503, "UNAVAILABLE") from None
        self.restart_failures = 0
        self.restart_after = 0.0

    async def translate(self, text: str, source_lang: str, target_lang: str,
                        *, deadline: float, disconnected: asyncio.Event | None = None) -> str:
        if self.poisoned:
            raise ServiceFailure(503, "UNAVAILABLE")
        async with self.admission:
            if self.waiting >= self.settings.max_waiters and (self.slot.locked() or self.waiting):
                raise ServiceFailure(429, "BUSY")
            self.waiting += 1
        acquired = False
        remote_started = False
        try:
            await asyncio.wait_for(self.slot.acquire(), timeout=max(0, deadline - time.monotonic()))
            acquired = True
        except asyncio.TimeoutError:
            raise ServiceFailure(504, "TIMEOUT") from None
        finally:
            async with self.admission:
                self.waiting -= 1
        try:
            if self.poisoned:
                raise ServiceFailure(503, "UNAVAILABLE")
            if disconnected is not None and disconnected.is_set():
                raise ServiceFailure(503, "DISCONNECTED")
            await self._ensure_live(deadline)
            remote_started = True
            work = asyncio.create_task(self._translate_inner(text, target_lang, deadline))
            def consume_late_result(task: asyncio.Task) -> None:
                with suppress(asyncio.CancelledError):
                    task.exception()
            work.add_done_callback(consume_late_result)
            watcher = asyncio.create_task(disconnected.wait()) if disconnected is not None else None
            try:
                pending = {work} | ({watcher} if watcher else set())
                done, _ = await asyncio.wait(pending, timeout=max(0, deadline - time.monotonic()),
                                              return_when=asyncio.FIRST_COMPLETED)
                if work in done:
                    return await work
                if watcher in done:
                    raise ServiceFailure(503, "DISCONNECTED")
                raise ServiceFailure(504, "TIMEOUT")
            finally:
                if watcher is not None:
                    watcher.cancel()
                if not work.done():
                    work.cancel()
        except (asyncio.CancelledError, asyncio.TimeoutError, httpx.TransportError):
            if remote_started:
                await self._reap_after_uncertain_call()
            if self.poisoned:
                raise ServiceFailure(503, "UNAVAILABLE") from None
            raise ServiceFailure(504, "TIMEOUT") from None
        except ServiceFailure as exc:
            if remote_started and (exc.uncertain or exc.code in {"TIMEOUT", "DISCONNECTED", "UNAVAILABLE"}):
                await self._reap_after_uncertain_call()
            if self.poisoned:
                raise ServiceFailure(503, "UNAVAILABLE") from None
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, UnicodeError):
            if remote_started:
                await self._reap_after_uncertain_call()
            raise ServiceFailure(503 if self.poisoned else 502, "UNAVAILABLE" if self.poisoned else "BAD_UPSTREAM") from None
        finally:
            if acquired and not self.poisoned:
                self.slot.release()

    async def _translate_inner(self, text: str, target_lang: str, deadline: float) -> str:
        prompt = _PROMPT.format(target=_NAMES[target_lang], source=text)
        formatted = await self.backend.post_json("/apply-template", {
            "messages": [{"role": "user", "content": prompt}], "add_generation_prompt": True}, deadline)
        formatted_prompt = formatted.get("prompt")
        if not isinstance(formatted_prompt, str) or not formatted_prompt or len(formatted_prompt) > _MAX_BODY:
            raise ServiceFailure(502, "BAD_UPSTREAM")
        tokenized = await self.backend.post_json("/tokenize", {
            "content": formatted_prompt, "add_special": False, "parse_special": True}, deadline)
        tokens = tokenized.get("tokens")
        if (not isinstance(tokens, list) or not 1 <= len(tokens) <= _MAX_INPUT_TOKENS or
                any(type(token) is not int or token < 0 for token in tokens) or
                tokens[0] != self.settings.candidate.bos_token_id or
                len(tokens) + _MAX_OUTPUT_TOKENS > _CONTEXT):
            raise ServiceFailure(413, "INPUT_TOO_LONG")
        completion = await self.backend.post_json("/completion", {
            "prompt": tokens, "n_predict": _MAX_OUTPUT_TOKENS, "temperature": 0,
            "top_k": 40, "top_p": .95, "min_p": .05, "repeat_penalty": 1,
            "seed": 42, "stream": False, "cache_prompt": False, "return_tokens": True}, deadline)
        content = completion.get("content")
        generated = completion.get("tokens")
        if (not isinstance(content, str) or not content.strip() or
                len(json.dumps(completion, ensure_ascii=False).encode("utf-8")) > _MAX_RESULT or
                completion.get("model") != f"{self.settings.candidate.model}@{self.settings.candidate.revision}" or
                completion.get("stop_type") != "eos" or completion.get("truncated") is not False or
                not isinstance(generated, list) or not 1 <= len(generated) <= _MAX_OUTPUT_TOKENS or
                any(type(token) is not int or token < 0 for token in generated) or
                generated[-1] not in self.settings.candidate.terminal_token_ids):
            raise ServiceFailure(502, "INCOMPLETE_GENERATION")
        return content


def _valid_request(body: Any) -> bool:
    if not isinstance(body, dict) or set(body) != {"text", "source_lang", "target_lang"}:
        return False
    text, source, target = body["text"], body["source_lang"], body["target_lang"]
    return (isinstance(text, str) and 0 < len(text) <= _MAX_TEXT and bool(text.strip()) and
            bool(text.encode("utf-8")) and
            isinstance(source, str) and isinstance(target, str) and
            source in _LANGS and target in _LANGS and
            (source == target or "vi" in (source, target)))


def create_app(settings: ServiceSettings | None = None,
               runtime: TranslationRuntime | None = None) -> FastAPI:
    settings = settings or ServiceSettings.from_env()
    backend = None if runtime is not None else OwnedLlamaServer(settings)
    runtime = runtime or TranslationRuntime(settings, backend)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            if backend is not None:
                await backend.start()
            yield
        finally:
            if backend is not None:
                await backend.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/translate")
    async def translate(request: Request):
        started = time.monotonic()
        deadline = started + settings.timeout_seconds
        def error(status: int, code: str):
            return JSONResponse(status_code=status, content={"error": code},
                                headers={"Cache-Control": "no-store"})

        try:
            loopback = ipaddress.ip_address(request.client.host).is_loopback if request.client else False
        except ValueError:
            loopback = False
        if not loopback:
            return error(403, "FORBIDDEN")

        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization.encode("utf-8"),
                                   f"Bearer {settings.token}".encode("utf-8")):
            return error(401, "UNAUTHORIZED")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return error(400, "BAD_REQUEST")
        async def read_bounded_body() -> bytearray:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > _MAX_BODY:
                    raise ServiceFailure(413, "INPUT_TOO_LONG")
            return data
        try:
            data = await asyncio.wait_for(read_bounded_body(),
                                          timeout=max(0, deadline - time.monotonic()))
            body = json.loads(data, object_pairs_hook=_unique_object)
            if not _valid_request(body):
                return error(400, "BAD_REQUEST")
            if body["source_lang"] == body["target_lang"]:
                return JSONResponse({"translated_text": body["text"], "model": settings.candidate.model},
                                    headers={"Cache-Control": "no-store"})
            disconnected = asyncio.Event()
            async def watch_disconnect():
                while not disconnected.is_set():
                    try:
                        lost = await request.is_disconnected()
                    except Exception:
                        lost = True
                    if lost:
                        disconnected.set()
                        break
                    await asyncio.sleep(.05)
            watcher = asyncio.create_task(watch_disconnect())
            try:
                result = await runtime.translate(body["text"], body["source_lang"],
                                                 body["target_lang"], deadline=deadline,
                                                 disconnected=disconnected)
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            return JSONResponse({"translated_text": result, "model": settings.candidate.model},
                                headers={"Cache-Control": "no-store"})
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return error(400, "BAD_REQUEST")
        except TimeoutError:
            return error(504, "TIMEOUT")
        except ClientDisconnect:
            return error(503, "DISCONNECTED")
        except ServiceFailure as exc:
            return error(exc.status, exc.code)

    return app
