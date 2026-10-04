import pytest

from secai_gateway.translation.service import translate_blocks
from secai_gateway.translation.types import DocumentBlock, TranslationError


def _source(prompt: str) -> str:
    wrapped = prompt.rsplit("<source>\n", 1)[1].split("\n</source>", 1)[0]
    return wrapped.split("\n", 1)[1].rsplit("\n</untrusted_retrieved_content>", 1)[0]


@pytest.mark.parametrize("literal,mutated", [
    ("2001:db8::1", "2001:db8::2"),
    ("2001:db8::/64", "2001:db8::/65"),
    ("https://example.com/search?q=a(b)c", "https://example.com/search?q=a(b)d"),
    (r"C:\Program Files\Vendor\app.exe", r"C:\Program Files\Other\app.exe"),
])
@pytest.mark.asyncio
async def test_provider_cannot_mutate_hidden_literal_suffix(literal, mutated):
    class Provider:
        def __init__(self):
            self.prompts = []

        async def checkpoint(self, *_args, **_kwargs):
            pass

        async def complete(self, _profile, prompt):
            self.prompts.append(prompt)
            return _source(prompt).replace(literal, mutated), {}

    source = f"Use {literal} now"
    provider = Provider()
    block = DocumentBlock(id="b000001", kind="paragraph", order=0, text=source, metadata={})
    result = await translate_blocks((block,), "en", "vi", context=provider)
    assert result[0].translated_text == source
    assert provider.prompts and all(literal not in prompt for prompt in provider.prompts)


@pytest.mark.parametrize("literal,mutated", [
    ("2001:db8::1", "2001:db8::2"),
    ("https://example.com/search?q=a(b)c", "https://example.com/search?q=a(b)d"),
    (r"C:\Program Files\Vendor\app.exe", r"C:\Program Files\Other\app.exe"),
])
@pytest.mark.asyncio
async def test_provider_invented_mutation_is_rejected(literal, mutated):
    class Provider:
        async def checkpoint(self, *_args, **_kwargs):
            pass

        async def complete(self, _profile, prompt):
            return _source(prompt) + " " + mutated, {}

    block = DocumentBlock(id="b000001", kind="paragraph", order=0,
                          text=f"Use {literal} now", metadata={})
    with pytest.raises(TranslationError) as exc:
        await translate_blocks((block,), "en", "vi", context=Provider())
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"
