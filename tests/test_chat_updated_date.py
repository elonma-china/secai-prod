import json
from datetime import date, datetime, timezone

import httpx
import pytest

import secai_gateway.api.routes as routes
from test_chat_followup_entity_prefetch import _FollowupFakeRunner, _client
from secai_gateway.retrieval import date_lookup


@pytest.mark.parametrize('prompt,expected', [
    ('CVE cập nhật ngày 09/10/2026', date(2026, 10, 9)),
    ('CVEs updated on 2026-10-09', date(2026, 10, 9)),
    ('CVE cập nhật hôm nay', date(2026, 10, 10)),
    ('CVE cập nhật hôm qua', date(2026, 10, 9)),
    ('CVE cập nhật ngày 9/10', date(2026, 10, 9)),
    ('CVE công bố ngày 09/10/2026', None),
    ('CVE cập nhật từ 09/10/2026 đến 10/10/2026', None),
    ('CVE-2026-107821 là gì?', None),
])
def test_updated_day_semantics(prompt, expected):
    assert date_lookup._updated_day(prompt, datetime(2026, 10, 9, 18, tzinfo=timezone.utc)) == expected


@pytest.mark.asyncio
async def test_date_filter_uses_local_day_and_deduplicates_across_pages(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body['filter']['must'][0]['range'] == {
            'gte': '2026-10-08T17:00:00Z', 'lt': '2026-10-09T17:00:00Z'}
        assert body['with_vector'] is False
        ids = ['CVE-2026-1', 'CVE-2026-1'] if 'offset' not in body else ['CVE-2026-2']
        return httpx.Response(200, json={'result': {
            'points': [{'payload': {'cve_id': cve, 'updated_at': '2026-10-09T08:00:00Z'}} for cve in ids],
            'next_page_offset': 'next' if 'offset' not in body else None}})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(date_lookup.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = await date_lookup._date_grounding('Liệt kê 2 CVE cập nhật ngày 09/10/2026')
    assert result['result'].count('**CVE:** CVE-2026-1') == 1
    assert '**CVE:** CVE-2026-2' in result['result']
    assert 'updated_at (UTC)' in result['result']
    assert '2026-10-09T15:00:00+07:00' in result['result']
    assert '2 CVEs' in result['summary']


@pytest.mark.asyncio
async def test_invalid_date_does_not_call_network():
    result = await date_lookup._date_grounding('CVE cập nhật ngày 31/02/2026')
    assert 'không hợp lệ' in result['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 503])
async def test_empty_results_are_distinct_from_service_failure(monkeypatch, status):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={
        'result': {'points': [], 'next_page_offset': None}}))
    monkeypatch.setattr(date_lookup.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    if status == 503:
        with pytest.raises(httpx.HTTPStatusError):
            await date_lookup._date_grounding('CVE cập nhật ngày 09/10/2026')
    else:
        result = await date_lookup._date_grounding('CVE cập nhật ngày 09/10/2026')
        assert 'Không tìm thấy CVE khớp ngày cập nhật' in result['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('session,stream', [(False, False), (False, True), (True, False), (True, True)])
async def test_date_query_gets_grounding_without_entity_history(monkeypatch, session, stream):
    prompt = 'Những CVE nào được cập nhật ngày 09/10/2026? Liệt kê 5 CVE.'
    async def lookup(query):
        assert query == prompt
        return {'query': query, 'result': 'CVE-2026-107459 updated_at=2026-10-09T08:13:42Z',
                'summary': 'qdrant_search 1 CVE [updated-date]', 'duration_ms': 12}
    monkeypatch.setattr(routes, '_date_grounding', lookup, raising=False)
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        url = '/ai/chat'
        if session:
            created = await client.post('/sessions', json={'user_id': 'date-test', 'title': 'date'})
            url = f"/sessions/{created.json()['session_id']}/chat"
        if stream:
            url += '/stream'
        response = await client.post(url, json={'user_id': 'date-test', 'message': prompt})
    assert response.status_code == 200
    assert runner.calls[-1][1] == prompt
    context = runner.calls[-1][3]
    assert context and 'CVE-2026-107459' in context
    if stream:
        frames = [json.loads(block.split('\n', 1)[1][6:]) for block in response.text.split('\n\n')
                  if block.startswith('event: tool_result\n')]
        assert any('[updated-date]' in f.get('summary', '') for f in frames)
