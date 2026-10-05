import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from test_review_auth import client, ADMIN, REVIEW


def test_sealed_artifact_integrity_and_production_gate(tmp_path):
    from secai_gateway.reward.artifact import seal_artifact, verify_artifact
    from secai_gateway.reward.pipeline import BASE_MODEL
    (tmp_path/'model.safetensors').write_bytes(b'synthetic-not-real-model')
    training={'schema_version':1,'base_model':BASE_MODEL,'base_revision':'a'*40,'purpose':'demo',
              'mode':'head','max_length':512,'policy':None}
    (tmp_path/'training.json').write_text(json.dumps(training))
    (tmp_path/'evaluation.json').write_text(json.dumps({'purpose':'demo','test':{},'gate':{'eligible':False}}))
    digest=seal_artifact(tmp_path)
    assert verify_artifact(tmp_path,digest,production=False)['base_model']==BASE_MODEL
    with pytest.raises(ValueError,match='production'):verify_artifact(tmp_path,digest)
    (tmp_path/'model.safetensors').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='integrity'):verify_artifact(tmp_path,digest,production=False)


def test_private_service_auth_scores_and_identical_answers():
    from secai_gateway.reward.service import create_service
    class Scorer:
        def scores(self,prompt,answers):return [float(len(a)) for a in answers]
    app=create_service(Scorer(),artifact_id='a'*64,token_sha256=hashlib.sha256(b'service-secret').hexdigest())
    http=TestClient(app)
    payload={'prompt':[{'role':'user','content':'MFA là gì?'}],'answers':['a','bb']}
    assert http.post('/score',json=payload).status_code==401
    headers={'Authorization':'Bearer service-secret'}
    result=http.post('/score',headers=headers,json=payload)
    assert result.status_code==200 and result.json()=={'artifact_id':'a'*64,'scores':[1.,2.]}
    assert http.post('/score',headers=headers,json={**payload,'prompt':[{'role':'system','content':'secret'}]}).status_code==422
    assert http.post('/score',headers=headers,json={**payload,'answers':['x']*5}).status_code==422
    assert http.post('/score',headers=headers,content=b' '*(262144+1)).status_code==413
    assert http.post('/score',headers=headers,json={**payload,'answers':['same','same']}).json()['scores']==[4.,4.]


def configure_gateway(client, handler):
    settings=client.app.state.gateway.settings
    settings.reward_enabled=True
    settings.reward_service_url='http://reward.internal'
    settings.reward_service_token='private-upstream-token'
    settings.reward_artifact_sha256='a'*64
    settings.reward_timeout_seconds=1
    client.app.state.gateway.reward_transport=httpx.MockTransport(handler)


def test_gateway_reward_auth_disabled_and_pinned_result(client):
    payload={'prompt':[{'role':'user','content':'MFA là gì?'}],'answers':['A','B']}
    assert client.post('/review-api/reward/score',headers=ADMIN,json=payload).status_code==404
    def handler(request):
        assert request.headers['Authorization']=='Bearer private-upstream-token'
        return httpx.Response(200,json={'artifact_id':'a'*64,'scores':[2.1,0.7]})
    configure_gateway(client,handler)
    assert client.post('/review-api/reward/score',json=payload).status_code==401
    assert client.post('/review-api/reward/score',headers=REVIEW,json=payload).status_code==403
    response=client.post('/review-api/reward/score',headers=ADMIN,json=payload)
    assert response.status_code==200 and response.json()['preferred_index']==0
    assert response.json()['margin']==pytest.approx(1.4)
    assert 'private-upstream-token' not in response.text


@pytest.mark.parametrize('body',[{'artifact_id':'b'*64,'scores':[1,2]},
    {'artifact_id':'a'*64,'scores':[1]}, {'artifact_id':'a'*64,'scores':['bad',2]}])
def test_gateway_rejects_wrong_artifact_or_scores(client,body):
    configure_gateway(client,lambda request:httpx.Response(200,json=body))
    result=client.post('/review-api/reward/score',headers=ADMIN,
                      json={'prompt':[{'role':'user','content':'Q'}],'answers':['A','B']})
    assert result.status_code==503 and 'scores' not in result.json()


def test_upstream_timeout_and_score_overflow_fail_closed(client):
    payload={'prompt':[{'role':'user','content':'Q'}],'answers':['A','B']}
    def timeout(request):raise httpx.ReadTimeout('private upstream detail')
    configure_gateway(client,timeout)
    response=client.post('/review-api/reward/score',headers=ADMIN,json=payload)
    assert response.status_code==503 and 'private upstream' not in response.text
    configure_gateway(client,lambda request:httpx.Response(200,json={'artifact_id':'a'*64,'scores':[1e308,-1e308]}))
    assert client.post('/review-api/reward/score',headers=ADMIN,json=payload).status_code==503


def test_gateway_tie_and_limits(client):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'artifact_id':'a'*64,'scores':[1,1]})
    configure_gateway(client,handler)
    result=client.post('/review-api/reward/score',headers=ADMIN,
                      json={'prompt':[{'role':'user','content':'Q'}],'answers':['same','same']})
    assert result.json()['preferred_index'] is None and result.json()['margin']==0
    assert client.post('/review-api/reward/score',headers=ADMIN,content=b' '*(262144+1)).status_code==413
    assert len(calls)==1


def test_gateway_rejects_oversize_upstream_response(client):
    configure_gateway(client,lambda request:httpx.Response(200,json={
        'artifact_id':'a'*64,'scores':[1,2],'extra':'x'*20000}))
    response=client.post('/review-api/reward/score',headers=ADMIN,
        json={'prompt':[{'role':'user','content':'Q'}],'answers':['A','B']})
    assert response.status_code==503


def test_forged_invalid_metric_report_cannot_start_production(tmp_path):
    from secai_gateway.reward.artifact import seal_artifact,verify_artifact
    from secai_gateway.reward.pipeline import BASE_MODEL
    (tmp_path/'model.safetensors').write_bytes(b'not-a-real-model')
    policy={'min_test_records':1,'min_accuracy':0.8,'min_wilson_lower_95':0.7,'max_invariance_delta':0.001}
    metadata={'base_model':BASE_MODEL,'base_revision':'a'*40,'purpose':'production',
              'mode':'head','max_length':512,'policy':policy}
    (tmp_path/'training.json').write_text(json.dumps(metadata))
    (tmp_path/'evaluation.json').write_text(json.dumps({'purpose':'production',
        'test':{'records':10.5,'accuracy':2.0,'accuracy_wilson_lower_95':1.5,
                'max_swap_score_delta':-1,'max_equal_score_delta':-1},
        'gate':{'eligible':True,'reasons':[]}}))
    digest=seal_artifact(tmp_path)
    with pytest.raises(ValueError,match='production'):verify_artifact(tmp_path,digest)
