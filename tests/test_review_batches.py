import concurrent.futures
import pytest
from secai_gateway.state.review_store import ReviewStore, ReviewError
from secai_gateway.api.review_schemas import ReviewItemInput, JudgmentInput, AdjudicationInput
from test_review_auth import client, ADMIN, REVIEW


def submitted(store, n=12, prefix="Question"):
    c = store.create_campaign('QA batch test', 'security_qa_v1', 'admin')
    for i in range(n):
        v = ReviewItemInput(external_id=str(i), prompt_messages=[{'role':'user','content':f'{prefix} {i}'}],
            candidates=[{'id':'a','text':f'Good {i}'},{'id':'b','text':f'Bad {i}'}], source_group=f'group-{i}')
        store.import_items(c['id'], [v], 'admin')
    rows = store.list_items(c['id'])
    for row in rows:
        a = store.assign(row['id'], ['expert'], 'admin')[0]
        store.submit_judgment(a['id'], 'expert', JudgmentInput(decision='left' if a['left_candidate_id']=='a' else 'right'))
    return rows


def test_overview_bulk_canonical_and_pagination(tmp_path):
    s=ReviewStore(tmp_path/'r.db'); rows=submitted(s)
    page=s.overview('pending','all','Question',0,5)
    assert page['total']==12 and len(page['items'])==5
    preview=s.bulk_preview('all','Question')
    assert preview['eligible_count']==12
    result=s.bulk_approve('all','Question',preview['snapshot_version'],'admin')
    assert result['approved']==12
    assert s.overview('pending','all','',0,10)['total']==0
    assert s.overview('approved','all','',0,10)['total']==12
    for r in rows: assert s.item_judgments(r['id'])['adjudication']['winning_candidate_id']=='a'
    assert ReviewStore(s.path).overview('approved','all','',0,10)['total']==12


def test_bulk_stale_does_not_approve_new_feedback(tmp_path):
    s=ReviewStore(tmp_path/'r.db'); submitted(s,3); p=s.bulk_preview('all','')
    row=s.overview('pending','all','',0,1)['items'][0]
    s.adjudicate(row['id'], AdjudicationInput(outcome='skip',reason='Excluded'), 'admin')
    with pytest.raises(ReviewError, match='changed'): s.bulk_approve('all','',p['snapshot_version'],'admin')
    assert s.overview('pending','all','',0,10)['total']==2


def test_batch_threshold_atomic_snapshot_retry(tmp_path):
    s=ReviewStore(tmp_path/'r.db'); submitted(s,1000)
    p=s.bulk_preview('qa',''); s.bulk_approve('qa','',p['snapshot_version'],'admin')
    eligibility=s.training_eligibility('demo')
    assert eligibility['valid_new_count']==1000 and eligibility['eligible']
    def reserve(key): return s.reserve_training_batch(key,'demo',eligibility['snapshot_version'],'admin')
    with concurrent.futures.ThreadPoolExecutor() as pool:
        results=list(pool.map(reserve,['same-key']*4))
    assert len({r['id'] for r in results})==1
    assert s.training_eligibility('demo')['valid_new_count']==0
    batch=results[0]; assert batch['sample_count']==1000
    assert s.batch_items(batch['id'],0,10)['total']==1000
    assert s.batch_snapshot(batch['id'])['items'][0]['adjudication']['winning_candidate_id']=='a'
    with pytest.raises(ReviewError): s.retry_batch(batch['id'],'retry','admin')
    run=s.batch_detail(batch['id'])['runs'][0]
    s.update_training_run(run['id'], {'status':'failed','error':'GPU unavailable'})
    retry=s.retry_batch(batch['id'],'retry','admin')
    assert retry['batch_id']==batch['id'] and retry['id']!=run['id']
    assert s.retry_batch(batch['id'],'retry','admin')['id']==retry['id']
    assert s.batch_snapshot(batch['id'])['items']==s.batch_snapshot(batch['id'])['items']


def test_999_and_production_blocked(tmp_path):
    s=ReviewStore(tmp_path/'r.db'); submitted(s,999)
    p=s.bulk_preview('qa',''); s.bulk_approve('qa','',p['snapshot_version'],'admin')
    assert not s.training_eligibility('demo')['eligible']
    assert not s.training_eligibility('production')['eligible']


def test_overview_bulk_api_auth_before_body(client):
    assert client.get('/review-api/overview',headers=REVIEW).status_code==403
    assert client.post('/review-api/approvals/bulk',headers=REVIEW,content='bad').status_code==403
    assert client.get('/review-api/overview',headers=ADMIN).status_code==200


