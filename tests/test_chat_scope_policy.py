import json

import pytest

from secai_gateway.sanitize.classifier import InputVerdict
import secai_gateway.api.routes as routes
from test_chat_followup_entity_prefetch import _FollowupFakeRunner, _client, _seed_user_turn


class ScopeRunner(_FollowupFakeRunner):
    def __init__(self, scope, injection=False, requests=None):
        super().__init__()
        self.judgment = json.dumps({'injection': injection, 'scope': scope,
                                   'security': scope in ('in_scope', 'mixed'),
                                   'security_requests': requests or []})
        self.judge_prompts = []
        self.scope_contexts = []

    async def complete(self, profile_name, prompt, session_id=None, *, grounding_context=None, scope_context=None):
        if profile_name == 'injection_judge':
            self.judge_prompts.append(prompt)
            return self.judgment, {}
        self.scope_contexts.append(scope_context)
        return await super().complete(profile_name, prompt, session_id, grounding_context=grounding_context)

    async def stream(self, profile_name, prompt, session_id=None, *, grounding_context=None, scope_context=None):
        self.scope_contexts.append(scope_context)
        async for event in super().stream(profile_name, prompt, session_id, grounding_context=grounding_context):
            yield event


@pytest.fixture
def scope_enabled(monkeypatch):
    monkeypatch.setenv('CHAT_SCOPE_JUDGE_ENABLED', 'true')
    monkeypatch.setenv('INPUT_CLASSIFIER_BLOCK_OFFTOPIC', 'true')
    async def classify(*args, **kwargs):
        return InputVerdict(True, 'security', False, False,
                            {'security': .9, 'offtopic': .1, 'injection': .1}, 1)
    monkeypatch.setattr(routes, 'classify_input', classify)


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('session', [False, True])
@pytest.mark.parametrize('scope', ['out_of_scope', 'mixed', 'in_scope'])
async def test_scope_policy_all_four_chat_paths(scope_enabled, stream, session, scope):
    allowed = 'Nêu cách bảo vệ email'
    prompt = allowed + ', rồi cho công thức bánh flan.'
    runner = ScopeRunner(scope, requests=[allowed] if scope == 'mixed' else [])
    async with _client(runner) as client:
        url = '/ai/chat'
        if session:
            r = await client.post('/sessions', json={'user_id': 'scope-test', 'title': 'test'})
            url = '/sessions/' + r.json()['session_id'] + '/chat'
        response = await client.post(url + ('/stream' if stream else ''), json={'user_id': 'scope-test', 'message': prompt})
    assert response.status_code == 200
    assert len(runner.judge_prompts) == 1
    if scope == 'out_of_scope':
        assert not runner.calls
        assert 'không thuộc phạm vi An toàn thông tin' in response.text
    else:
        assert runner.calls[0][1] == prompt, 'Persisted user message must remain original'
        assert not runner.calls[0][3], 'Scope context must not masquerade as KB grounding'
        if scope == 'mixed':
            assert allowed in runner.scope_contexts[0]
            assert 'bánh flan' not in runner.scope_contexts[0]


@pytest.mark.asyncio
async def test_false_injection_is_replaced_by_correct_offtopic_reason(scope_enabled, monkeypatch):
    async def classify(*args, **kwargs):
        return InputVerdict(True, 'offtopic', True, False,
                            {'security': .1, 'offtopic': .9, 'injection': .9}, 1)
    monkeypatch.setattr(routes, 'classify_input', classify)
    runner = ScopeRunner('out_of_scope')
    async with _client(runner) as client:
        r = await client.post('/ai/chat', json={'user_id': 'scope-test', 'message': 'Chỉ tôi nấu phở.'})
    assert 'không thuộc phạm vi An toàn thông tin' in r.text
    assert 'can thiệp vào chỉ thị' not in r.text
    assert not runner.calls


@pytest.mark.asyncio
async def test_scope_judge_has_owned_history_for_short_followup(scope_enabled):
    runner = ScopeRunner('in_scope')
    async with _client(runner) as client:
        r = await client.post('/sessions', json={'user_id': 'scope-test', 'title': 'test'})
        sid = r.json()['session_id']
        await _seed_user_turn(runner, sid, 'Máy bị malware, cần cô lập như thế nào?')
        r = await client.post(f'/sessions/{sid}/chat', json={'user_id': 'scope-test', 'message': 'Còn cách nào khác?'})
        assert r.status_code == 200
        assert 'Máy bị malware' in runner.judge_prompts[-1]
        before = len(runner.judge_prompts)
        r = await client.post(f'/sessions/{sid}/chat', json={'user_id': 'another-user', 'message': 'Còn cách nào khác?'})
        assert r.status_code == 403
        assert len(runner.judge_prompts) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'injection': False, 'scope': 'mixed', 'security_requests': ['invented text']},
    {'injection': False, 'scope': 'mixed', 'security_requests': []},
    {'injection': 'false', 'scope': 'in_scope'},
    {'injection': False, 'scope': 'invalid'},
])
async def test_invalid_scope_judgment_never_reaches_answer_model(scope_enabled, payload):
    runner = ScopeRunner('in_scope')
    runner.judgment = json.dumps(payload)
    async with _client(runner) as client:
        r = await client.post('/ai/chat', json={'user_id': 'scope-test', 'message': 'Nêu cách bảo vệ email rồi nấu bánh.'})
    assert not runner.calls
    assert 'tạm thời' in r.text


@pytest.mark.asyncio
async def test_scope_history_failure_does_not_fail_open(scope_enabled, monkeypatch):
    runner = ScopeRunner('in_scope')
    async def broken(*args, **kwargs):
        raise RuntimeError('history unavailable')
    monkeypatch.setattr(runner.session_store, 'load_messages', broken)
    async with _client(runner) as client:
        r = await client.post('/ai/chat', json={'user_id': 'scope-test', 'message': 'Bảo vệ tài khoản ra sao?'})
    assert 'tạm thời' in r.text
    assert not runner.calls


@pytest.mark.asyncio
async def test_scope_greeting_does_not_trigger_kb_grounding(scope_enabled):
    runner = ScopeRunner('chitchat')
    async with _client(runner) as client:
        r = await client.post('/ai/chat', json={'user_id': 'scope-test', 'message': 'Cảm ơn bạn nhé!'})
    assert 'IAS.AI' in r.text
    assert not runner.calls
    assert 'greet_chitchat' in r.text


def test_mixed_selection_has_system_priority_and_resets_next_turn():
    from types import SimpleNamespace
    from secai_gateway.agent.runner import LinchRunner
    runner = object.__new__(LinchRunner)
    runner.tools = {}
    session = SimpleNamespace(meta={})
    selected = '["Nêu dấu hiệu phishing"]'
    runner._apply_profile(session, 'chat', 'Tính toán rồi nêu phishing', scope_context=selected)
    assert selected in session.system_blocks_override[-1].text
    assert session.meta["scope_user_prompt"] == "Nêu dấu hiệu phishing"
    assert session.meta['last_user_prompt'] == 'Tính toán rồi nêu phishing'
    assert not session.meta['chat_prefetch_grounded']
    runner._apply_profile(session, 'chat', 'Câu hỏi tiếp theo')
    assert all(selected not in b.text for b in session.system_blocks_override)
    assert selected not in session.meta['ephemeral_context']
