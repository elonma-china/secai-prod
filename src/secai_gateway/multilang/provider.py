from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from .config import EXPECTED_MODEL, MultilangSettings
from .errors import TranslationError


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    max_output_tokens: int
    temperature: float = 0.1
    top_p: float = 0.9
    source_lang: str | None = None
    target_lang: str | None = None


@dataclass(frozen=True)
class GenerationResult:
    text: str
    stop_reason: str
    model: str
    model_revision: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class LlmPort(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class HttpLlm:
    """OpenAI-compatible no-tool transport to the incumbent endpoint only."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self.client = client
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        payload = {
            "model": EXPECTED_MODEL,
            "messages": [{"role": "system", "content": request.system},
                         {"role": "user", "content": request.user}],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            response = await self.client.post(
                self.url, json=payload, headers={"Authorization": f"Bearer {self.api_key}"})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid provider envelope")
            if data.get("model") != EXPECTED_MODEL:
                raise TranslationError("MODEL_UNAVAILABLE")
            if not isinstance(data.get("choices"), list) or not data["choices"]:
                raise ValueError("invalid choices")
            choice = data["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("invalid choice")
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            if message.get("tool_calls") or message.get("function_call"):
                raise TranslationError("OUTPUT_VALIDATION_FAILED")
            content = message["content"]
            if not isinstance(content, str):
                raise ValueError("invalid content")
            usage = data.get("usage")
            if usage is None:
                usage = {}
            if not isinstance(usage, dict):
                raise ValueError("invalid usage")
            return GenerationResult(content, choice.get("finish_reason", ""), data["model"],
                                    data.get("model_revision"), usage.get("prompt_tokens"),
                                    usage.get("completion_tokens"))
        except TranslationError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise TranslationError("MODEL_UNAVAILABLE") from None


class SpecialistLlm:
    """Private structured translator; HTTP 200 is an attested EOS-complete response."""

    def __init__(self, client: httpx.AsyncClient | None, settings: MultilangSettings) -> None:
        try:
            parsed = urlsplit(settings.specialist_base_url.rstrip("/"))
            port = parsed.port
        except ValueError:
            raise TranslationError("MODEL_UNAVAILABLE") from None
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
            loopback = address.is_loopback
            private = loopback or any(address in network for network in (
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("fc00::/7")))
        except ValueError:
            private = loopback = parsed.hostname == "localhost"
        if (not private or parsed.scheme not in ("http", "https")
                or (parsed.scheme == "http" and not loopback)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or port is None
                or not settings.specialist_api_key or not settings.specialist_model):
            raise TranslationError("MODEL_UNAVAILABLE")
        self.url = settings.specialist_base_url.rstrip("/") + "/v1/translate"
        self.client = client
        self.api_key = settings.specialist_api_key
        self.model = settings.specialist_model
        self.timeout = settings.specialist_timeout_seconds

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if (request.source_lang not in ("vi", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar")
                or request.target_lang not in ("vi", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar")
                or request.source_lang == request.target_lang
                or "vi" not in (request.source_lang, request.target_lang)):
            raise TranslationError("MODEL_UNAVAILABLE")
        body = {"text": request.user,
                "source_lang": "zh" if request.source_lang == "zh-Hans" else request.source_lang,
                "target_lang": "zh" if request.target_lang == "zh-Hans" else request.target_lang}
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not request.user or len(request.user) > 6000 or len(encoded) > 128 * 1024:
            raise TranslationError("MODEL_UNAVAILABLE")
        if self.client is None:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                return await self._post(client, encoded)
        return await self._post(self.client, encoded)

    async def _post(self, client: httpx.AsyncClient, encoded: bytes) -> GenerationResult:
        try:
            async with client.stream("POST", self.url, content=encoded, timeout=self.timeout,
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json"}) as response:
                if response.status_code != 200:
                    raise TranslationError("MODEL_UNAVAILABLE")
                data = bytearray()
                async for piece in response.aiter_bytes():
                    data.extend(piece)
                    if len(data) > 256 * 1024:
                        raise TranslationError("MODEL_UNAVAILABLE")
            payload = json.loads(data)
            if (not isinstance(payload, dict) or set(payload) != {"translated_text", "model"}
                    or not isinstance(payload["translated_text"], str)
                    or not payload["translated_text"].strip()
                    or payload["model"] != self.model):
                raise TranslationError("MODEL_UNAVAILABLE")
            return GenerationResult(payload["translated_text"], "stop", self.model)
        except TranslationError:
            raise
        except httpx.TimeoutException:
            raise TranslationError("TRANSLATION_TIMEOUT") from None
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise TranslationError("MODEL_UNAVAILABLE") from None