def test_bulk_keeps_tie_conflict_and_incomplete(tmp_path):
    s=ReviewStore(tmp_path/'r.db'); rows=submitted(s,3)
    # Pending assignment and conflicting user vote are kept for individual review.
    s.assign(rows[0]['id'],['second'],'admin')
    a=s.assign(rows[1]['id'],['second'],'admin')[0]
    s.submit_judgment(a['id'],'second',JudgmentInput(decision='left' if a['left_candidate_id']=='b' else 'right'))
    p=s.bulk_preview('all',''); assert p['eligible_count']==1 and p['skipped_count']==2
    s.bulk_approve('all','',p['snapshot_version'],'admin')
    assert s.overview('pending','all','',0,20)['total']==2


def test_batch_api_lost_response_reconciles_same_remote_job(client,tmp_path):
    import httpx,hashlib
    from fastapi.testclient import TestClient
    from secai_gateway.reward.training_jobs import TrainingJobs
    from secai_gateway.reward.training_service import create_training_service
    store=client.app.state.gateway.review_store
    submitted(store,20); p=store.bulk_preview('qa','');store.bulk_approve('qa','',p['snapshot_version'],'admin')
    with store.connection(True) as db: db.execute("UPDATE training_policy SET minimum_pairs=3 WHERE purpose='demo'")
    settings=client.app.state.gateway.settings;settings.reward_training_enabled=True;settings.reward_training_url='http://private';settings.reward_training_token='private'
    jobs=TrainingJobs(tmp_path/'jobs'); remote=TestClient(create_training_service(jobs,token_sha256=hashlib.sha256(b'private').hexdigest()))
    lost=[True]
    def transport(req):
        response=remote.request(req.method,req.url.path,headers=dict(req.headers),content=req.content)
        if req.method=='POST' and lost[0]: lost[0]=False;raise httpx.ReadTimeout('lost')
        return httpx.Response(response.status_code,content=response.content)
    client.app.state.gateway.reward_training_transport=httpx.MockTransport(transport)
    prefix='/review-api/reward/training'
    e=client.get(prefix+'/eligibility',headers=ADMIN).json()
    data={'request_key':'same-browser-request','purpose':'demo','snapshot_version':e['snapshot_version']}
    assert client.post(prefix+'/batches',headers=REVIEW,content='bad').status_code==403
    assert client.post(prefix+'/batches',headers=ADMIN,json=data).status_code==503
    response=client.post(prefix+'/batches',headers=ADMIN,json=data)
    assert response.status_code==202,response.text
    batch=response.json();assert len(jobs.list())==1 and len(batch['runs'])==1
    assert client.get(prefix+'/batches/'+batch['id']+'/items',headers=ADMIN).json()['total']==20
    assert client.post(prefix+'/batches/'+batch['id']+'/retry',headers=ADMIN,json={'request_key':'retry'}).status_code==409


def test_holdout_bindings_persist_between_batches(tmp_path):
    s=ReviewStore(tmp_path/'r.db');submitted(s,20)
    with s.connection(True) as db:db.execute("UPDATE training_policy SET minimum_pairs=3 WHERE purpose='demo'")
    p=s.bulk_preview('qa','');s.bulk_approve('qa','',p['snapshot_version'],'admin')
    e=s.training_eligibility('demo');b=s.reserve_training_batch('first','demo',e['snapshot_version'],'admin')
    first=s.batch_partitions(b['id']);old=s.batch_snapshot(b['id'])['items']
    s.update_training_run(b['runs'][0]['id'],{'status':'failed','error':'test'})
    submitted(s,20,prefix='New question');p=s.bulk_preview('qa','');s.bulk_approve('qa','',p['snapshot_version'],'admin')
    e=s.training_eligibility('demo');b2=s.reserve_training_batch('second','demo',e['snapshot_version'],'admin')
    second=s.batch_partitions(b2['id']);new=s.batch_snapshot(b2['id'])['items']
    by_group={r['source_group']:first[r['id']] for r in old}
    assert all(second[r['id']]==by_group[r['source_group']] for r in new)


def test_duplicate_aliases_cannot_become_new_batch(tmp_path):
    s=ReviewStore(tmp_path/'r.db');submitted(s,20);submitted(s,20)
    with s.connection(True) as db: db.execute("UPDATE training_policy SET minimum_pairs=3 WHERE purpose='demo'")
    p=s.bulk_preview('qa','');s.bulk_approve('qa','',p['snapshot_version'],'admin')
    e=s.training_eligibility('demo');assert e['valid_new_count']==20
    b=s.reserve_training_batch('dedup','demo',e['snapshot_version'],'admin')
    s.update_training_run(b['runs'][0]['id'],{'status':'succeeded'})
    assert s.training_eligibility('demo')['valid_new_count']==0
    assert s.overview('approved','qa','',0,20)['total']==0
    submitted(s,20);p=s.bulk_preview('qa','');s.bulk_approve('qa','',p['snapshot_version'],'admin')
    assert s.training_eligibility('demo')['valid_new_count']==0
