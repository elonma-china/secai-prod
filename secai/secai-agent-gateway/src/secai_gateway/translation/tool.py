"""Opt-in DuyTa delegation tool; the specialist alone performs translation."""
from __future__ import annotations

from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from .service import translate_blocks
from .settings import TranslationSettings
from .specialist import TranslationAgent
from .types import DocumentBlock, TranslationError


def build_translation_tool(settings: TranslationSettings):
    agent = TranslationAgent(settings)

    class Context:
        model_context_tokens = settings.specialist_context_tokens

        async def checkpoint(self, stage: str, completed: int = 0, total: int | None = None):
            return None

        async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
            return await agent.translate(text, source_lang, target_lang)

    @tool(name="translation_specialist", scope="read",
          description="Translate text using the dedicated specialist. Supply source and target language codes explicitly.")
    async def translation_specialist(text: str, source_lang: str, target_lang: str,
                                     ctx: ToolContext) -> ToolResult:
        try:
            block = DocumentBlock(id="delegated", kind="paragraph", order=0,
                                  text=text, metadata={})
            items = await translate_blocks((block,), source_lang, target_lang, context=Context())
            return ToolResult(content=items[0].translated_text, summary="translation_specialist complete")
        except TranslationError as exc:
            return ToolResult(content=f"Translation failed: {exc.code}",
                              summary="translation_specialist failed", is_error=True)

    return translation_specialist
