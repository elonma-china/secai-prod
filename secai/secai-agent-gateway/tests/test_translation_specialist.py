import asyncio
import json

import httpx
import pytest

from secai_gateway.translation.service import translate_blocks
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.specialist import TranslationAgent
from secai_gateway.translation.types import DocumentBlock, TranslationError


class Context:
    model_context_tokens = 8192
    def __init__(self, output=None):
        self.calls = []
        self.output = output
    async def checkpoint(self, *args, **kwargs):
        pass
    async def translate(self, text, source_lang, target_lang):
        self.calls.append((text, source_lang, target_lang))
        return self.output if self.output is not None else text


def block(text):
    return DocumentBlock(id='b1', kind='paragraph', order=0, text=text, metadata={})


@pytest.mark.parametrize('other', ['en','zh','ja','ko','fr','de','es','ru','ar'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.asyncio
async def test_all_18_directions_use_structured_specialist(other, reverse):
    source, target = (other, 'vi') if reverse else ('vi', other)
    context = Context()
    result = await translate_blocks((block('Hello CVE-2024-12345'),), source, target, context=context)
    assert result[0].translated_text == 'Hello CVE-2024-12345'
    assert context.calls[0][1:] == (source, target)
    assert 'CVE-2024-12345' not in context.calls[0][0]


@pytest.mark.asyncio
async def test_mutated_literal_fails_after_one_retry():
    context = Context('changed')
    with pytest.raises(TranslationError, match='literal') as exc:
        await translate_blocks((block('Hello CVE-2024-12345'),), 'en', 'vi', context=context)
    assert exc.value.code == 'TOKEN_INTEGRITY_FAILED'
    assert len(context.calls) == 2


@pytest.mark.asyncio
async def test_http_contract_and_caps(monkeypatch):
    captured = []
    def handler(request):
        captured.append((request.headers['authorization'], json.loads(request.content)))
        return httpx.Response(200, json={'translated_text':'Xin chào','model':'tencent/Hy-MT2-1.8B'})
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = TranslationSettings(specialist_base_url='http://127.0.0.1:18404',
                                   specialist_api_key='test-secret', _env_file=None)
    agent = TranslationAgent(settings)
    assert await agent.translate('Hello', 'en', 'vi') == 'Xin chào'
    assert captured == [('Bearer test-secret', {'text':'Hello','source_lang':'en','target_lang':'vi'})]


@pytest.mark.parametrize('status,payload,code', [
    (401, {}, 'TRANSLATION_UNAVAILABLE'), (429, {}, 'TRANSLATION_UNAVAILABLE'),
    (503, {}, 'TRANSLATION_UNAVAILABLE'), (504, {}, 'PROCESSING_TIMEOUT'),
    (200, {'translated_text':'','model':'tencent/Hy-MT2-1.8B'}, 'TRANSLATION_UNAVAILABLE'),
    (200, {'translated_text':'x','model':'wrong'}, 'TRANSLATION_UNAVAILABLE'),
    (200, {'translated_text':12,'model':'tencent/Hy-MT2-1.8B'}, 'TRANSLATION_UNAVAILABLE'),
])
@pytest.mark.asyncio
async def test_http_failure_is_safe(monkeypatch, status, payload, code):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    agent = TranslationAgent(TranslationSettings(specialist_base_url='http://127.0.0.1:18404',
                                                 specialist_api_key='test-secret', _env_file=None))
    with pytest.raises(TranslationError) as exc:
        await agent.translate('Hello', 'en', 'vi')
    assert exc.value.code == code
    assert 'test-secret' not in exc.value.message


@pytest.mark.asyncio
async def test_cancel_propagates(monkeypatch):
    async def handler(request):
        await asyncio.sleep(100)
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    agent = TranslationAgent(TranslationSettings(specialist_base_url='http://127.0.0.1:18404',
                                                 specialist_api_key='test-secret', _env_file=None))
    task = asyncio.create_task(agent.translate('Hello', 'en', 'vi'))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

@pytest.mark.asyncio
async def test_opt_in_linch_tool_uses_specialist(monkeypatch):
    from linch.tools.base import ToolContext
    from secai_gateway.agent.profiles import registry_for
    from secai_gateway.translation.tool import build_translation_tool
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'translated_text':seen[-1]['text'],
                                          'model':'tencent/Hy-MT2-1.8B'})
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = TranslationSettings(specialist_base_url='http://127.0.0.1:18404',
                                   specialist_api_key='test-secret', _env_file=None)
    tool = build_translation_tool(settings)
    result = await tool.execute({'text':'Hello CVE-2024-12345',
                                 'source_lang':'en','target_lang':'vi'},
                                ToolContext(cwd='.', session_id='test', run_id='test', session_store=None))
    assert not result.is_error
    assert result.content == 'Hello CVE-2024-12345'
    assert len(seen) == 1 and 'CVE-2024-12345' not in seen[0]['text']
    assert registry_for('translation_orchestrator') == frozenset({'translation_specialist'})
    assert 'translation_specialist' not in registry_for('chat')

