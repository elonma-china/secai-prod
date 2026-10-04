import pytest

from secai_gateway.translation.service import _chunks, translate_blocks
from secai_gateway.translation.types import DocumentBlock, TranslationError


def block(text):
    return DocumentBlock(id='b000001', kind='paragraph', order=0, text=text, metadata={})


class Context:
    model_context_tokens = 8192
    def __init__(self):
        self.calls = []
    async def checkpoint(self, stage, completed=0, total=None):
        pass
    async def translate(self, text, source_lang, target_lang):
        self.calls.append((text, source_lang, target_lang))
        return text.replace('Hello', 'Xin chào')


@pytest.mark.asyncio
async def test_translate_preserves_literal_and_order():
    context = Context()
    result = await translate_blocks((block('Hello CVE-2024-12345'),), 'en', 'vi', context=context)
    assert result[0].translated_text == 'Xin chào CVE-2024-12345'
    assert context.calls[0][1:] == ('en', 'vi')
    assert 'CVE-2024-12345' not in context.calls[0][0]


@pytest.mark.asyncio
async def test_same_language_skips_model():
    context = Context()
    result = await translate_blocks((block('Hello'),), 'en', 'en', context=context)
    assert result[0].translated_text == 'Hello'
    assert context.calls == []


@pytest.mark.parametrize('source', ['中https://example.com/a中', 'نصhttps://example.com/aنص',
                                    'текстhttps://example.com/aслово'])
@pytest.mark.asyncio
async def test_script_adjacent_url_never_reaches_provider(source):
    context = Context()
    result = await translate_blocks((block(source),), 'zh', 'vi', context=context)
    assert result[0].translated_text == source
    assert all('https://example.com/a' not in call[0] for call in context.calls)


def test_chunk_boundaries_keep_cjk_and_combining_graphemes():
    source = '漢字' * 2000 + 'e\u0301' * 2000
    chunks = _chunks(source)
    assert ''.join(chunks) == source
    assert all(len(chunk) <= 3000 for chunk in chunks)
    assert all(not chunk.startswith('\u0301') and not chunk.endswith('e') for chunk in chunks[:-1])


@pytest.mark.asyncio
async def test_configured_small_provider_context_reduces_chunk_size():
    class Small(Context):
        model_context_tokens = 2048
    context = Small()
    await translate_blocks((block('Word ' * 100),), 'en', 'vi', context=context)
    assert len(context.calls) > 1
    assert all(len(call[0]) <= 128 for call in context.calls)


class TrimmingContext(Context):
    model_context_tokens = 4096

    async def translate(self, text, source_lang, target_lang):
        self.calls.append((text, source_lang, target_lang))
        return text.strip()


@pytest.mark.asyncio
async def test_trimmed_chunk_outputs_keep_internal_word_separators():
    source = 'Xin chào thế giới. ' * 100
    context = TrimmingContext()
    result = await translate_blocks((block(source),), 'vi', 'en', context=context)
    assert len(context.calls) > 1
    assert result[0].translated_text == source


@pytest.mark.asyncio
async def test_trimmed_chunk_outputs_keep_edge_and_internal_newlines():
    source = '\n  ' + ('First line\nSecond line\n' * 40) + '  \n'
    context = TrimmingContext()
    result = await translate_blocks((block(source),), 'en', 'vi', context=context)
    assert len(context.calls) > 1
    assert result[0].translated_text == source


@pytest.mark.asyncio
async def test_trimmed_chunk_outputs_keep_protected_literal_boundaries():
    source = ('Hello CVE-2024-12345 at https://example.com/a \n' * 30)
    context = TrimmingContext()
    result = await translate_blocks((block(source),), 'en', 'vi', context=context)
    assert len(context.calls) > 1
    assert result[0].translated_text == source
    assert all('CVE-2024-12345' not in call[0] and 'https://example.com/a' not in call[0]
               for call in context.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('source_lang,source,target_lang,word', [
    ('zh', '中' * 800, 'vi', 'từ'),
    ('ja', '日' * 800, 'vi', 'từ'),
    ('vi', 'chào ' * 199 + 'chào', 'zh', '你好'),
    ('vi', 'chào ' * 199 + 'chào', 'ja', 'こんにちは'),
], ids=['zh-to-vi', 'ja-to-vi', 'vi-to-zh', 'vi-to-ja'])
async def test_chunk_join_uses_target_word_spacing(source_lang, source, target_lang, word):
    class Static(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            return word

    context = Static()
    result = await translate_blocks((block(source),), source_lang, target_lang, context=context)
    assert len(context.calls) > 1
    seam = '' if target_lang in {'zh', 'ja'} else ' '
    assert result[0].translated_text == seam.join([word] * len(context.calls))


@pytest.mark.asyncio
async def test_cjk_target_preserves_explicit_newline_at_chunk_seam():
    class OneCharacter(Context):
        model_context_tokens = 1032

        async def translate(self, text, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            return '甲'

    context = OneCharacter()
    result = await translate_blocks((block('a\nb'),), 'vi', 'zh', context=context)
    assert result[0].translated_text == '甲\n甲'


@pytest.mark.asyncio
async def test_spaced_target_separates_sentence_outputs_without_source_spaces():
    class Sentences(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            return 'Kết thúc.' if len(self.calls) == 1 else 'Tiếp theo'

    context = Sentences()
    result = await translate_blocks((block('中' * 800),), 'zh', 'vi', context=context)
    assert len(context.calls) == 3
    assert result[0].translated_text == 'Kết thúc. Tiếp theo Tiếp theo'


@pytest.mark.asyncio
async def test_spaced_target_keeps_literal_bytes_at_small_chunk_seams():
    class Short(Context):
        model_context_tokens = 1032

        async def translate(self, text, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            return text if text.startswith('⟦') else 'từ'

    context = Short()
    result = await translate_blocks((block('中https://example.com/a中'),), 'zh', 'vi', context=context)
    assert result[0].translated_text == 'từ https://example.com/a từ'


@pytest.mark.asyncio
async def test_new_technical_literal_rejected():
    class Invent(Context):
        async def translate(self, text, source_lang, target_lang):
            return text + ' CVE-2025-12345'
    with pytest.raises(TranslationError) as exc:
        await translate_blocks((block('Hello'),), 'en', 'vi', context=Invent())
    assert exc.value.code == 'TOKEN_INTEGRITY_FAILED'
