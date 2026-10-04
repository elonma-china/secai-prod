from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Callable, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from secai_gateway.config import Settings as LegacySettings


EXPECTED_MODEL = "DuyTa/Cyber-F1-AWQ"


class MultilangSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MULTILANG_", env_file=".env", extra="ignore")

    enabled: bool = False
    enable_zh: bool = False
    enable_ja: bool = False
    enable_ko: bool = False
    enable_fr: bool = False
    enable_de: bool = False
    enable_es: bool = False
    enable_ru: bool = False
    enable_ar: bool = False
    max_text_chars: int = Field(24000, ge=1, le=24000)
    max_body_bytes: int = Field(262144, ge=1024, le=1048576)
    max_source_tokens: int = Field(8192, ge=1, le=8192)
    max_chunks: int = Field(16, ge=1, le=16)
    chunk_input_tokens: int = Field(2048, ge=1, le=2048)
    max_output_tokens: int = Field(4096, ge=1, le=4096)
    max_retries: int = Field(1, ge=0, le=1)
    request_timeout_seconds: int = Field(120, ge=1, le=120)
    max_concurrent_model_calls: int = Field(2, ge=1, le=2)
    max_active_requests: int = Field(8, ge=1, le=8)
    rate_limit_per_minute: int = Field(10, ge=1, le=10)
    # Both are deployment evidence, not model discovery. Never fetch at startup.
    tokenizer_json: str = ""
    tokenizer_sha256: str = ""
    context_tokens: int = Field(0, ge=0, le=131072)
    # Attested upper bound for serving chat-template role/special-token overhead.
    chat_template_overhead_tokens: int = Field(0, ge=0, le=4096)
    backend: Literal["incumbent", "specialist"] = "incumbent"
    specialist_base_url: str = ""
    specialist_api_key: str = ""
    specialist_model: str = ""
    specialist_tokenizer_json: str = ""
    specialist_tokenizer_sha256: str = ""
    specialist_context_tokens: int = Field(0, ge=0, le=512)
    specialist_chunk_input_tokens: int = Field(256, ge=1, le=512)
    specialist_max_output_tokens: int = Field(256, ge=1, le=256)
    specialist_timeout_seconds: int = Field(60, ge=1, le=120)

    @model_validator(mode="after")
    def valid_caps(self) -> "MultilangSettings":
        if self.chunk_input_tokens > self.max_source_tokens:
            raise ValueError("chunk input budget exceeds source budget")
        if (self.backend == "specialist" and self.specialist_context_tokens
                and self.specialist_chunk_input_tokens > self.specialist_context_tokens):
            raise ValueError("specialist chunk budget exceeds context budget")
        return self

    def flag(self, code: str) -> bool:
        return bool(getattr(self, f"enable_{'zh' if code == 'zh-Hans' else code}", False))


def verified_counter(settings: MultilangSettings) -> Callable[[str], int] | None:
    """Load only a locally pinned tokenizer. Missing evidence closes the inference gate."""
    if not (settings.tokenizer_json and settings.tokenizer_sha256 and settings.context_tokens
            and settings.chat_template_overhead_tokens):
        return None
    return _pinned_counter(settings.tokenizer_json, settings.tokenizer_sha256)


def verified_specialist_counter(settings: MultilangSettings) -> Callable[[str], int] | None:
    """Count with the exact pinned specialist tokenizer, including its EOS postprocessor."""
    if not settings.specialist_context_tokens:
        return None
    return _pinned_counter(settings.specialist_tokenizer_json, settings.specialist_tokenizer_sha256)


def _pinned_counter(path_name: str, digest: str) -> Callable[[str], int] | None:
    if not path_name or not digest:
        return None
    path = Path(path_name)
    try:
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != digest.lower():
            return None
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(str(path))
        # A serialized max_length silently undercounts every budget; do not trust it.
        if tokenizer.truncation is not None:
            return None
    except Exception:
        # The Rust tokenizer parser raises plain Exception for malformed JSON.
        return None

    def count(text: str) -> int:
        try:
            return len(tokenizer.encode(text).ids)
        except Exception:
            from .errors import TranslationError
            raise TranslationError("MODEL_UNAVAILABLE") from None

    return count


def incumbent_ok(legacy: LegacySettings) -> bool:
    return (legacy.llm_provider.lower() == "vllm"
            and legacy.qwen_model == EXPECTED_MODEL
            and legacy.qwen_base_url.startswith(("http://", "https://")))
