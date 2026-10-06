"""Job provenance, persistence and auth; GPU training is verified separately on Vast."""
import base64
import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_review_auth import client, ADMIN, REVIEW
from test_reward_pipeline import archives, example


def payload(tmp_path, title='QA training'):
    paths = archives(tmp_path, [example(i) for i in range(9)], title=title)
    return {'campaign_id': 'campaign', 'preference': base64.b64encode(paths[0].read_bytes()).decode(),
            'raw': base64.b64encode(paths[1].read_bytes()).decode()}


def test_jobs_validate_snapshot_deduplicate_and_persist(tmp_path):
    from secai_gateway.reward.training_jobs import TrainingJobs
    jobs = TrainingJobs(tmp_path/'jobs')
    data = payload(tmp_path)
    job = jobs.submit(data)
    assert job['status'] == 'queued' and job['purpose'] == 'demo'
    assert job['counts']['train'] + job['counts']['validation'] + job['counts']['test'] == 9
    assert jobs.submit(data)['id'] == job['id']
    assert TrainingJobs(tmp_path/'jobs').get(job['id']) == job
    assert jobs.list('campaign') == [job]
    assert 'preference' not in job and 'raw' not in job
    assert jobs.get('../private') is None


def test_jobs_reject_nonqa_and_tampered_exports(tmp_path):
    from secai_gateway.reward.training_jobs import TrainingJobs
    jobs = TrainingJobs(tmp_path/'jobs')
    with pytest.raises(ValueError, match='QA'):
        jobs.submit(payload(tmp_path, 'Expert real production'))
    with pytest.raises(ValueError, match='QA'):
        jobs.submit(payload(tmp_path, 'Do not train'))
    data = payload(tmp_path)
    data['campaign_id'] = 'another-campaign'
    with pytest.raises(ValueError, match='campaign'):
        jobs.submit(data)
    data['campaign_id'] = 'campaign'
    data['preference'] = 'not base64'
    with pytest.raises(ValueError): jobs.submit(data)


def test_crashed_preparation_is_not_a_published_job(tmp_path):
    from secai_gateway.reward.training_jobs import TrainingJobs
    from uuid import uuid4
    jobs = TrainingJobs(tmp_path/'jobs')
    good = jobs.submit(payload(tmp_path))
    staging = jobs.root/'.prepare-crashed'
    staging.mkdir()
    (staging/'job.json').write_text(json.dumps({**good, 'id':str(uuid4())}))
    assert jobs.list('campaign') == [good]


def test_private_training_service_auth_precedes_body(tmp_path):
    from secai_gateway.reward.training_jobs import TrainingJobs
    from secai_gateway.reward.training_service import create_training_service
    app = create_training_service(TrainingJobs(tmp_path/'jobs'), token_sha256=hashlib.sha256(b'private').hexdigest())
    http = TestClient(app)
    assert http.post('/jobs', content='bad').status_code == 401
    headers = {'Authorization':'Bearer private'}
    assert http.post('/jobs', headers=headers, content='bad').status_code == 422
    result = http.post('/jobs', headers=headers, json=payload(tmp_path))
    assert result.status_code == 202
    jid = result.json()['id']
    assert http.get('/jobs/'+jid).status_code == 401
    assert http.get('/jobs/'+jid, headers=headers).json()['id'] == jid
    assert http.get('/campaigns/campaign/jobs',headers=headers).json()['jobs'][0]['id'] == jid


def test_worker_ignores_staging_and_processes_published_queue(tmp_path):
    from secai_gateway.reward.training_jobs import TrainingJobs
    from secai_gateway.reward.training_service import create_training_service
    from uuid import uuid4
    import time
    jobs = TrainingJobs(tmp_path/'jobs')
    good = jobs.submit(payload(tmp_path))
    staging = jobs.root/'.prepare-crashed'
    staging.mkdir()
    (staging/'job.json').write_text(json.dumps({**good, 'id':str(uuid4())}))
    async def consume(store, job):
        store.update(job['id'], status='succeeded')
    app = create_training_service(jobs, token_sha256=hashlib.sha256(b'private').hexdigest(), runner=consume)
    with TestClient(app):
        until = time.monotonic()+3
        while jobs.get(good['id'])['status'] != 'succeeded' and time.monotonic() < until:
            time.sleep(.01)
        assert jobs.get(good['id'])['status'] == 'succeeded'


