import hashlib
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from secai_gateway.api.review_auth import load_credentials
from secai_gateway.api.reviews import router
from secai_gateway.state.review_store import ReviewStore

@pytest.fixture
def client(tmp_path):
    file = tmp_path / 'credentials.json'
    file.write_text(json.dumps({'credentials':[{'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'actor_id':actor,'role':role} for token,actor,role in [('admin-token','admin','admin'),('review-token','expert','reviewer'),('other-token','other','reviewer')]]}))
    app = FastAPI()
    app.state.gateway = SimpleNamespace(settings=SimpleNamespace(review_enabled=True),review_credentials=load_credentials(file),review_store=ReviewStore(tmp_path/'reviews.db'))
    app.include_router(router)
    return TestClient(app)

ADMIN = {'Authorization':'Bearer admin-token'}
REVIEW = {'Authorization':'Bearer review-token'}
OTHER = {'Authorization':'Bearer other-token'}

def test_missing_or_revoked_token_401(client):
    assert client.get('/review-api/me').status_code == 401
    assert client.get('/review-api/me',headers={'Authorization':'Bearer revoked'}).status_code == 401
    assert client.get('/review-api/me',headers=REVIEW).json() == {'actor_id':'expert','role':'reviewer'}

def test_reviewer_admin_action_403(client):
    assert client.post('/review-api/campaigns',headers=REVIEW,json={'title':'X'}).status_code == 403

def test_disabled_routes_unavailable(client):
    client.app.state.gateway.settings.review_enabled = False
    assert client.get('/review-api/me',headers=ADMIN).status_code == 404

def test_invalid_credential_config_fails_closed(tmp_path):
    for data in ({}, {'credentials':[]}, {'credentials':[{'actor_id':'x','role':'admin','token_sha256':'short'}]}):
        file=tmp_path/'bad.json'; file.write_text(json.dumps(data))
        with pytest.raises(ValueError): load_credentials(file)
