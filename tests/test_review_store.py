import concurrent.futures
import pytest
from secai_gateway.state.review_store import ReviewStore, ReviewError
from secai_gateway.api.review_schemas import ReviewItemInput, JudgmentInput, AdjudicationInput


def pair(external='x', text='first'):
    return ReviewItemInput(external_id=external, prompt_messages=[{'role':'user','content':'Question'}], candidates=[{'id':'a','text':text},{'id':'b','text':'second'}])

@pytest.fixture
def store(tmp_path):
    return ReviewStore(tmp_path / 'reviews.db')

def setup_item(store):
    campaign = store.create_campaign('Pilot', 'security_qa_v1', 'admin')
    store.import_items(campaign['id'], [pair()], 'admin')
    item = store.list_items(campaign['id'])[0]
    assignment = store.assign(item['id'], ['reviewer'], 'admin')[0]
    return campaign, item, assignment

def test_import_atomic_conflict(store):
    c = store.create_campaign('Pilot', 'security_qa_v1', 'admin')
    assert store.import_items(c['id'], [pair()], 'admin') == {'imported':1,'skipped':0}
    assert store.import_items(c['id'], [pair()], 'admin') == {'imported':0,'skipped':1}
    with pytest.raises(ReviewError):
        store.import_items(c['id'], [pair('new'),pair(text='changed')], 'admin')
    assert len(store.list_items(c['id'])) == 1

def test_parallel_submission_unique(store):
    _, _, a = setup_item(store)
    payload = JudgmentInput(decision='left')
    with concurrent.futures.ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda _: store.submit_judgment(a['id'], 'reviewer', payload), range(8)))
    assert len({x['id'] for x in results}) == 1
    with pytest.raises(ReviewError):
        store.submit_judgment(a['id'], 'reviewer', JudgmentInput(decision='right'))

def test_restart_preserves_snapshot(store):
    c, item, a = setup_item(store)
    reopened = ReviewStore(store.path)
    assert reopened.assignment_detail(a['id'], 'reviewer') == store.assignment_detail(a['id'], 'reviewer')
    assert reopened.list_items(c['id'])[0]['content_sha256'] == item['content_sha256']

def test_adjudication_requires_all_submissions(store):
    c, item, a = setup_item(store)
    outcome = AdjudicationInput(outcome='winner', winning_candidate_id='a', reason='Verified')
    with pytest.raises(ReviewError): store.adjudicate(item['id'], outcome, 'admin')
    store.submit_judgment(a['id'], 'reviewer', JudgmentInput(decision='left'))
    approved = store.adjudicate(item['id'], outcome, 'admin')
    assert store.adjudicate(item['id'], outcome, 'admin') == approved
    with pytest.raises(ReviewError): store.assign(item['id'], ['second'], 'admin')
    with pytest.raises(ReviewError): store.adjudicate(item['id'], AdjudicationInput(outcome='tie',reason='changed'), 'admin')

def test_snapshot_preserves_answer_and_prompt_whitespace(store):
    raw = {'external_id':'spacing','prompt_messages':[{'role':'user','content':'  Question\n'}], 'candidates':[{'id':'a','text':'    code\n\n'},{'id':'b','text':' alternative\n'}], 'evidence':'  evidence\n'}
    value = ReviewItemInput.model_validate(raw)
    campaign=store.create_campaign('Spacing','security_qa_v1','admin')
    store.import_items(campaign['id'],[value],'admin')
    item=store.list_items(campaign['id'])[0]
    assert item['candidates'][0]['text']==raw['candidates'][0]['text']
    assert item['prompt_messages']==raw['prompt_messages']
    assert item['evidence']==raw['evidence']
    assignment=store.assign(item['id'],['expert'],'admin')[0]
    detail=store.assignment_detail(assignment['id'],'expert')
    assert detail['prompt_messages']==raw['prompt_messages']
    candidates={candidate['id']:candidate['text'] for candidate in raw['candidates']}
    assert detail['left']['text']==candidates[assignment['left_candidate_id']]
    store.submit_judgment(assignment['id'],'expert',JudgmentInput(decision='left'))
    store.adjudicate(item['id'],AdjudicationInput(outcome='winner',winning_candidate_id=assignment['left_candidate_id'],reason='verified'),'admin')
    from secai_gateway.api.reviews import build_export
    import io, json, zipfile
    with zipfile.ZipFile(io.BytesIO(build_export(store.export_snapshot(campaign['id']),'preference'))) as archive:
        record=json.loads(archive.read('preference.jsonl'))
    assert record['prompt']==raw['prompt_messages']
    assert record['chosen']==candidates[assignment['left_candidate_id']]
    assert record['rejected']==candidates[assignment['right_candidate_id']]
    with pytest.raises(ValueError):
        ReviewItemInput.model_validate({**raw,'candidates':[{'id':'a','text':' same '},{'id':'b','text':'same'}]})
