from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import EXPECTED_MODEL
from .errors import TranslationError


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    max_output_tokens: int
    temperature: float = 0.1
    top_p: float = 0.9


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
            if data.get("model") != EXPECTED_MODEL:
                raise TranslationError("MODEL_UNAVAILABLE")
            choice = data["choices"][0]
            message = choice["message"]
            if message.get("tool_calls") or message.get("function_call"):
                raise TranslationError("OUTPUT_VALIDATION_FAILED")
            content = message["content"]
            if not isinstance(content, str):
                raise ValueError("invalid content")
            usage = data.get("usage") or {}
            return GenerationResult(content, choice.get("finish_reason", ""), data["model"],
                                    data.get("model_revision"), usage.get("prompt_tokens"),
                                    usage.get("completion_tokens"))
        except TranslationError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise TranslationError("MODEL_UNAVAILABLE") from None
