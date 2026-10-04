"""Private, structured HTTP adapter for the dedicated translation model."""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

import httpx

from .settings import TranslationSettings
from .types import TargetLang, TranslationError

_LANGS = frozenset(("vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"))
_MAX_REQUEST = 128 * 1024
_MAX_RESPONSE = 256 * 1024


class TranslationAgent:
    def __init__(self, settings: TranslationSettings):
        self.settings = settings
        parsed = urlsplit(settings.specialist_base_url.rstrip("/"))
        try:
            private = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or bool(
                parsed.hostname and ipaddress.ip_address(parsed.hostname).is_private)
        except ValueError:
            private = parsed.hostname == "localhost"
        if (not private or parsed.scheme not in {"http", "https"} or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in {"", "/"} or not settings.specialist_api_key or
                not settings.specialist_model):
            raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation service is not configured.")
        self.url = settings.specialist_base_url.rstrip("/") + "/v1/translate"

    async def translate(self, text: str, source_lang: TargetLang,
                        target_lang: TargetLang, *, timeout_seconds: float | None = None) -> str:
        if (source_lang not in _LANGS or target_lang not in _LANGS or
                source_lang != target_lang and "vi" not in {source_lang, target_lang} or
                not text or len(text) > 6000):
            raise TranslationError("TRANSLATION_UNAVAILABLE", "Invalid translation input.")
        body = {"text": text, "source_lang": source_lang, "target_lang": target_lang}
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > _MAX_REQUEST:
            raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation input exceeds limit.")
        timeout = min(self.settings.specialist_timeout_seconds,
                      timeout_seconds if timeout_seconds is not None else 120)
        if timeout <= 0:
            raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.")
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream("POST", self.url, content=encoded, headers={
                        "Authorization": f"Bearer {self.settings.specialist_api_key}",
                        "Content-Type": "application/json"}) as response:
                    if response.status_code == 504:
                        raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.", retryable=True)
                    if response.status_code != 200:
                        raise TranslationError("TRANSLATION_UNAVAILABLE",
                                               "Translation service is unavailable.",
                                               retryable=response.status_code in {429, 502, 503})
                    data = bytearray()
                    async for piece in response.aiter_bytes():
                        data.extend(piece)
                        if len(data) > _MAX_RESPONSE:
                            raise TranslationError("TRANSLATION_UNAVAILABLE", "Invalid translation response.")
            payload = json.loads(data)
            if (not isinstance(payload, dict) or set(payload) != {"translated_text", "model"} or
                    not isinstance(payload["translated_text"], str) or
                    not payload["translated_text"].strip() or
                    not isinstance(payload["model"], str) or
                    payload["model"] != self.settings.specialist_model):
                raise TranslationError("TRANSLATION_UNAVAILABLE", "Invalid translation response.")
            return payload["translated_text"]
        except TranslationError:
            raise
        except httpx.TimeoutException as exc:
            raise TranslationError("PROCESSING_TIMEOUT", "Processing timed out.", retryable=True) from exc
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation service is unavailable.",
                                   retryable=isinstance(exc, httpx.TransportError)) from exc
