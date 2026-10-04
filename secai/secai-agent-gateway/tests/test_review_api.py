import json
import pytest
from test_review_auth import client, ADMIN, REVIEW, OTHER
from test_review_store import pair

def prepared(client, reviewers=('expert',)):
    cid = client.post('/review-api/campaigns',headers=ADMIN,json={'title':'Pilot'}).json()['id']
    assert client.post(f'/review-api/campaigns/{cid}/imports',headers=ADMIN,content=pair().model_dump_json()).json() == {'imported':1,'skipped':0}
    item = client.get(f'/review-api/campaigns/{cid}/items',headers=ADMIN).json()['items'][0]
    assignments = client.post(f'/review-api/items/{item["id"]}/assignments',headers=ADMIN,json={'reviewer_ids':list(reviewers)}).json()['items']
    return cid,item,assignments

def test_complete_workflow_and_idor(client):
    cid,item,assignments = prepared(client,('expert','other'))
    aid = assignments[0]['id']; iid=item['id']
    detail = client.get(f'/review-api/assignments/{aid}',headers=REVIEW).json()
    assert detail == client.get(f'/review-api/assignments/{aid}',headers=REVIEW).json()
    assert 'provenance' not in json.dumps(detail) and 'candidate_id' not in json.dumps(detail)
    assert client.get(f'/review-api/assignments/{aid}',headers=OTHER).status_code == 404
    assert client.put(f'/review-api/assignments/{aid}/judgment',headers=OTHER,json={'decision':'left'}).status_code == 404
    assert client.get(f'/review-api/items/{iid}/judgments',headers=REVIEW).status_code == 403
    assert client.put(f'/review-api/assignments/{aid}/judgment',headers=REVIEW,json={'decision':'both_bad'}).status_code == 422
    winner = {'outcome':'winner','winning_candidate_id':assignments[0]['left_candidate_id'],'reason':'Technically verified'}
    assert client.put(f'/review-api/items/{iid}/adjudication',headers=ADMIN,json=winner).status_code == 409
    first=client.put(f'/review-api/assignments/{aid}/judgment',headers=REVIEW,json={'decision':'left'}).json()
    assert client.put(f'/review-api/assignments/{aid}/judgment',headers=REVIEW,json={'decision':'left'}).json()==first
    assert client.put(f'/review-api/assignments/{aid}/judgment',headers=REVIEW,json={'decision':'right'}).status_code==409
    other=assignments[1]
    # Choose the same candidate regardless of each assignment's shuffled positions.
    decision='left' if other['left_candidate_id']==winner['winning_candidate_id'] else 'right'
    assert client.put(f'/review-api/assignments/{other["id"]}/judgment',headers=OTHER,json={'decision':decision}).status_code==200
    stats=client.get(f'/review-api/campaigns/{cid}/stats',headers=ADMIN).json()
    assert stats['disagreements']==0 and stats['multi_reviewed_items']==1
    normalized=client.get(f'/review-api/items/{iid}/judgments',headers=ADMIN).json()
    assert {j['winning_candidate_id'] for j in normalized['judgments']}=={winner['winning_candidate_id']}
    approved=client.put(f'/review-api/items/{iid}/adjudication',headers=ADMIN,json=winner).json()
    assert client.put(f'/review-api/items/{iid}/adjudication',headers=ADMIN,json=winner).json()==approved
    assert client.post(f'/review-api/items/{iid}/assignments',headers=ADMIN,json={'reviewer_ids':['expert']}).status_code==409
    assert client.delete(f'/review-api/assignments/{aid}',headers=ADMIN).status_code==409

def test_closed_import_atomic_validation_limits_and_pagination(client):
    cid,item,assignments=prepared(client)
    path=f'/review-api/campaigns/{cid}'
    payload=pair('valid').model_dump_json()+'\n'+pair('invalid').model_dump_json().replace('"user"','"system"')
    assert client.post(path+'/imports',headers=ADMIN,content=payload).status_code==422
    assert client.get(path+'/items',headers=ADMIN).json()['total']==1
    assert client.post(path+'/imports',headers=ADMIN,content=b'\xff').status_code==422
    assert client.post(path+'/imports',headers={**ADMIN,'Content-Length':'10485761'},content='x').status_code==413
    assert client.post(path+'/imports',headers=ADMIN,content='\n'.join(pair(str(i)).model_dump_json() for i in range(501))).status_code==413
    assert client.get(path+'/items?limit=101',headers=ADMIN).status_code==422
    assert client.get(path+'/items?offset=1&limit=1',headers=ADMIN).json()['items']==[]
    assert client.patch(path,headers=ADMIN,json={'state':'closed'}).status_code==200
    assert client.post(path+'/imports',headers=ADMIN,content=pair('new').model_dump_json()).status_code==409
    assert client.put(f'/review-api/assignments/{assignments[0]["id"]}/judgment',headers=REVIEW,json={'decision':'tie'}).status_code==409
    assert client.patch(path,headers=ADMIN,json={'state':'open'}).status_code==200
    assert client.delete(f'/review-api/assignments/{assignments[0]["id"]}',headers=ADMIN).status_code==204
    assert client.get('/review-api/campaigns',headers=REVIEW).json()['total']==0

def test_no_identity_spoof_and_other_tag_reason(client):
    _,_,assignments=prepared(client)
    path=f'/review-api/assignments/{assignments[0]["id"]}/judgment'
    assert client.put(path,headers=REVIEW,json={'decision':'left','user_id':'admin'}).status_code==422
    assert client.put(path,headers=REVIEW,json={'decision':'left','error_tags':{'left':['other']}}).status_code==422
    assert client.put(path,headers=REVIEW,json={'decision':'tie','error_tags':{'left':['unsafe']},'comment':'Verified issue'}).status_code==200

@pytest.mark.parametrize('messages',[
    [{'role':'assistant','content':'No question'}],
    [{'role':'user','content':'Question'},{'role':'assistant','content':'Already answered'}],
    [{'role':'assistant','content':'Leading answer'},{'role':'user','content':'Question'}],
])
def test_import_rejects_invalid_prompt_atomically(client,messages):
    cid,item,_=prepared(client)
    valid=pair('valid-new').model_dump_json()
    invalid=pair('invalid-new').model_dump()
    invalid['prompt_messages']=messages
    response=client.post(f'/review-api/campaigns/{cid}/imports',headers=ADMIN,
                         content=valid+'\n'+json.dumps(invalid))
    assert response.status_code==422
    remaining=client.get(f'/review-api/campaigns/{cid}/items',headers=ADMIN).json()
    assert remaining['total']==1 and remaining['items'][0]['id']==item['id']
