"""Review traffic stays bounded without throttling ordinary labeling batches."""
from test_review_auth import client, ADMIN
from secai_gateway.api.middleware import configure_middleware, route_limit
from secai_gateway.state.ratelimit import FixedWindowRateLimiter


def configure(client):
    state = client.app.state.gateway
    state.settings.cors_origins = ['http://localhost:5173']
    state.rate_limiter = FixedWindowRateLimiter()
    configure_middleware(client.app, state)


def test_review_crud_limit_keeps_model_routes_bounded(client):
    configure(client)
    for _ in range(120):
        assert client.get('/review-api/me', headers=ADMIN).status_code == 200
    assert client.get('/review-api/me', headers=ADMIN).status_code == 429
    assert route_limit('/review-api/live-pairs') == 15
    assert route_limit('/review-api/report-templates/revise') == 15
    assert route_limit('/review-api/report-templates/render') == 15
    assert route_limit('/ai/exploit') == 20


def test_bearer_and_put_cors_preserve_origin_and_auth_checks(client):
    configure(client)
    headers = {'Origin': 'http://localhost:5173',
               'Access-Control-Request-Method': 'PUT',
               'Access-Control-Request-Headers': 'authorization,content-type'}
    response = client.options('/review-api/assignments/example/judgment', headers=headers)
    assert response.status_code == 200
    assert 'PUT' in response.headers['access-control-allow-methods']
    assert 'Authorization' in response.headers['access-control-allow-headers']
    assert client.options('/review-api/me', headers={**headers, 'Origin': 'https://untrusted.example'}).status_code == 400
    assert client.get('/review-api/me', headers={'Origin': headers['Origin']}).status_code == 401
