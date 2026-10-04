from test_review_auth import client, REVIEW
from test_review_live import FakeRunner

REVISE={'template':'# Report\n{{summary}}\n{{details}}\n','instruction':'Add a recommendation section','input_clean_confirmed':True}
RENDER={'template':'# Report\n{{summary}}\n','logs':'2026-10-04: one failed login','input_clean_confirmed':True}

def test_template_revise_render_isolated_and_no_tools(client):
    from secai_gateway.agent.profiles import PROFILES
    state=client.app.state.gateway
    state.runner=FakeRunner('# Report\n{{summary}}\n{{details}}\n## Recommendation\n')
    response=client.post('/review-api/report-templates/revise',headers=REVIEW,json=REVISE)
    assert response.status_code==200
    assert response.json()['draft'].endswith('## Recommendation\n')
    state.runner.answer='# Report\nOne failed login; cause unverified.'
    response=client.post('/review-api/report-templates/render',headers=REVIEW,json=RENDER)
    assert response.status_code==200 and response.json()['report'].startswith('# Report')
    profiles=[call[0] for call in state.runner.calls]
    assert profiles==['report_template_edit','report_template_render']
    for profile in profiles: assert not PROFILES[profile].tool_names and not PROFILES[profile].base
    ids=[call[2] for call in state.runner.calls]
    assert len(set(ids))==2 and all(i.startswith('report-template-') for i in ids)
    assert state.runner.deleted==ids

def test_template_clean_guard_limits_and_missing_placeholder(client):
    state=client.app.state.gateway; state.runner=FakeRunner('# Report without required fields')
    endpoint='/review-api/report-templates/revise'
    assert client.post(endpoint,headers=REVIEW,json={**REVISE,'input_clean_confirmed':False}).status_code==422
    assert client.post(endpoint,headers=REVIEW,json={k:v for k,v in REVISE.items() if k!='input_clean_confirmed'}).status_code==422
    assert client.post(endpoint,headers=REVIEW,json={**REVISE,'instruction':'4111111111111111'}).status_code==422
    assert not state.runner.calls
    assert client.post(endpoint,headers=REVIEW,json={**REVISE,'template':'x'*65537}).status_code==422
    assert client.post(endpoint,headers=REVIEW,content=b' '*(262144+1)).status_code==413
    response=client.post(endpoint,headers=REVIEW,json=REVISE)
    assert response.status_code==422 and 'placeholder' in response.json()['detail']

def test_template_timeout_empty_error_and_original_preserved(client,monkeypatch):
    import asyncio
    from secai_gateway.api import review_templates
    monkeypatch.setattr(review_templates,'MODEL_TIMEOUT',0.01)
    state=client.app.state.gateway; state.runner=FakeRunner('')
    endpoint='/review-api/report-templates/revise'
    assert client.post(endpoint,headers=REVIEW,json=REVISE).status_code==503
    state.runner.answer='Error: generation failed'
    assert client.post(endpoint,headers=REVIEW,json=REVISE).status_code==503
    async def hung(*args): await asyncio.Event().wait()
    state.runner.complete=hung
    assert client.post(endpoint,headers=REVIEW,json=REVISE).status_code==503
    assert len(state.runner.deleted)==3
    assert REVISE['template']=='# Report\n{{summary}}\n{{details}}\n'


def test_live_closed_campaign_does_not_generate(client):
    from test_review_live import configure,PAYLOAD
    from test_review_auth import ADMIN
    state=configure(client)
    result=client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()
    cid=result['assignment']['campaign_id']
    client.patch(f'/review-api/campaigns/{cid}',headers=ADMIN,json={'state':'closed'})
    state.review_live_next={}
    calls=len(state.runner.calls)
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert len(state.runner.calls)==calls

def test_generated_draft_must_fit_template_limit(client):
    state=client.app.state.gateway
    state.runner=FakeRunner('{{summary}}{{details}}'+'x'*65536)
    response=client.post('/review-api/report-templates/revise',headers=REVIEW,json=REVISE)
    assert response.status_code==422
    assert '64 KiB' in response.json()['detail']

def test_template_releases_live_session_before_deleting_history(client):
    from types import SimpleNamespace
    state=client.app.state.gateway; state.runner=FakeRunner(REVISE['template'])
    operations=[]
    async def release(sid,force=False): operations.append(('release',sid,force))
    async def delete(sid): operations.append(('delete',sid))
    state.runner.agent=SimpleNamespace(release_session=release)
    state.runner.session_store=SimpleNamespace(delete=delete)
    assert client.post('/review-api/report-templates/revise',headers=REVIEW,json=REVISE).status_code==200
    assert operations[0][0]=='release' and operations[0][2] is True
    assert operations[1]==('delete',operations[0][1])

def test_template_cleanup_real_linch_registry_without_model_call(client,tmp_path,monkeypatch):
    import asyncio
    from secai_gateway.agent.runner import LinchRunner
    from secai_gateway.config import get_settings
    settings=get_settings()
    monkeypatch.setattr(settings,'session_store_backend','memory')
    runner=LinchRunner(tmp_path/'gateway.db')
    generated_ids=[]
    async def complete(profile,prompt,sid):
        session=await runner.agent.session(id=sid)
        runner._apply_profile(session,profile,prompt)
        assert session.tools_override is not None
        generated_ids.append(sid)
        return REVISE['template'],{}
    runner.complete=complete
    client.app.state.gateway.runner=runner
    assert client.post('/review-api/report-templates/revise',headers=REVIEW,json=REVISE).status_code==200
    sid=generated_ids[0]
    assert sid not in runner.agent._sessions
    assert asyncio.run(runner.session_store.load(sid)) is None
