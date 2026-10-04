import json
from types import SimpleNamespace
from test_review_auth import client, ADMIN, REVIEW

class FakeRunner:
    def __init__(self, answer='Alternative answer'):
        self.answer=answer; self.calls=[]; self.deleted=[]
        async def delete(sid): self.deleted.append(sid)
        self.session_store=SimpleNamespace(delete=delete)
    async def complete(self, profile, prompt, session_id=None):
        self.calls.append((profile,prompt,session_id))
        return self.answer, {'model':'fake'}

def configure(client, rate=1,answer='Alternative answer'):
    state=client.app.state.gateway
    state.settings.review_live_sample_rate=rate
    state.settings.review_live_cooldown_seconds=600
    state.settings.review_live_timeout_seconds=1
    state.runner=FakeRunner(answer)
    return state

PAYLOAD={'prompt_messages':[{'role':'user','content':'Question'}],'answer':'Original answer','source_group':'original-session'}

def test_zero_sampling_never_calls_model(client):
    state=configure(client,0)
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert state.runner.calls==[]

def test_live_pair_isolated_blinded_cooldown_export_path(client):
    state=configure(client)
    response=client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD)
    assert response.status_code==200
    result=response.json(); assert result['offered']
    a=result['assignment']; assert a['status']=='pending'
    assert {a['left']['text'],a['right']['text']}=={'Original answer','Alternative answer'}
    assert 'provenance' not in json.dumps(a)
    profile,prompt,sid=state.runner.calls[0]
    assert profile=='chat' and sid!='original-session' and sid.startswith('review-live-')
    assert 'Original answer' not in prompt
    assert state.runner.deleted==[sid]
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert len(state.runner.calls)==1
    assert client.put(f'/review-api/assignments/{a["id"]}/judgment',headers=REVIEW,json={'decision':'left'}).status_code==200
    item=client.get(f'/review-api/campaigns/{a["campaign_id"]}/items',headers=ADMIN).json()['items'][0]
    assert item['source_group']=='original-session'
    winner=item['candidates'][0]['id']
    assert client.put(f'/review-api/items/{item["id"]}/adjudication',headers=ADMIN,json={'outcome':'winner','winning_candidate_id':winner,'reason':'Reviewed'}).status_code==200
    assert client.get(f'/review-api/campaigns/{a["campaign_id"]}/export?format=preference',headers=ADMIN).status_code==200

def test_live_identical_and_invalid_context_no_pair(client):
    state=configure(client,answer='Original answer')
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert state.review_store.list_campaigns()==[]
    assert client.post('/review-api/live-pairs',headers=REVIEW,json={**PAYLOAD,'prompt_messages':[{'role':'system','content':'secret'}]}).status_code==422

def test_live_timeout_failure_and_empty_cleaned(client):
    import asyncio
    state=configure(client)
    state.settings.review_live_cooldown_seconds=0
    state.settings.review_live_timeout_seconds=0.01
    async def fail(*args): raise RuntimeError('model unavailable')
    async def hang(*args): await asyncio.Event().wait()
    for function in (fail,hang):
        state.runner.complete=function
        assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
        assert not state.review_live_inflight
    state.runner=FakeRunner(' ')
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert len(state.runner.deleted)==1
    assert state.review_store.list_campaigns()==[]

def test_live_actor_inflight_rejects_second_request(client):
    import asyncio
    import httpx
    state=configure(client)
    state.settings.review_live_cooldown_seconds=0
    async def exercise():
        started=asyncio.Event(); release=asyncio.Event(); calls=[]
        async def slow(profile,prompt,session_id):
            calls.append(session_id); started.set(); await release.wait()
            return 'Independent answer',{}
        state.runner.complete=slow
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),base_url='http://test') as http:
            first=asyncio.create_task(http.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD))
            await started.wait()
            second=await http.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD)
            assert second.json()=={'offered':False}
            release.set()
            assert (await first).json()['offered']
            assert len(calls)==1
    asyncio.run(exercise())

def test_live_redaction_limits_and_no_user_session_parameter(client):
    state=configure(client)
    payload={**PAYLOAD,'prompt_messages':[{'role':'user','content':'Public question with identifier 4111111111111111'}]}
    response=client.post('/review-api/live-pairs',headers=REVIEW,json=payload)
    assert response.json()['offered']
    assert '4111111111111111' not in state.runner.calls[0][1]
    assert '[REDACTED_IDENTIFIER]' in state.runner.calls[0][1]
    assert client.post('/review-api/live-pairs',headers=REVIEW,json={**PAYLOAD,'session_id':'original-session'}).status_code==422
    assert client.post('/review-api/live-pairs',headers=REVIEW,content=b' '*(262144+1)).status_code==413

def test_live_releases_cached_session_before_deleting_history(client):
    from types import SimpleNamespace
    state=configure(client)
    operations=[]
    async def release(sid,force=False): operations.append(('release',sid,force))
    async def delete(sid): operations.append(('delete',sid))
    state.runner.agent=SimpleNamespace(release_session=release)
    state.runner.session_store=SimpleNamespace(delete=delete)
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()['offered']
    assert operations[0][0]=='release' and operations[0][2] is True
    assert operations[1]==('delete',operations[0][1])

def test_live_history_does_not_override_current_question(client,tmp_path,monkeypatch):
    from secai_gateway.agent.runner import LinchRunner
    from secai_gateway.config import get_settings
    from linch.events import ResultEvent
    from linch.types import Usage
    state=configure(client)
    monkeypatch.setattr(get_settings(),'session_store_backend','memory')
    runner=LinchRunner(tmp_path/'gateway.db')
    history=[{'role':'user','content':'CVE nào có điểm CVSS cao nhất?'},
             {'role':'assistant','content':'Chưa có thống kê đầy đủ.'}]
    seen=[]
    original_session=runner.agent.session
    async def session(*args,**kwargs):
        current=await original_session(*args,**kwargs)
        async def run(self,prompt):
            rows=await runner.session_store.load_messages(kwargs['id'])
            seen.append((prompt,current.meta.copy(),[r.message for r in rows]))
            yield ResultEvent(subtype='success',stop_reason='end_turn',total_usage=Usage(),duration_ms=1,final_text='MFA là xác thực đa yếu tố.')
        monkeypatch.setattr(type(current),'run',run)
        return current
    monkeypatch.setattr(runner.agent,'session',session)
    state.runner=runner
    response=client.post('/review-api/live-pairs',headers=REVIEW,json={
        'prompt_messages':history+[{'role':'user','content':'MFA là gì?'}],
        'answer':'MFA yêu cầu nhiều yếu tố xác thực.','source_group':'original-session'})
    assert response.status_code==200 and response.json()['offered']
    pair=response.json()['assignment']
    assert 'MFA là xác thực đa yếu tố.' in {pair['left']['text'],pair['right']['text']}
    prompt,meta,messages=seen[0]
    assert prompt==meta['last_user_prompt']=='MFA là gì?'
    assert not meta['chat_prefetch_grounded']
    assert [(m.role,m.content[0].text) for m in messages]==[(m['role'],m['content']) for m in history]
    import asyncio
    assert asyncio.run(runner.session_store.list())==[]
    assert not runner.agent._sessions
