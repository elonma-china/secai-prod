"""English/Vietnamese v3 admission and specialist regression coverage."""
import json
import re

import httpx
import pytest

from secai_gateway.multilang.config import EXPECTED_MODEL, MultilangSettings
from secai_gateway.multilang.errors import TranslationError
from secai_gateway.multilang.provider import GenerationRequest, GenerationResult, SpecialistLlm
from secai_gateway.multilang.registry import capabilities, resolve_pair
from secai_gateway.multilang.service import TranslationRequest, translate

from .test_specialist_provider import configured


def test_english_capabilities_and_opt_in():
    settings = MultilangSettings(_env_file=None, enabled=True, enable_en=True)
    cap = capabilities(settings)
    assert len(cap['pairs']) == 18
    english = [p for p in cap['pairs'] if 'en' in (p['source_lang'], p['target_lang'])]
    assert len(english) == 2 and all(p['enabled'] for p in english)
    assert resolve_pair(' EN ', 'vi', settings) == ('en', 'vi')
    with pytest.raises(TranslationError, match='LANGUAGE_DISABLED'):
        resolve_pair('en', 'vi', MultilangSettings(_env_file=None, enabled=True))
    with pytest.raises(TranslationError, match='UNSUPPORTED_PAIR'):
        resolve_pair('en', 'ja', settings)


@pytest.mark.asyncio
@pytest.mark.parametrize('source,target', [('en', 'vi'), ('vi', 'en')])
async def test_specialist_accepts_english_both_directions(source, target):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'translated_text': 'Translated text.', 'model': configured().specialist_model})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await SpecialistLlm(client, configured()).generate(
            GenerationRequest('', 'Source text.', 100, source_lang=source, target_lang=target))
    assert seen == [{'text': 'Source text.', 'source_lang': source, 'target_lang': target}]


@pytest.mark.asyncio
@pytest.mark.parametrize('source,target', [('en', 'vi'), ('vi', 'en')])
async def test_english_core_preserves_identifiers(source, target):
    class Translator:
        async def generate(self, request):
            markers = re.findall(r'⟦ML-[^⟧]+⟧', request.user)
            prose = 'Kiểm tra nhật ký bảo mật tại' if target == 'vi' else 'Inspect security logs at'
            return GenerationResult(prose + ' ' + ' '.join(markers), 'stop', EXPECTED_MODEL)
    text = ('Check the security log at' if source == 'en' else 'Kiểm tra nhật ký bảo mật tại') + ' 192.0.2.15 for CVE-2024-1234.'
    result = await translate(TranslationRequest(text, source, target), llm=Translator(),
        settings=MultilangSettings(_env_file=None, enabled=True, enable_en=True,
            context_tokens=32768, chat_template_overhead_tokens=128), token_count=len)
    assert '192.0.2.15' in result.translated_text and 'CVE-2024-1234' in result.translated_text