def test_gateway_exports_real_approvals_to_private_service(client, tmp_path):
    from secai_gateway.reward.training_jobs import TrainingJobs
    from secai_gateway.reward.training_service import create_training_service
    from test_review_store import pair
    from secai_gateway.api.review_schemas import Message
    cid = client.post('/review-api/campaigns',headers=ADMIN,json={'title':'QA gateway training'}).json()['id']
    rows = [pair().model_copy(update={'external_id':str(i), 'source_group':f'chat-{i}',
        'prompt_messages':[Message(role='user',content=f'QA question number {i}')]}) for i in range(9)]
    assert client.post(f'/review-api/campaigns/{cid}/imports',headers=ADMIN,
        content='\n'.join(row.model_dump_json() for row in rows)).status_code == 200
    items = client.get(f'/review-api/campaigns/{cid}/items',headers=ADMIN).json()['items']
    for item in items:
        assignment = client.post(f'/review-api/items/{item["id"]}/assignments',headers=ADMIN,
            json={'reviewer_ids':['expert']}).json()['items'][0]
        client.put(f'/review-api/assignments/{assignment["id"]}/judgment',headers=REVIEW,json={'decision':'left'})
        client.put(f'/review-api/items/{item["id"]}/adjudication',headers=ADMIN,
            json={'outcome':'winner','winning_candidate_id':assignment['left_candidate_id'],'reason':'QA reference'})
    remote = TestClient(create_training_service(TrainingJobs(tmp_path/'jobs'), token_sha256=hashlib.sha256(b'private').hexdigest()))
    settings = client.app.state.gateway.settings
    settings.reward_training_enabled = True
    settings.reward_training_url = 'http://private-training'
    settings.reward_training_token = 'private'
    def transport(req):
        response = remote.request(req.method,req.url.path,headers=dict(req.headers),content=req.content)
        return httpx.Response(response.status_code,content=response.content)
    client.app.state.gateway.reward_training_transport = httpx.MockTransport(transport)
    path = '/review-api/reward/training/campaigns/'+cid
    assert client.post(path,headers=ADMIN,json={'device':'cuda:999'}).status_code == 422
    result = client.post(path,headers=ADMIN,json={})
    assert result.status_code == 202
    job = result.json()
    assert sum(job['counts'].values()) == 9
    assert client.post(path,headers=ADMIN,json={}).json()['id'] == job['id']
    assert client.get(path,headers=ADMIN).json()['jobs'][0]['id'] == job['id']
    assert client.get('/review-api/reward/training/jobs/'+job['id'],headers=ADMIN).json()['id'] == job['id']


def test_gateway_training_admin_only_and_disabled(client):
    path = '/review-api/reward/training/campaigns/campaign'
    assert client.post(path, content='bad').status_code == 401
    assert client.post(path, headers=REVIEW, content='bad').status_code == 403
    assert client.get(path, headers=ADMIN).json()['enabled'] is False
    assert client.post(path, headers=ADMIN, json={}).status_code == 404


def test_gateway_training_list_outage_not_empty_success(client):
    settings = client.app.state.gateway.settings
    settings.reward_training_enabled = True
    settings.reward_training_url = 'http://private-training'
    settings.reward_training_token = 'secret'
    client.app.state.gateway.reward_training_transport = httpx.MockTransport(lambda req: httpx.Response(503))
    response = client.get('/review-api/reward/training/campaigns/campaign',headers=ADMIN)
    assert response.status_code == 503
    assert 'secret' not in response.text
