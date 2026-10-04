"""Default-off private MT adapter to a root-owned, separately guarded Q4 server.

This adapter is for the serial GPU diagnostic only. The external guard owns the
model process and VRAM; an uncertain call poisons this adapter because it cannot
prove that external generation ended. OCR must remain stopped.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx

from deploy.mt_cpu_service.service import (
    Candidate, ServiceFailure, ServiceSettings, TranslationRuntime, _unique_object,
    create_app,
)

_REVISION = "ab8472660ac61fac25f1af43fac2599d52a8a775"
GUARD_ALIAS = f"tencent/Hy-MT2-7B-GGUF@{_REVISION}:Q4_K_M:gpu"
Q4_CANDIDATE = Candidate(
    key="7b-q4-gpu-diagnostic", model="tencent/Hy-MT2-7B", revision=_REVISION,
    gguf_sha256="9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
    server_sha256="968c20dc975560d48587321068931e6d5b1ab2e0430491ff37b38d6eca8a8239",
    bos_token_id=127958, gguf_eos_token_id=3,
    terminal_token_ids=frozenset({127960, 127967}),
    chat_template_sha256="788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677",
    runtime_overrides=("tokenizer.ggml.eos_token_id=int:127960",
                       "tokenizer.ggml.eot_token_id=int:127967"),
)
_UPSTREAM = "http://127.0.0.1:18767"  # root-owned tunnel to the guarded Vast process
_MAX_RESULT = 256 * 1024


class GuardedBackend:
    """HTTP client to the external guard; never starts or reaps its model."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.live = False

    async def start(self, deadline: float | None = None) -> None:
        remaining = max(0, (deadline or time.monotonic() + 5) - time.monotonic())
        if remaining <= 0:
            raise ServiceFailure(504, "TIMEOUT")
        response = await self.client.get("/health", timeout=min(5, remaining))
        if response.status_code != 200 or response.json().get("status") != "ok":
            raise ServiceFailure(503, "UNAVAILABLE")
        self.live = True

    async def post_json(self, path: str, body: dict, deadline: float) -> dict:
        if not self.live or path not in {"/apply-template", "/tokenize", "/completion"}:
            raise ServiceFailure(503, "UNAVAILABLE", uncertain=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceFailure(504, "TIMEOUT", uncertain=True)
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        async with self.client.stream("POST", path, content=payload, timeout=remaining,
                                      headers={"Content-Type": "application/json"}) as response:
            if response.status_code != 200:
                raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > _MAX_RESULT:
                    raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
        try:
            result = json.loads(data, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError):
            raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True) from None
        if not isinstance(result, dict):
            raise ServiceFailure(502, "BAD_UPSTREAM", uncertain=True)
        if path == "/completion":
            tokens = result.get("tokens")
            if (result.get("model") != GUARD_ALIAS or result.get("stop_type") != "eos"
                    or result.get("truncated") is not False
                    or not isinstance(result.get("content"), str)
                    or not result["content"].strip()
                    or not isinstance(tokens, list) or not tokens
                    or type(tokens[-1]) is not int
                    or tokens[-1] not in Q4_CANDIDATE.terminal_token_ids):
                raise ServiceFailure(502, "INCOMPLETE_GENERATION", uncertain=True)
            # The private gateway contract uses the base model name. Normalize
            # only after checking the exact guarded Q4 runtime alias.
            result["model"] = f"{Q4_CANDIDATE.model}@{Q4_CANDIDATE.revision}"
        return result

    async def reap(self) -> bool:
        # The guard is owned by root, not this process. An uncertain started
        # call cannot be retried or followed by another generation here.
        self.live = False
        return False


def diagnostic_settings(*, token: str, token_file: Path | None) -> ServiceSettings:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
        raise ValueError("invalid private token")
    return ServiceSettings(candidate=Q4_CANDIDATE,
                           model_path=Path("/external-guard-owned-model"),
                           server_path=Path("/external-guard-owned-server"),
                           token=token, token_file=token_file,
                           timeout_seconds=105, max_waiters=1,
                           server_port=18767)


def create_diagnostic_app(settings: ServiceSettings, backend: GuardedBackend):
    # Reviewed CPU runtime enforces exact 3-field request, Bearer auth,
    # one generation slot, full rendered prompt <=1024 input tokens, <=512
    # output tokens, and exact EOS. Thus total <=1536 < Q4 n_ctx=2048.
    runtime = TranslationRuntime(settings, backend)
    return create_app(settings, runtime)


def create_gpu_app():
    """Uvicorn --factory entrypoint; explicit flag and private token required."""
    if os.getenv("GPU_MT_DIAGNOSTIC_ENABLED") != "1":
        raise RuntimeError("GPU MT diagnostic disabled")
    path = Path(os.environ["GPU_MT_DIAGNOSTIC_TOKEN_FILE"])
    if path.stat().st_mode & 0o077:
        raise ValueError("token file must be private")
    token = path.read_text(encoding="utf-8").removesuffix("\n")
    settings = diagnostic_settings(token=token, token_file=path)
    client = httpx.AsyncClient(base_url=_UPSTREAM, trust_env=False,
                               follow_redirects=False)
    return create_diagnostic_app(settings, GuardedBackend(client))
