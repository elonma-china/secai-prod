from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Callable

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

    @model_validator(mode="after")
    def valid_caps(self) -> "MultilangSettings":
        if self.chunk_input_tokens > self.max_source_tokens:
            raise ValueError("chunk input budget exceeds source budget")
        return self

    def flag(self, code: str) -> bool:
        return bool(getattr(self, f"enable_{'zh' if code == 'zh-Hans' else code}", False))


def verified_counter(settings: MultilangSettings) -> Callable[[str], int] | None:
    """Load only a locally pinned tokenizer. Missing evidence closes the inference gate."""
    if not (settings.tokenizer_json and settings.tokenizer_sha256 and settings.context_tokens
            and settings.chat_template_overhead_tokens):
        return None
    path = Path(settings.tokenizer_json)
    try:
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != settings.tokenizer_sha256.lower():
            return None
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(str(path))
    except (OSError, ValueError, ImportError):
        return None
    return lambda text: len(tokenizer.encode(text).ids)


def incumbent_ok(legacy: LegacySettings) -> bool:
    return (legacy.llm_provider.lower() == "vllm"
            and legacy.qwen_model == EXPECTED_MODEL
            and legacy.qwen_base_url.startswith(("http://", "https://")))
