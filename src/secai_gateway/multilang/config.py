from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from secai_gateway.config import Settings as LegacySettings


EXPECTED_MODEL = "DuyTa/Cyber-F1-AWQ"
HYMT_MODEL = "tencent/Hy-MT2-7B"
HYMT_TOKENIZER_SHA256 = "4185389a69284fdd3ebf737c6120bbeee36cbed9763151fa5bac5986a39e05d4"
HYMT_TOKENIZER_CONFIG_SHA256 = "1fffdb809549222938f4a732645dd984c7c0b2973d592c2d01e05425274317da"
HYMT_TOKENIZER_REVISION = "9b0eb4e8f001def3e5ff6469a0ac96fdb39ec223"
HYMT_GGUF_TEMPLATE_SHA256 = "788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677"
# Exact GGUF metadata template from pinned Hy-MT2-7B Q8 artifact; its one-user
# specialization renders <|startoftext|>{user}<|extra_0|>, with no system turn.
HYMT_GGUF_TEMPLATE = (
    "{% set ns = namespace(has_head=true) %}"
    "{% set loop_messages = messages %}"
    "{% for message in loop_messages %}"
    "{% set content = message['content'] %}"
    "{% if loop.index0 == 0 %}"
    "{% if content == '' %}"
    "{% set ns.has_head = false %}"
    "{% elif message['role'] == 'system' %}"
    "{% set content = '<|startoftext|>' + content + '<|extra_4|>' %}"
    "{% endif %}{% endif %}"
    "{% if message['role'] == 'user' %}"
    "{% if loop.index0 == 1 and ns.has_head %}"
    "{% set content = content + '<|extra_0|>' %}"
    "{% else %}"
    "{% set content = '<|startoftext|>' + content + '<|extra_0|>' %}"
    "{% endif %}"
    "{% elif message['role'] == 'assistant' %}"
    "{% set content = content + '<|eos|>' %}"
    "{% endif %}{{ content }}{% endfor %}"
)
_HYMT_PROMPT = ("Translate the following text into {target}. Note that you should only "
                "output the translated result without any additional explanation:\n\n{source}")
_HYMT_TARGETS = {"vi": "Vietnamese", "en": "English", "zh-Hans": "Simplified Chinese",
                 "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German",
                 "es": "Spanish", "ru": "Russian", "ar": "Modern Standard Arabic"}


@dataclass(frozen=True)
class HyMtCounter:
    tokenizer: Any

    def __call__(self, text: str) -> int:
        try:
            # No serialized truncation, no added BOS/EOS. Special tokens in the
            # rendered prompt are already present and encode as single IDs.
            return len(self.tokenizer.encode(text, add_special_tokens=False).ids)
        except Exception:
            from .errors import TranslationError
            raise TranslationError("MODEL_UNAVAILABLE") from None

    def render_prompt(self, target: str, source: str) -> str:
        try:
            user = _HYMT_PROMPT.format(target=_HYMT_TARGETS[target], source=source)
        except KeyError:
            from .errors import TranslationError
            raise TranslationError("MODEL_UNAVAILABLE") from None
        return f"<|startoftext|>{user}<|extra_0|>"

    def prompt_tokens(self, target: str, source: str) -> int:
        return self(self.render_prompt(target, source))


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
    specialist_profile: Literal["madlad_t5", "hymt2_7b_gguf", "hymt2_7b_gguf_gpu"] = "madlad_t5"
    specialist_base_url: str = ""
    specialist_api_key: str = ""
    specialist_model: str = ""
    specialist_tokenizer_json: str = ""
    specialist_tokenizer_sha256: str = ""
    specialist_tokenizer_config_json: str = ""
    specialist_tokenizer_config_sha256: str = ""
    specialist_tokenizer_revision: str = ""
    specialist_chat_template_sha256: str = ""
    specialist_context_tokens: int = Field(0, ge=0, le=4096)
    specialist_chunk_input_tokens: int = Field(256, ge=1, le=1024)
    specialist_max_output_tokens: int = Field(256, ge=1, le=512)
    specialist_timeout_seconds: int = Field(60, ge=1, le=120)

    @model_validator(mode="after")
    def valid_caps(self) -> "MultilangSettings":
        if self.chunk_input_tokens > self.max_source_tokens:
            raise ValueError("chunk input budget exceeds source budget")
        if (self.backend == "specialist" and self.specialist_context_tokens
                and self.specialist_chunk_input_tokens > self.specialist_context_tokens):
            raise ValueError("specialist chunk budget exceeds context budget")
        if (self.specialist_profile == "madlad_t5" and
                (self.specialist_context_tokens > 512 or
                 self.specialist_chunk_input_tokens > 512 or
                 self.specialist_max_output_tokens > 256)):
            raise ValueError("MADLAD specialist budget exceeds encoder/decoder limits")
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
    """Load only the selected specialist's locally pinned tokenization recipe."""
    if settings.specialist_profile in {"hymt2_7b_gguf", "hymt2_7b_gguf_gpu"}:
        return _verified_hymt_counter(settings)
    if not settings.specialist_context_tokens:
        return None
    return _pinned_counter(settings.specialist_tokenizer_json, settings.specialist_tokenizer_sha256)


def _verified_hymt_counter(settings: MultilangSettings) -> HyMtCounter | None:
    required_context = 2048 if settings.specialist_profile == "hymt2_7b_gguf_gpu" else 4096
    if (settings.specialist_model != HYMT_MODEL or
            settings.specialist_context_tokens != required_context or
            settings.specialist_max_output_tokens != 512 or
            settings.specialist_chunk_input_tokens > 1024 or
            settings.specialist_tokenizer_sha256.lower() != HYMT_TOKENIZER_SHA256 or
            settings.specialist_tokenizer_config_sha256.lower() != HYMT_TOKENIZER_CONFIG_SHA256 or
            settings.specialist_tokenizer_revision != HYMT_TOKENIZER_REVISION or
            settings.specialist_chat_template_sha256.lower() != HYMT_GGUF_TEMPLATE_SHA256 or
            sha256(HYMT_GGUF_TEMPLATE.encode()).hexdigest() != HYMT_GGUF_TEMPLATE_SHA256):
        return None
    try:
        tokenizer_path = Path(settings.specialist_tokenizer_json)
        config_path = Path(settings.specialist_tokenizer_config_json)
        tokenizer_raw = tokenizer_path.read_bytes()
        config_raw = config_path.read_bytes()
        if (sha256(tokenizer_raw).hexdigest() != HYMT_TOKENIZER_SHA256 or
                sha256(config_raw).hexdigest() != HYMT_TOKENIZER_CONFIG_SHA256):
            return None
        metadata = json.loads(config_raw)
        if (not isinstance(metadata, dict) or metadata.get("chat_template") is not None or
                metadata.get("bos_token") != "<|startoftext|>" or
                metadata.get("eos_token") != "<|eos|>"):
            return None
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_str(tokenizer_raw.decode("utf-8"))
        if tokenizer.truncation is not None or tokenizer.padding is not None:
            return None
        counter = HyMtCounter(tokenizer)
        probe = counter.render_prompt("zh-Hans", "Xin chào")
        ids = tokenizer.encode(probe, add_special_tokens=False).ids
        if not ids or ids[0] != 127958 or ids[-1] != 127962:
            return None
        return counter
    except Exception:
        return None


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
