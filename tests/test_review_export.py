import hashlib
import io
import json
import zipfile
from test_review_auth import client, ADMIN, REVIEW
from test_review_api import prepared
from test_review_store import pair
from secai_gateway.api.reviews import build_export

def test_export_correct_after_shuffle_and_manifest(client):
    cid,item,assignments=prepared(client)
    a=assignments[0]
    client.put(f'/review-api/assignments/{a["id"]}/judgment',headers=REVIEW,json={'decision':'right'})
    client.put(f'/review-api/items/{item["id"]}/adjudication',headers=ADMIN,json={'outcome':'winner','winning_candidate_id':a['right_candidate_id'],'reason':'Verified'})
    snapshot=client.app.state.gateway.review_store.export_snapshot(cid)
    response=client.get(f'/review-api/campaigns/{cid}/export?format=preference',headers=ADMIN)
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        body=archive.read('preference.jsonl'); manifest=json.loads(archive.read('manifest.json'))
    record=json.loads(body)
    expected={c['id']:c['text'] for c in item['candidates']}
    assert record['chosen']==expected[a['right_candidate_id']]
    assert record['rejected']==expected[a['left_candidate_id']]
    assert record['prompt']==item['prompt_messages']
    assert manifest['files']['preference.jsonl']['sha256']==hashlib.sha256(body).hexdigest()
    assert manifest['counts']=={'items':1,'records':1}
    assert b'admin-token' not in body and b'token_sha256' not in body
    # A detached transaction snapshot remains stable after later imports.
    client.post(f'/review-api/campaigns/{cid}/imports',headers=ADMIN,content=pair('new').model_dump_json())
    with zipfile.ZipFile(io.BytesIO(build_export(snapshot,'raw'))) as archive:
        assert len(archive.read('raw.jsonl').splitlines())==1

def test_raw_retains_non_winners_and_preference_excludes_unapproved(client):
    cid,item,assignments=prepared(client)
    for outcome in ('tie','both_bad','insufficient_context'):
        client.post(f'/review-api/campaigns/{cid}/imports',headers=ADMIN,content=pair(outcome).model_dump_json())
        new=next(i for i in client.get(f'/review-api/campaigns/{cid}/items',headers=ADMIN).json()['items'] if i['external_id']==outcome)
        aid=client.post(f'/review-api/items/{new["id"]}/assignments',headers=ADMIN,json={'reviewer_ids':['expert']}).json()['items'][0]['id']
        assert client.put(f'/review-api/assignments/{aid}/judgment',headers=REVIEW,json={'decision':outcome,'comment':'Explanation'}).status_code==200
        assert client.put(f'/review-api/items/{new["id"]}/adjudication',headers=ADMIN,json={'outcome':outcome,'reason':'Explanation'}).status_code==200
    for fmt,count in [('raw',4),('preference',0)]:
        response=client.get(f'/review-api/campaigns/{cid}/export?format={fmt}',headers=ADMIN)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            lines=archive.read(fmt+'.jsonl').splitlines()
        assert len(lines)==count
        if fmt=='raw':
            assert {json.loads(line)['judgments'][0]['decision'] for line in lines if json.loads(line)['judgments']}=={'tie','both_bad','insufficient_context'}
