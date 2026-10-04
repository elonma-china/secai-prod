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
@pytest.mark.parametrize('first', ['Xin,', 'Xin;', 'Xin:', 'Xin)', 'Xin]',
                                          'Xin"', 'Xin，', 'Xin；', 'Xin…', 'Xin؟',
                                          'Xin）', 'Xin”', 'Xin۔', 'Xin、', 'Xin»'])
async def test_spaced_target_separates_after_punctuation(first):
    class Sentences(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return (first, 'chào', 'bạn')[len(self.calls) - 1]

    context = Sentences()
    result = await translate_blocks((block('中' * 800),), 'zh', 'vi', context=context)
    assert result[0].translated_text == first + ' chào bạn'


@pytest.mark.asyncio
async def test_spaced_target_separates_after_decomposed_latin_grapheme():
    class Sentences(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('Cafe\u0301', 'chào', 'bạn')[len(self.calls) - 1]

    result = await translate_blocks((block('中' * 800),), 'zh', 'vi', context=Sentences())
    assert result[0].translated_text == 'Cafe\u0301 chào bạn'


@pytest.mark.asyncio
@pytest.mark.parametrize('first,second,expected', [
    ('công-', 'nghệ', 'công-nghệ'),
    ('công‐', 'nghệ', 'công‐nghệ'),
    ('HTTP/', '2', 'HTTP/2'),
    ('HTTP⁄', '2', 'HTTP⁄2'),
    ('file_', 'name', 'file_name'),
    ('l’', 'amour', 'l’amour'),
    ('don’', 't', 'don’t'),
    ('don’', 't know', 'don’t know'),
    ("don'", 't know', "don't know"),
    ('they’', 're ready', 'they’re ready'),
    ('He said ‘Hello’', 'a friend arrived', 'He said ‘Hello’ a friend arrived'),
    ("He said 'Hello'", 'a friend arrived', "He said 'Hello' a friend arrived"),
    ("Hello'", 'a friend arrived', "Hello' a friend arrived"),
    ('He said ‘don’t’', 'a friend arrived', 'He said ‘don’t’ a friend arrived'),
    ('Xin’', 'chào', 'Xin’ chào'),
    ('Xin’', 'chào tiếp', 'Xin’ chào tiếp'),
    ('Xin—', 'chào', 'Xin— chào'),
    ('-', 'mục', '- mục'),
])
async def test_spaced_target_distinguishes_word_joiners_from_sentence_punctuation(
        first, second, expected):
    class Sentences(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return (first, second, 'bạn')[len(self.calls) - 1]

    result = await translate_blocks((block('中' * 800),), 'zh', 'vi', context=Sentences())
    assert result[0].translated_text == expected + ' bạn'


@pytest.mark.asyncio
@pytest.mark.parametrize('second', [', chào', '，chào', ')chào', '”chào'])
async def test_spaced_target_does_not_insert_before_closing_punctuation(second):
    class Sentences(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('Xin', second, 'bạn')[len(self.calls) - 1]

    result = await translate_blocks((block('中' * 800),), 'zh', 'vi', context=Sentences())
    assert result[0].translated_text == 'Xin' + second + ' bạn'


@pytest.mark.asyncio
@pytest.mark.parametrize('opening', ['"', '“', '(', '¿', '¡', '«', '「'])
async def test_spaced_target_does_not_insert_after_opening_punctuation(opening):
    class Sentences(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return (opening, 'xin', 'bạn')[len(self.calls) - 1]

    result = await translate_blocks((block('中' * 800),), 'zh', 'vi', context=Sentences())
    assert result[0].translated_text == opening + 'xin bạn'


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['zh', 'ja'])
@pytest.mark.parametrize('first', ['OpenAI', 'Café', 'OpenAI,', 'Cafe\u0301',
                                          'OpenAI…', '接口,'])
async def test_cjk_target_keeps_latin_word_space_at_chunk_seam(target, first):
    class Mixed(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return (first, 'API', '接口')[len(self.calls) - 1]

    context = Mixed()
    result = await translate_blocks((block('a ' * 400),), 'vi', target, context=context)
    assert len(context.calls) == 3
    assert result[0].translated_text == first + ' API 接口 '


@pytest.mark.asyncio
async def test_cjk_target_drops_space_after_cjk_comma_before_latin_word():
    class Mixed(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('接口，', 'API', '接口')[len(self.calls) - 1]

    result = await translate_blocks((block('a ' * 400),), 'vi', 'zh', context=Mixed())
    assert result[0].translated_text == '接口，API 接口 '


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['zh', 'ja'])
@pytest.mark.parametrize('second', ['，乙', '。乙', '；乙', '？乙', '、乙'])
async def test_cjk_target_attaches_leading_fullwidth_closing_punctuation(target, second):
    class Punctuation(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('甲', second, '接口')[len(self.calls) - 1]

    result = await translate_blocks((block('a ' * 400),), 'vi', target, context=Punctuation())
    assert result[0].translated_text == '甲' + second + '接口 '


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['OpenAI', 'Cafe\u0301', 'API2'])
@pytest.mark.parametrize('target,second,third,suffix', [
    ('zh', '，接口', '服务', '，接口服务 '),
    ('ja', '、API', '仕様', '、API 仕様 '),
])
async def test_cjk_target_attaches_leading_fullwidth_punctuation_after_latin(
        first, target, second, third, suffix):
    class Punctuation(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return (first, second, third)[len(self.calls) - 1]

    result = await translate_blocks((block('a ' * 400),), 'vi', target, context=Punctuation())
    assert result[0].translated_text == first + suffix


@pytest.mark.asyncio
async def test_cjk_target_keeps_tab_before_leading_fullwidth_punctuation_after_latin():
    class Punctuation(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('OpenAI', '，接口')[len(self.calls) - 1]

    result = await translate_blocks((block('g' * 384 + '\tb'),), 'vi', 'zh', context=Punctuation())
    assert result[0].translated_text == 'OpenAI\t，接口'


@pytest.mark.asyncio
async def test_cjk_target_keeps_space_before_leading_fullwidth_opening_punctuation():
    class Punctuation(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('甲', '「乙', '接口')[len(self.calls) - 1]

    result = await translate_blocks((block('a ' * 400),), 'vi', 'zh', context=Punctuation())
    assert result[0].translated_text == '甲 「乙接口 '


@pytest.mark.asyncio
async def test_cjk_target_keeps_structural_tab_at_chunk_seam():
    class Columns(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append(text)
            return ('甲', '乙')[len(self.calls) - 1]

    context = Columns()
    result = await translate_blocks((block('g' * 384 + '\tb'),), 'vi', 'zh', context=context)
    assert len(context.calls) == 2
    assert result[0].translated_text == '甲\t乙'


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


@pytest.mark.asyncio
async def test_decimal_comma_translation_does_not_invent_software_version():
    source = ('Hàng năm vào tháng Mười, gần 1,5 triệu động vật ăn cỏ tiến về phía '
              'đồng bằng phía Nam, băng qua Sông Mara, từ những ngon đồi phía Bắc '
              'để đón những cơn mưa.')
    output = ('Every year in October, nearly 1.5 million herbivores migrate toward '
              'the southern plains, crossing the Mara River from the northern hills '
              'to reach the rains.')

    class DecimalTranslation(Context):
        model_context_tokens = 4096

        async def translate(self, text, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            return output

    context = DecimalTranslation()
    result = await translate_blocks((block(source),), 'vi', 'en', context=context)
    assert result[0].translated_text == output
    assert len(context.calls) == 1