@pytest.mark.asyncio
async def test_oversized_response_and_malformed_json_fail(monkeypatch):
    for body in (b'x' * (256 * 1024 + 1), b'{'):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        real_client = httpx.AsyncClient
        with monkeypatch.context() as patch:
            patch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
            agent = TranslationAgent(TranslationSettings(specialist_base_url='http://127.0.0.1:18404',
                                                         specialist_api_key='test-secret', _env_file=None))
            with pytest.raises(TranslationError) as exc:
                await agent.translate('Hello', 'en', 'vi')
            assert exc.value.code == 'TRANSLATION_UNAVAILABLE'


@pytest.mark.asyncio
async def test_transient_retry_is_bounded_at_block_layer():
    class Flaky(Context):
        async def translate(self, text, source_lang, target_lang):
            self.calls.append((text, source_lang, target_lang))
            raise TranslationError('TRANSLATION_UNAVAILABLE', 'unavailable', retryable=True)
    context = Flaky()
    with pytest.raises(TranslationError):
        await translate_blocks((block('Hello'),), 'en', 'vi', context=context)
    assert len(context.calls) == 2

@pytest.mark.asyncio
async def test_supported_ocr_correction_outage_is_not_disguised_as_review():
    from secai_gateway.translation.ocr_correction import correct_ocr
    from secai_gateway.translation.types import OcrResult
    class Failed:
        async def complete(self, profile, prompt):
            raise TranslationError('TRANSLATION_UNAVAILABLE', 'Unavailable')
    with pytest.raises(TranslationError):
        await correct_ocr(OcrResult(blocks=(block('Hello'),), raw_text='Hello'), context=Failed())

@pytest.mark.asyncio
async def test_file_job_captures_real_specialist_wire(tmp_path, monkeypatch):
    from secai_gateway.translation.jobs import JobStore
    from secai_gateway.translation.worker import Worker
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={'translated_text':body['text'],
                                          'model':'tencent/Hy-MT2-1.8B'})
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = TranslationSettings(db_path=str(tmp_path/'jobs.sqlite3'),
                                   artifact_dir=str(tmp_path/'artifacts'),
                                   specialist_base_url='http://127.0.0.1:18404',
                                   specialist_api_key='test-secret', _env_file=None)
    store = JobStore(settings)
    job = store.admit('file', b'Hello CVE-2024-12345', '.txt', 'en', 'vi', '127.0.0.1', None)
    await Worker(store, settings, None).run_once()
    result = store.get(job['job_id'])
    assert result['status'] == 'succeeded'
    assert 'CVE-2024-12345' in result['result_json']
    assert len(seen) == 1 and seen[0]['source_lang'] == 'en' and seen[0]['target_lang'] == 'vi'
    assert 'CVE-2024-12345' not in seen[0]['text']
