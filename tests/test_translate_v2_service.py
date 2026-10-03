import pytest

from secai_gateway.translation.types import DocumentBlock, TranslationError


class Context:
    def __init__(self):
        self.prompts = []

    async def checkpoint(self, stage, completed=0, total=None):
        pass

    async def complete(self, profile, prompt):
        self.prompts.append((profile, prompt))
        return prompt.rsplit("<source>\n", 1)[1].split("\n</source>", 1)[0].replace("Hello", "Xin chào"), {}


@pytest.mark.asyncio
async def test_translate_preserves_literal_and_order():
    from secai_gateway.translation.service import translate_blocks

    blocks = (DocumentBlock(id="b000001", kind="paragraph", order=0,
                            text="Hello CVE-2024-12345", metadata={}),)
    context = Context()
    translated = await translate_blocks(blocks, "en", "vi", context=context)
    assert translated[0].translated_text == "Xin chào CVE-2024-12345"
    assert len(context.prompts) == 1
    assert context.prompts[0][0] == "translate"


@pytest.mark.asyncio
async def test_translation_rejects_dropped_literal():
    from secai_gateway.translation.service import translate_blocks

    class Bad(Context):
        async def complete(self, profile, prompt):
            return "translated without token", {}

    blocks = (DocumentBlock(id="b000001", kind="paragraph", order=0,
                            text="Hello CVE-2024-12345", metadata={}),)
    with pytest.raises(TranslationError) as e:
        await translate_blocks(blocks, "en", "vi", context=Bad())
    assert e.value.code == "TOKEN_INTEGRITY_FAILED"


@pytest.mark.asyncio
async def test_same_language_skips_model():
    from secai_gateway.translation.service import translate_blocks

    blocks = (DocumentBlock(id="b000001", kind="paragraph", order=0,
                            text="Hello", metadata={}),)
    context = Context()
    translated = await translate_blocks(blocks, "en", "en", context=context)
    assert translated[0].translated_text == "Hello"
    assert context.prompts == []


@pytest.mark.asyncio
async def test_translation_rejects_new_technical_literal():
    from secai_gateway.translation.service import translate_blocks

    class Invent(Context):
        async def complete(self, profile, prompt):
            return "Xin chào CVE-2025-12345", {}

    blocks = (DocumentBlock(id="b000001", kind="paragraph", order=0,
                            text="Hello", metadata={}),)
    with pytest.raises(TranslationError) as e:
        await translate_blocks(blocks, "en", "vi", context=Invent())
    assert e.value.code == "TOKEN_INTEGRITY_FAILED"
