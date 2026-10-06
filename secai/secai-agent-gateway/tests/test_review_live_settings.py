import json
from test_review_auth import client, ADMIN, REVIEW, OTHER
from test_review_live import configure, PAYLOAD
from secai_gateway.state.review_store import ReviewStore

PATH='/review-api/live-settings'

def test_admin_only_and_auth_before_body(client):
    assert client.get(PATH).status_code==401
    assert client.put(PATH,content=b'not json').status_code==401
    assert client.get(PATH,headers=REVIEW).status_code==403
    assert client.put(PATH,headers=REVIEW,content=b'not json').status_code==403
    assert client.put(PATH,headers=ADMIN,content=b'x'*1025).status_code==413
    for value in [-1,101,True,'50',12.5]:
        assert client.put(PATH,headers=ADMIN,json={'sample_percent':value}).status_code==422
    assert client.put(PATH,headers=ADMIN,json={'sample_percent':50,'cooldown_seconds':0}).status_code==422

def test_persistent_global_settings_audit_and_live_sampling(client):
    state=configure(client,0.1)
    assert client.get(PATH,headers=ADMIN).json()['sample_percent']==10
    assert client.put(PATH,headers=ADMIN,json={'sample_percent':100}).json()['sample_percent']==100
    # A fresh store/process still uses the database override rather than env 10%.
    state.review_store=ReviewStore(state.review_store.path)
    assert client.get(PATH,headers=ADMIN).json()['sample_percent']==100
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()['offered']
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert len(state.runner.calls)==1
    assert client.post('/review-api/live-pairs',headers=OTHER,json=PAYLOAD).json()['offered']
    assert len(state.runner.calls)==2
    assert client.put(PATH,headers=ADMIN,json={'sample_percent':0}).status_code==200
    state.review_live_next.clear()
    assert client.post('/review-api/live-pairs',headers=REVIEW,json=PAYLOAD).json()=={'offered':False}
    assert len(state.runner.calls)==2
    with state.review_store.connection() as db:
        rows=db.execute("SELECT actor_id,details FROM audit_events WHERE action='live.settings.update' ORDER BY rowid").fetchall()
    assert len(rows)==2 and all(r['actor_id']=='admin' for r in rows)
    assert json.loads(rows[0]['details'])=={'before_percent':10,'after_percent':100}
