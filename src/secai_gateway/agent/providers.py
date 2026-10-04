"""Chat LLM backend selection, driven by Settings.llm_provider."""
from __future__ import annotations

from typing import Any

from linch.providers.openai_chat import OpenAIChatCompletionsProvider, OpenAIChatProviderOptions
from linch.providers.vllm import VLLMProvider, VLLMProviderOptions

from ..config import get_settings


def build_llm_provider(*, temperature_override: float | None = None) -> tuple[Any, str]:
    """Select the chat LLM backend from ``LLM_PROVIDER`` (default ``vllm``/Qwen).

    ``LLM_PROVIDER=deepseek`` routes to DeepSeek's OpenAI-compatible chat API
    (``deepseek-v4-flash``), the same backend family the legacy IAS gateway used.
    Thinking is disabled the way the Family-A DeepSeek agent does it; the vLLM
    ``enable_thinking`` chat-template kwarg is NOT sent (DeepSeek rejects it).
    """
    s = get_settings()
    if s.llm_provider.lower() == "deepseek":
        provider = OpenAIChatCompletionsProvider(OpenAIChatProviderOptions(
            api_key=s.deepseek_api_key,
            base_url=s.deepseek_base_url.rstrip("/"),
            context_window=s.deepseek_context_window,
            parallel_tool_calls=True,
            extra_body={"thinking": {"type": "disabled"}},
        ))
        return provider, s.deepseek_model
    provider = VLLMProvider(VLLMProviderOptions(
        api_key=s.qwen_api_key,
        base_url=s.qwen_base_url.rstrip("/"),
        context_window=s.qwen_context_window,
        parallel_tool_calls=True,
        # Cyber-F1 (Qwen3-30B-A3B-Instruct-2507 MoE) model-card recommended sampling, now env-tunable via
        # Settings (LLM_TEMPERATURE, LLM_TOP_P, …). Only `temperature` is a first-class
        # OpenAI field (linch); top_p/top_k/min_p/presence_penalty/repetition_penalty are
        # vLLM sampling extensions, so all six go through extra_body — the OpenAI SDK
        # merges these into the actual HTTP request body.
        extra_body={
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": (s.llm_temperature if temperature_override is None
                            else temperature_override), "top_p": s.llm_top_p, "top_k": s.llm_top_k,
            "min_p": s.llm_min_p, "presence_penalty": s.llm_presence_penalty,
            "repetition_penalty": s.llm_repetition_penalty,
        },
    ))
    return provider, s.qwen_model
