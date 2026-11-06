"""T4 security and edge-case QA for the A/B review feature (backend).

Covers: stored-XSS payload round trip (A), authorization depth (B), input hardening (C),
concurrency and integrity (D) and information leakage (E).

Everything runs against temporary SQLite databases through TestClient. The training gateway
is an in-process httpx.MockTransport. No Vast/GPU, no canary and no network.
"""
import hashlib
import json
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secai_gateway.api.middleware import configure_middleware
from secai_gateway.api.review_auth import load_credentials
from secai_gateway.api.reviews import router
from secai_gateway.state.ratelimit import FixedWindowRateLimiter
from secai_gateway.state.review_store import ReviewStore

from test_review_live import FakeRunner
from test_review_ab_flow_matrix import Training, World, approved_world

P = '/review-api'
TRAIN = P + '/reward/training'
TOKENS = {'admin-token': ('admin', 'admin'), 'admin2-token': ('admin2', 'admin'),
          'review-token': ('expert', 'reviewer'), 'other-token': ('other', 'reviewer')}
ADMIN = {'Authorization': 'Bearer admin-token'}
ADMIN2 = {'Authorization': 'Bearer admin2-token'}
REVIEW = {'Authorization': 'Bearer review-token'}
OTHER = {'Authorization': 'Bearer other-token'}

# Strings that must never appear in any HTTP response body (E).
LEAKS = ['Traceback', 'File "', '/home/', '/tmp/', '.db', 'sqlite3', 'OperationalError', 'IntegrityError',
         'secai_gateway', 'admin-token', 'admin2-token', 'review-token', 'other-token', 'private-training']


def make_app(tmp_path, middleware=False):
    file = tmp_path / 'credentials.json'
    file.write_text(json.dumps({'credentials': [
        {'token_sha256': hashlib.sha256(t.encode()).hexdigest(), 'actor_id': a, 'role': r}
        for t, (a, r) in TOKENS.items()]}))
    app = FastAPI()
    settings = SimpleNamespace(review_enabled=True, review_live_sample_rate=1, review_live_cooldown_seconds=0,
                               review_live_timeout_seconds=2, cors_origins=['http://localhost:5173'])
    state = SimpleNamespace(settings=settings, review_credentials=load_credentials(file),
                            review_store=ReviewStore(tmp_path / 'reviews.db'), rate_limiter=FixedWindowRateLimiter())
    state.runner = FakeRunner('Alternative answer')
    app.state.gateway = state
    app.include_router(router)
    if middleware:
        configure_middleware(app, state)
    return app


@pytest.fixture
def client(tmp_path):
    return TestClient(make_app(tmp_path), raise_server_exceptions=False)


@pytest.fixture
def mw_client(tmp_path):
    return TestClient(make_app(tmp_path, middleware=True), raise_server_exceptions=False)


def clean(response, allow_5xx=False):
    """Response must not be a 5xx and must not leak server internals."""
    assert allow_5xx or response.status_code < 500, f'{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:200]}'
    body = response.text
    for needle in LEAKS:
        assert needle not in body, f'leak {needle!r} in {response.status_code} {body[:200]}'
    return response


def live(client, headers=REVIEW, **override):
    payload = {'prompt_messages': [{'role': 'user', 'content': 'Question'}], 'answer': 'Original answer', 'source_group': 'g'}
    payload.update(override)
    return client.post(f'{P}/live-pairs', headers=headers, json=payload)


# =============================================================================== A
XSS = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '[click](javascript:alert(1))',
    '<a href="javascript:alert(1)">x</a>',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<svg onload=alert(1)>',
    '<details open ontoggle=alert(1)>',
    '# Heading\n<b>raw html</b> **bold**',
    '```html\n<script>alert(1)</script>\n```',
    'rtl ‮evil‬ zero​width ⁦iso⁩ ctrl \u0007\u001b[31m bell',
    'x' * 60000,
]


def test_a_xss_payloads_round_trip_verbatim_and_inert_json(client):
    """The API stores payloads as data (no HTML interpretation) and serves them as JSON only."""
    world = World(client, title='QA xss')
    for i, text in enumerate(XSS):
        lines = json.dumps({'external_id': f'x{i}', 'source_group': f'g{i}',
                            'prompt_messages': [{'role': 'user', 'content': text}],
                            'candidates': [{'id': 'a', 'text': text + ' A'}, {'id': 'b', 'text': text + ' B'}]})
        r = client.post(f'{P}/campaigns/{world.cid}/imports', headers=ADMIN, content=lines.encode())
        assert r.status_code == 200, (text[:30], r.text)
    page = client.get(f'{P}/campaigns/{world.cid}/items?limit=100', headers=ADMIN).json()
    assert page['total'] == len(XSS)
    for row in page['items']:
        aid = client.post(f'{P}/items/{row["id"]}/assignments', headers=ADMIN, json={'reviewer_ids': ['expert']}).json()['items'][0]['id']
        d = client.get(f'{P}/assignments/{aid}', headers=REVIEW)
        assert d.status_code == 200
        assert d.headers['content-type'].startswith('application/json')
        # nosniff-style safety: JSON never has an HTML content type and carries the raw text.
        assert d.json()['prompt_messages'][0]['content'] in XSS
        o = client.get(f'{P}/overview/{row["id"]}', headers=ADMIN)
        assert o.headers['content-type'].startswith('application/json')
    # Error bodies for payload-shaped paths do not reflect user input as HTML either.
    for path in ['/review-api/overview/<script>alert(1)</script>', '/review-api/assignments/"><img src=x onerror=1>']:
        r = client.get(path, headers=ADMIN)
        assert r.headers['content-type'].startswith('application/json')
        assert '<script>' not in r.text and '<img' not in r.text  # 404 bodies do not reflect the path


def test_a_live_pairs_stores_xss_without_interpretation_and_survives_unicode(client):
    for text in XSS[:-1]:
        r = live(client, prompt_messages=[{'role': 'user', 'content': text}], answer='Original ' + text)
        clean(r)
        assert r.status_code == 200, text[:40]
        assert r.headers['content-type'].startswith('application/json')


# =============================================================================== B
BAD_AUTH = [
    ('empty', ''), ('scheme only', 'Bearer'), ('scheme+space', 'Bearer '), ('basic', 'Basic admin-token'),
    ('raw token', 'admin-token'), ('extra part', 'Bearer admin-token extra'), ('truncated', 'Bearer admin-toke'),
    ('prefixed', 'Bearer xadmin-token'), ('upper token', 'Bearer ADMIN-TOKEN'), ('comma list', 'Bearer a, Bearer admin-token'),
    ('Token scheme', 'Token admin-token'), ('hash itself', 'Bearer ' + hashlib.sha256(b'admin-token').hexdigest()),
    ('sha256 prefix', 'sha256:' + hashlib.sha256(b'admin-token').hexdigest()), ('huge', 'Bearer ' + 'A' * 200000),
    ('latin1', 'Bearer caf\xe9'), ('null-ish', 'Bearer null'), ('devspace fake', 'Bearer devspace:admin'),
]


@pytest.mark.parametrize('name,value', BAD_AUTH, ids=[n for n, _ in BAD_AUTH])
def test_b_malformed_bearer_is_401_never_5xx(client, name, value):
    for path in ['/review-api/me', '/review-api/overview', '/review-api/live-settings', '/review-api/reward/training/batches']:
        r = clean(client.get(path, headers=[(b'Authorization', value.encode('latin-1'))]))
        assert r.status_code == 401, (path, r.status_code)
        assert r.headers.get('www-authenticate') == 'Bearer'


def test_b_lowercase_scheme_and_extra_spaces_only_work_with_the_right_token(client):
    assert client.get(f'{P}/me', headers={'Authorization': 'bearer review-token'}).status_code == 200
    assert client.get(f'{P}/me', headers={'Authorization': 'Bearer   review-token'}).status_code == 200
    assert client.get(f'{P}/me', headers={'Authorization': 'bearer wrong'}).status_code == 401


def test_b_multiple_authorization_headers_never_escalate(client):
    """Two Authorization headers: the first one wins; presenting a reviewer token first never
    yields admin even if an admin-looking value follows, and garbage first never falls through."""
    def raw(first, second, path):
        return client.get(path, headers=[('Authorization', first), ('Authorization', second)])
    assert raw('Bearer review-token', 'Bearer admin-token', f'{P}/overview').status_code == 403
    assert raw('Bearer wrong', 'Bearer admin-token', f'{P}/overview').status_code == 401
    assert raw('Bearer review-token', 'Bearer admin-token', f'{P}/me').json()['role'] == 'reviewer'


@pytest.mark.parametrize('header', [{'X-Review-Role': 'admin'}, {'x-review-role': 'ADMIN'}, {'X-Review-Actor': 'admin'},
                                    {'X-Forwarded-User': 'admin'}, {'X-Review-Role': 'admin', 'X-Forwarded-For': '127.0.0.1'}])
def test_b_role_headers_have_no_effect_directly_on_gateway(client, header):
    for path in ['/review-api/overview', '/review-api/live-settings', '/review-api/approvals/preview', '/review-api/reward/training/eligibility']:
        assert client.get(path, headers=header).status_code == 401
        assert client.get(path, headers={**header, **REVIEW}).status_code == 403
        assert client.get(path, headers={**header, 'Authorization': 'Bearer devspace:admin'}).status_code == 401
    assert client.get(f'{P}/me', headers={**header, **REVIEW}).json() == {'actor_id': 'expert', 'role': 'reviewer'}


def test_b_reviewer_cannot_reach_admin_routes_for_any_real_campaign_or_item(client):
    w = World(client)
    iid = w.add_items(1)[0]
    w.vote(iid, 'expert', 'a')
    admin_calls = [
        ('GET', f'/campaigns/{w.cid}/items'), ('GET', f'/campaigns/{w.cid}/stats'), ('GET', f'/campaigns/{w.cid}/export'),
        ('GET', f'/items/{iid}/judgments'), ('GET', f'/overview/{iid}'), ('GET', '/overview'), ('GET', '/reviewers'),
        ('GET', '/approvals/preview'), ('GET', '/live-settings'), ('GET', '/reward/training/eligibility'),
    ]
    for method, path in admin_calls:
        for who in (REVIEW, OTHER):
            assert client.request(method, P + path, headers=who).status_code == 403, path
    writes = [('PUT', f'/items/{iid}/adjudication', {'outcome': 'skip', 'reason': 'x'}), ('PATCH', f'/campaigns/{w.cid}', {'state': 'closed'}),
              ('POST', f'/items/{iid}/assignments', {'reviewer_ids': ['other']}), ('PUT', '/live-settings', {'sample_percent': 0}),
              ('POST', '/approvals/bulk', {'snapshot_version': '0' * 64}), ('POST', '/campaigns', {'title': 'pwn'}),
              ('POST', '/reward/training/batches', {'request_key': 'k', 'snapshot_version': '0' * 64})]
    for method, path, body in writes:
        assert client.request(method, P + path, headers=REVIEW, json=body).status_code == 403, path
    assert client.delete(f'{P}/assignments/{w.assignments[(iid, "expert")]["id"]}', headers=REVIEW).status_code == 403
    # Nothing changed.
    assert w.judgments(iid)['adjudication'] is None
    assert client.get(f'{P}/live-settings', headers=ADMIN).json()['sample_percent'] == 100
    assert client.get(f'{P}/campaigns', headers=ADMIN).json()['total'] == 1


def test_b_idor_between_reviewers_on_assignments_and_lists(client):
    w = World(client)
    i1, i2 = w.add_items(2)
    a_exp = w.assign(i1, 'expert')['id']
    a_oth = w.assign(i2, 'other')['id']
    for method, path, body in [('GET', f'/assignments/{a_oth}', None),
                               ('PUT', f'/assignments/{a_oth}/judgment', {'decision': 'left'}),
                               ('DELETE', f'/assignments/{a_oth}', None)]:
        r = client.request(method, P + path, headers=REVIEW, **({'json': body} if body else {}))
        assert r.status_code in {403, 404}, (method, r.status_code)
        assert 'other' not in r.text and a_oth not in r.text
    assert client.get(f'{P}/assignments/{a_exp}', headers=OTHER).status_code == 404
    mine = client.get(f'{P}/assignments', headers=REVIEW).json()
    assert [r['id'] for r in mine['items']] == [a_exp] and all(r['reviewer_id'] == 'expert' for r in mine['items'])
    # Another reviewer's campaign filter / status does not widen the result.
    assert client.get(f'{P}/assignments?campaign_id={w.cid}&status=pending', headers=OTHER).json()['total'] == 1
    assert client.get(f'{P}/assignments?campaign_id=nope', headers=OTHER).json()['total'] == 0
    # Admin cannot read or vote on a reviewer's assignment either.
    assert client.get(f'{P}/assignments/{a_exp}', headers=ADMIN).status_code == 404
    assert client.put(f'{P}/assignments/{a_exp}/judgment', headers=ADMIN, json={'decision': 'left'}).status_code == 404
    assert w.judgments(i1)['judgments'] == [] and w.judgments(i2)['judgments'] == []
    # A reviewer sees only campaigns they are assigned to.
    w2 = World(client, title='Another campaign')
    w2.add_items(1)
    assert [c['id'] for c in client.get(f'{P}/campaigns', headers=REVIEW).json()['items']] == [w.cid]
    assert client.get(f'{P}/campaigns', headers=ADMIN).json()['total'] == 2


def test_b_user_responses_do_not_expose_ids_provenance_model_or_other_votes(client):
    state = client.app.state.gateway
    r = live(client).json()
    assert r['offered']
    a = r['assignment']
    allowed = {'id', 'item_id', 'campaign_id', 'campaign_title', 'external_id', 'prompt_messages', 'evidence', 'left', 'right',
               'rubric_version', 'rubric', 'judgment', 'status'}
    assert set(a) == allowed, set(a) ^ allowed
    assert set(a['left']) == {'text'} and set(a['right']) == {'text'}
    blob = json.dumps(a)
    for needle in ['provenance', 'candidate', 'original_chat_final', 'review_generation', 'model', 'content_sha256', 'reviewer_id',
                   'winning', 'review-live-system', 'profile']:
        assert needle not in blob, needle
    # Another reviewer already voted on the same item through the admin route: still invisible.
    iid = a['item_id']
    client.post(f'{P}/items/{iid}/assignments', headers=ADMIN, json={'reviewer_ids': ['other']})
    other_aid = client.get(f'{P}/assignments', headers=OTHER).json()['items'][0]['id']
    assert client.put(f'{P}/assignments/{other_aid}/judgment', headers=OTHER, json={'decision': 'left', 'comment': 'SECRET-OTHER-COMMENT'}).status_code == 200
    mine = client.get(f'{P}/assignments/{a["id"]}', headers=REVIEW)
    assert 'SECRET-OTHER-COMMENT' not in mine.text and 'other' not in json.dumps(mine.json().get('judgment') or {})
    # Submit response: no candidate ids / winner, only the reviewer's own fields.
    sub = client.put(f'{P}/assignments/{a["id"]}/judgment', headers=REVIEW, json={'decision': 'left', 'error_tags': {'right': ['incomplete']}})
    assert sub.status_code == 200
    assert set(sub.json()) == {'id', 'assignment_id', 'decision', 'error_tags', 'comment', 'submitted_at'}, sub.json()
    assert 'original' not in sub.text and 'generated' not in sub.text
    detail = client.get(f'{P}/assignments/{a["id"]}', headers=REVIEW).json()
    assert set(detail['judgment']) == {'id', 'assignment_id', 'decision', 'error_tags', 'comment', 'submitted_at'}
    listing = client.get(f'{P}/assignments', headers=REVIEW).json()['items'][0]
    assert set(listing) == {'id', 'item_id', 'reviewer_id', 'created_at', 'campaign_id', 'campaign_title', 'external_id', 'status'}
    assert state.runner.calls  # sanity: B was generated server-side


def test_b_admin_cannot_modify_user_votes_through_any_route(client):
    w = World(client)
    iid = w.add_items(1)[0]
    assert w.vote(iid, 'expert', 'a').status_code == 200
    before = w.judgments(iid)['judgments']
    aid = w.assignments[(iid, 'expert')]['id']
    assert client.delete(f'{P}/assignments/{aid}', headers=ADMIN).status_code == 409
    assert client.put(f'{P}/assignments/{aid}/judgment', headers=ADMIN, json={'decision': 'right'}).status_code == 404
    assert w.vote(iid, 'expert', 'b').status_code == 409  # the opposite choice
    assert client.put(f'{P}/assignments/{aid}/judgment', headers=REVIEW, json={'decision': 'skip'}).status_code == 409
    # Even after an adjudication or closing the campaign nothing rewrites the vote.
    assert w.adjudicate(iid, winner='b').status_code == 200
    assert client.patch(f'{P}/campaigns/{w.cid}', headers=ADMIN, json={'state': 'closed'}).status_code == 200
    assert w.vote(iid, 'expert', 'b').status_code == 409
    assert w.judgments(iid)['judgments'] == before
    assert client.post(f'{P}/items/{iid}/assignments', headers=ADMIN, json={'reviewer_ids': ['other']}).status_code == 409


ODD_IDS = ['..', '../../etc/passwd', '..%2f..%2fetc%2fpasswd', '%2e%2e%2f', "' OR 1=1--", '"; DROP TABLE items;--', '%00', 'a%00b',
           '‮', 'x' * 5000, '%', '%25', '*', '{iid}', '${jndi:ldap://x}', '..\\..\\windows', 'null', '0', '-1', 'ÿ']


@pytest.mark.parametrize('odd', ODD_IDS, ids=[repr(o)[:24] for o in ODD_IDS])
def test_b_odd_identifiers_are_clean_404_not_5xx(client, odd):
    w = World(client)
    w.add_items(1)
    for method, path in [('GET', '/overview/{}'), ('GET', '/items/{}/judgments'), ('GET', '/assignments/{}'),
                         ('GET', '/campaigns/{}/items'), ('GET', '/campaigns/{}/stats'), ('GET', '/campaigns/{}/export'),
                         ('GET', '/reward/training/batches/{}'), ('GET', '/reward/training/batches/{}/items'),
                         ('GET', '/reward/training/runs/{}'), ('GET', '/reward/training/runs/{}/events'), ('DELETE', '/assignments/{}')]:
        r = clean(client.request(method, P + path.format(odd), headers=ADMIN))
        assert r.status_code in {404, 405, 307, 308, 422, 400}, (path, odd, r.status_code)
    for body_path in ['/items/{}/adjudication', '/assignments/{}/judgment']:
        clean(client.put(P + body_path.format(odd), headers=ADMIN, json={'outcome': 'skip', 'reason': 'x', 'decision': 'left'}))
    clean(client.post(P + '/items/{}/assignments'.format(odd), headers=ADMIN, json={'reviewer_ids': ['expert']}))
    clean(client.patch(P + '/campaigns/{}'.format(odd), headers=ADMIN, json={'state': 'closed'}))
    clean(client.post(P + '/campaigns/{}/imports'.format(odd), headers=ADMIN, content=b'{}'))


@pytest.mark.parametrize('method,path', [('DELETE', '/overview'), ('POST', '/overview/x'), ('PUT', '/me'), ('POST', '/me'), ('GET', '/live-pairs'),
                                         ('DELETE', '/live-settings'), ('POST', '/live-settings'), ('GET', '/approvals/bulk'),
                                         ('PUT', '/reward/training/batches'), ('DELETE', '/reward/training/batches/x'), ('GET', '/reward/training/batches/x/retry'),
                                         ('TRACE', '/me'), ('PATCH', '/items/x/adjudication'), ('POST', '/assignments/x/judgment')])
def test_b_wrong_method_is_405_without_effect(client, method, path):
    r = clean(client.request(method, P + path, headers=ADMIN, json={} if method in {'POST', 'PUT', 'PATCH'} else None))
    assert r.status_code in {404, 405}, r.status_code


def test_b_disabled_review_hides_every_route_even_with_valid_admin(client):
    client.app.state.gateway.settings.review_enabled = False
    for path in ['/me', '/overview', '/live-settings', '/reward/training/eligibility', '/assignments']:
        assert client.get(P + path, headers=ADMIN).status_code == 404
    assert live(client, headers=ADMIN).status_code == 404


# =============================================================================== C
def raw(client, method, path, content, headers=ADMIN, ctype='application/json'):
    return client.request(method, P + path, headers={**headers, 'Content-Type': ctype}, content=content)


LIVE_BODIES = [
    b'', b'null', b'[]', b'"x"', b'{', b'{"answer":', b'\xff\xfe\x00', b'{"prompt_messages":[],"answer":"a"}',
    b'{"answer":"a"}', b'{"prompt_messages":[{"role":"user","content":"q"}]}',
    b'{"prompt_messages":"q","answer":"a"}', b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":5}',
    b'{"prompt_messages":[{"role":"system","content":"q"}],"answer":"a"}',
    b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":"a","extra":1}',
    b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":"a","source_group":""}',
    ('{"prompt_messages":[{"role":"user","content":"q"}],"answer":"a","source_group":"%s"}' % ('s' * 201)).encode(),
    b'{"prompt_messages":[{"role":"user","content":"   "}],"answer":"a"}',
    b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":"   "}',
    b'{"prompt_messages":[{"role":"user","content":"q"},{"role":"assistant","content":"a"}],"answer":"a"}',
    b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":"\\ud800"}',
    b'{"prompt_messages":[{"role":"user","content":"q","role":"assistant"}],"answer":"a"}',
    b'{"prompt_messages":' + b'[' * 5000 + b'],"answer":"a"}',
    b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":NaN}',
]


@pytest.mark.parametrize('body', LIVE_BODIES, ids=[f'live{i}' for i in range(len(LIVE_BODIES))])
def test_c_live_pairs_malformed_bodies_are_422_and_do_not_spend_model(client, body):
    r = clean(raw(client, 'POST', '/live-pairs', body, headers=REVIEW))
    assert r.status_code == 422, (r.status_code, r.text[:200])
    assert client.app.state.gateway.runner.calls == []


def test_c_live_pairs_size_limits(client):
    big = json.dumps({'prompt_messages': [{'role': 'user', 'content': 'q'}], 'answer': 'a' * 300000}).encode()
    assert clean(raw(client, 'POST', '/live-pairs', big, headers=REVIEW)).status_code == 413
    over = json.dumps({'prompt_messages': [{'role': 'user', 'content': 'q'}], 'answer': 'a' * 65537}).encode()
    assert clean(raw(client, 'POST', '/live-pairs', over, headers=REVIEW)).status_code == 422
    # Multi-byte text: 65536 characters but > 64 KiB UTF-8.
    wide = json.dumps({'prompt_messages': [{'role': 'user', 'content': 'q'}], 'answer': 'é' * 40000}, ensure_ascii=False).encode()
    assert clean(raw(client, 'POST', '/live-pairs', wide, headers=REVIEW)).status_code == 422
    ok = json.dumps({'prompt_messages': [{'role': 'user', 'content': 'q'}], 'answer': 'a' * 65536}).encode()
    assert clean(raw(client, 'POST', '/live-pairs', ok, headers=REVIEW)).status_code == 200
    thirty_one = json.dumps({'prompt_messages': [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'm'} for i in range(31)], 'answer': 'a'}).encode()
    assert clean(raw(client, 'POST', '/live-pairs', thirty_one, headers=REVIEW)).status_code == 422
    # Chunked transfer without Content-Length is bounded by the streamed byte counter.
    def chunks():
        for _ in range(40):
            yield b'x' * 8192
    assert clean(client.post(f'{P}/live-pairs', headers=REVIEW, content=chunks())).status_code == 413


def test_c_live_pairs_control_characters_null_byte_and_unicode_are_stored_safely(client):
    state = client.app.state.gateway
    state.runner = FakeRunner('Alt \u0000 nul ‮ rtl ​ zw')
    r = clean(live(client, prompt_messages=[{'role': 'user', 'content': 'q \u0000 ‮ ​ \U0001f600 \x1b[0m'}], answer='A \u0000 ‮'))
    assert r.status_code == 200 and r.json()['offered'] is True
    a = r.json()['assignment']
    assert any('\u0000' in side['text'] for side in (a['left'], a['right']))
    # Round trip through the admin view and export.
    iid = a['item_id']
    assert clean(client.get(f'{P}/overview/{iid}', headers=ADMIN)).status_code == 200
    assert clean(client.get(f'{P}/campaigns/{a["campaign_id"]}/export', headers=ADMIN)).status_code == 200


JUDGMENT_BODIES = [
    (b'', 422), (b'null', 422), (b'[]', 422), (b'{', 422), (b'{}', 422), (b'{"decision":null}', 422), (b'{"decision":1}', 422),
    (b'{"decision":"LEFT"}', 422), (b'{"decision":"win"}', 422), (b'{"decision":"left","x":1}', 422),
    (b'{"decision":"left","comment":null}', 422), (b'{"decision":"left","comment":5}', 422),
    (('{"decision":"left","comment":"%s"}' % ('c' * 4001)).encode(), 422),
    (b'{"decision":"left","error_tags":{"left":["bogus"]}}', 422), (b'{"decision":"left","error_tags":{"middle":[]}}', 422),
    (b'{"decision":"left","error_tags":{"left":"other"}}', 422), (b'{"decision":"left","error_tags":[]}', 422),
    (b'{"decision":"left","error_tags":{"left":["incomplete","incomplete","incomplete","incomplete","incomplete","incomplete","incomplete"]}}', 422),
    (b'{"decision":"left","error_tags":{"left":["other"]}}', 422),
    (b'{"decision":"both_bad"}', 422), (b'{"decision":"both_bad","comment":"   "}', 422),
    (b'{"decision":"insufficient_context","comment":""}', 422),
]


@pytest.mark.parametrize('body,expected', JUDGMENT_BODIES, ids=[f'j{i}' for i in range(len(JUDGMENT_BODIES))])
def test_c_judgment_bad_bodies(client, body, expected):
    w = World(client)
    iid = w.add_items(1)[0]
    aid = w.assign(iid, 'expert')['id']
    r = clean(raw(client, 'PUT', f'/assignments/{aid}/judgment', body, headers=REVIEW))
    assert r.status_code == expected, (r.status_code, r.text[:200])
    assert w.judgments(iid)['judgments'] == []


def test_c_judgment_edge_values_accepted(client):
    w = World(client)
    ids = w.add_items(5)
    aids = [w.assign(i, 'expert')['id'] for i in ids]
    comment = 'x' * 4000
    ok = [{'decision': 'left', 'comment': comment}, {'decision': 'skip'}, {'decision': 'tie', 'comment': 'rtl ‮ zero​ nul \u0000'},
          {'decision': 'right', 'error_tags': {'left': ['other', 'other', 'incomplete'], 'right': []}, 'comment': 'why'},
          {'decision': 'insufficient_context', 'comment': '<script>alert(1)</script>'}]
    for aid, body in zip(aids, ok):
        r = clean(client.put(f'{P}/assignments/{aid}/judgment', headers=REVIEW, json=body))
        assert r.status_code == 200, (body, r.text)
    # Dedupe + sort of tags is deterministic.
    r = client.get(f'{P}/assignments/{aids[3]}', headers=REVIEW).json()['judgment']
    assert r['error_tags'] == {'left': ['incomplete', 'other'], 'right': []}


def test_c_judgment_comment_is_not_secret_redacted_but_is_bounded(client):
    """Documented behaviour: user comments are stored as typed (only live-pair text is redacted)."""
    w = World(client)
    iid = w.add_items(1)[0]
    aid = w.assign(iid, 'expert')['id']
    secret = 'sk-proj-' + 'A' * 30
    r = client.put(f'{P}/assignments/{aid}/judgment', headers=REVIEW, json={'decision': 'left', 'comment': secret})
    assert r.status_code == 200
    stored = w.judgments(iid)['judgments'][0]['comment']
    assert stored == secret  # recorded finding T4-M1 (comment is not redacted)


ADJ_BODIES = [
    (b'', 422), (b'null', 422), (b'{}', 422), (b'{"outcome":"winner","reason":"x"}', 422), (b'{"outcome":"winner","winning_candidate_id":"","reason":"x"}', 422),
    (b'{"outcome":"win","reason":"x"}', 422), (b'{"outcome":"tie","winning_candidate_id":"a","reason":"x"}', 422),
    (b'{"outcome":"tie","reason":""}', 422), (b'{"outcome":"tie","reason":"   "}', 422), (('{"outcome":"tie","reason":"%s"}' % ('r' * 4001)).encode(), 422),
    (b'{"outcome":"winner","winning_candidate_id":"zzz","reason":"x"}', 422), (b'{"outcome":"winner","winning_candidate_id":5,"reason":"x"}', 422),
    (('{"outcome":"winner","winning_candidate_id":"%s","reason":"x"}' % ('i' * 201)).encode(), 422),
    (b'{"outcome":"winner","winning_candidate_id":"a","reason":"x","extra":1}', 422),
]


@pytest.mark.parametrize('body,expected', ADJ_BODIES, ids=[f'a{i}' for i in range(len(ADJ_BODIES))])
def test_c_adjudication_bad_bodies(client, body, expected):
    w = World(client)
    iid = w.add_items(1)[0]
    assert w.vote(iid, 'expert', 'a').status_code == 200
    r = clean(raw(client, 'PUT', f'/items/{iid}/adjudication', body))
    assert r.status_code == expected, (r.status_code, r.text[:200])
    assert w.judgments(iid)['adjudication'] is None


def test_c_adjudication_strips_labels_and_nul_reason(client):
    w = World(client)
    iid = w.add_items(1)[0]
    w.vote(iid, 'expert', 'a')
    r = clean(client.put(f'{P}/items/{iid}/adjudication', headers=ADMIN, json={'outcome': 'winner', 'winning_candidate_id': ' a ', 'reason': ' ok \u0000 ‮ '}))
    assert r.status_code == 200 and r.json()['winning_candidate_id'] == 'a'


BULK = [
    (b'', 422), (b'null', 422), (b'[]', 422), (b'{}', 422), (b'{"snapshot_version":"' + b'0' * 63 + b'"}', 422),
    (b'{"snapshot_version":"' + b'0' * 65 + b'"}', 422), (b'{"snapshot_version":"' + b'A' * 64 + b'"}', 422),
    (b'{"snapshot_version":"' + b'0' * 64 + b'\\n"}', 422), (b'{"snapshot_version":"' + b'0' * 64 + b'","source":"x"}', 422),
    (b'{"snapshot_version":"' + b'0' * 64 + b'","extra":1}', 422), (b'{"snapshot_version":5}', 422),
    (('{"snapshot_version":"%s","search":"%s"}' % ('0' * 64, 's' * 201)).encode(), 422),
    (('{"snapshot_version":"%s","search":"%s"}' % ('0' * 64, 's' * 5000)).encode(), 413),
    (b'{"snapshot_version":"' + b'0' * 64 + b'"}', 409),
    (('{"snapshot_version":"%s","search":"\\u0000 \' OR 1=1 --"}' % ('0' * 64)).encode(), 409),
]


@pytest.mark.parametrize('body,expected', BULK, ids=[f'b{i}' for i in range(len(BULK))])
def test_c_bulk_bad_bodies(client, body, expected):
    w = World(client)
    iid = w.add_items(1)[0]
    w.vote(iid, 'expert', 'a')
    r = clean(raw(client, 'POST', '/approvals/bulk', body))
    assert r.status_code == expected, (r.status_code, r.text[:200])
    assert w.overview('approved')['total'] == 0


SETTINGS = [
    (b'', 422), (b'null', 422), (b'[]', 422), (b'{}', 422), (b'{"sample_percent":-1}', 422), (b'{"sample_percent":101}', 422),
    (b'{"sample_percent":100.5}', 422), (b'{"sample_percent":10.0}', 422), (b'{"sample_percent":"50"}', 422), (b'{"sample_percent":NaN}', 422),
    (b'{"sample_percent":Infinity}', 422), (b'{"sample_percent":true}', 422), (b'{"sample_percent":null}', 422),
    (b'{"sample_percent":1e2}', 422), (b'{"sample_percent":' + b'9' * 40 + b'}', 422), (b'{"sample_percent":10,"x":1}', 422),
    (b'{"sample_percent":10,"sample_percent":20}', 200), (b'{"sample_percent":0}', 200), (b'{"sample_percent":100}', 200),
    (b'{"sample_percent":' + b' ' * 2000 + b'5}', 413),
]


@pytest.mark.parametrize('body,expected', SETTINGS, ids=[f's{i}' for i in range(len(SETTINGS))])
def test_c_live_settings_values(client, body, expected):
    r = clean(raw(client, 'PUT', '/live-settings', body))
    assert r.status_code == expected, (r.status_code, r.text[:200])
    if expected != 200:
        assert client.get(f'{P}/live-settings', headers=ADMIN).json()['sample_percent'] == 100


@pytest.mark.parametrize('key', ['', 'a' * 101, '../../x', 'a b', 'a\n', 'abc\n', 'ключ', 'a\u0000b', "k'; DROP TABLE x;--", 'k/1', 'k.1', '‮', ' '])
def test_c_batch_request_key_validation(client, tmp_path, key):
    Training(client, tmp_path)
    r = clean(client.post(f'{TRAIN}/batches', headers=ADMIN, json={'request_key': key, 'purpose': 'demo', 'snapshot_version': '0' * 64}))
    assert r.status_code == 422, (key, r.status_code)
    r = clean(client.post(f'{TRAIN}/batches/x/retry', headers=ADMIN, json={'request_key': key}))
    assert r.status_code == 422


@pytest.mark.parametrize('key', ['a', 'A_b-9', 'k' * 100])
def test_c_batch_request_key_accepts_documented_shapes(client, tmp_path, key):
    Training(client, tmp_path, minimum=1000)
    r = clean(client.post(f'{TRAIN}/batches', headers=ADMIN, json={'request_key': key, 'purpose': 'demo', 'snapshot_version': '0' * 64}))
    assert r.status_code == 409  # shape accepted; stale/ineligible


@pytest.mark.parametrize('body', [b'', b'{', b'null', b'[]', b'{"request_key":"k"}', b'{"request_key":"k","snapshot_version":"%s","purpose":"x"}' % (b'0' * 64),
                                  b'{"request_key":"k","snapshot_version":"%s","purpose":"demo","extra":1}' % (b'0' * 64), b'x' * 5000])
def test_c_batch_create_bad_bodies(client, tmp_path, body):
    Training(client, tmp_path)
    r = clean(raw(client, 'POST', '/reward/training/batches', body))
    assert r.status_code in {413, 422}


PAGINATED = ['/overview', '/reviewers', '/campaigns', '/assignments', '/campaigns/{cid}/items',
             '/reward/training/batches', '/reward/training/batches/{bid}/items']
BAD_NUMS = ['-1', '0', '101', '1.5', 'abc', '', 'NaN', 'Infinity', '1e2', '-' + '9' * 30, '%00', "1'--", '٣']


@pytest.mark.parametrize('param', ['offset', 'limit'])
@pytest.mark.parametrize('value', BAD_NUMS)
def test_c_pagination_bad_numbers_never_5xx(client, tmp_path, param, value):
    Training(client, tmp_path)
    w = World(client)
    w.add_items(1)
    for template in PAGINATED:
        path = template.format(cid=w.cid, bid='x')
        r = clean(client.get(f'{P}{path}', headers=ADMIN, params={param: value}))
        assert r.status_code in {200, 404, 422}, (path, param, value, r.status_code)


@pytest.mark.parametrize('value', ['-1', 'abc', '', '1.5', '9' * 30, '%00', '../x', 'x&limit=1000'])
def test_c_events_cursor_never_5xx(client, tmp_path, value):
    Training(client, tmp_path)
    for param in ('after_seq', 'limit'):
        r = clean(client.get(f'{TRAIN}/runs/nope/events', headers=ADMIN, params={param: value}))
        assert r.status_code in {404, 422}


def test_c_overview_filters_reject_unknown_values_and_long_search(client):
    for params in [{'status': 'x'}, {'source': 'x'}, {'search': 's' * 201}, {'status': 'approved ', 'source': 'all'}]:
        assert clean(client.get(f'{P}/overview', headers=ADMIN, params=params)).status_code == 422
    for q in ['\u0000', "%' OR '1'='1", '_', '%', '\\', '"', '‮', 'a' * 200]:
        assert clean(client.get(f'{P}/overview', headers=ADMIN, params={'search': q})).status_code == 200
        assert clean(client.get(f'{P}/approvals/preview', headers=ADMIN, params={'search': q})).status_code == 200


IMPORT_BODIES = {
    'empty': b'', 'blank lines': b'\n\n\n', 'not json': b'not json\n', 'bom': b'\xef\xbb\xbf{"external_id":"a"}\n',
    'bad utf8': b'\xff\xfe{"a":1}\n', 'null byte raw': b'{"external_id":"a\x00b"}\n', 'array': b'[1,2,3]\n', 'deep': b'{"x":' + b'[' * 4000 + b'}\n',
    'dup external': b'{"external_id":"a","prompt_messages":[{"role":"user","content":"q"}],"candidates":[{"id":"x","text":"1"},{"id":"x","text":"2"}]}\n',
    'same text': b'{"external_id":"a","prompt_messages":[{"role":"user","content":"q"}],"candidates":[{"id":"x","text":"1"},{"id":"y","text":" 1 "}]}\n',
    'one candidate': b'{"external_id":"a","prompt_messages":[{"role":"user","content":"q"}],"candidates":[{"id":"x","text":"1"}]}\n',
    'long line': b'{"external_id":"' + b'a' * 1100000 + b'"}\n',
}


@pytest.mark.parametrize('name', list(IMPORT_BODIES))
def test_c_import_malformed_bodies(client, name):
    w = World(client)
    r = clean(raw(client, 'POST', f'/campaigns/{w.cid}/imports', IMPORT_BODIES[name], ctype='application/x-ndjson'))
    assert r.status_code in {413, 422}, (name, r.status_code, r.text[:150])
    # Validation errors never echo uploaded content.
    assert 'a' * 50 not in r.text
    assert client.get(f'{P}/campaigns/{w.cid}/items', headers=ADMIN).json()['total'] == 0


def test_c_import_limits_and_conflicts(client):
    w = World(client)
    def line(i, text='t'):
        return json.dumps({'external_id': f'e{i}', 'prompt_messages': [{'role': 'user', 'content': 'q'}],
                           'candidates': [{'id': 'a', 'text': f'{text}1'}, {'id': 'b', 'text': f'{text}2'}]})
    assert clean(raw(client, 'POST', f'/campaigns/{w.cid}/imports', '\n'.join(line(i) for i in range(501)).encode(), ctype='application/x-ndjson')).status_code == 413
    assert clean(raw(client, 'POST', f'/campaigns/{w.cid}/imports', ('\n'.join(line(i) for i in range(3))).encode(), ctype='application/x-ndjson')).status_code == 200
    # Same external_id, same content: skipped. Different content: 409 and nothing partially applied.
    r = clean(raw(client, 'POST', f'/campaigns/{w.cid}/imports', (line(1) + '\n' + line(9)).encode(), ctype='application/x-ndjson'))
    assert r.json() == {'imported': 1, 'skipped': 1}
    r = clean(raw(client, 'POST', f'/campaigns/{w.cid}/imports', (line(10) + '\n' + line(1, 'changed')).encode(), ctype='application/x-ndjson'))
    assert r.status_code == 409 and 'changed' not in r.text
    assert client.get(f'{P}/campaigns/{w.cid}/items', headers=ADMIN).json()['total'] == 4
    # Declared lengths: oversized and non-numeric Content-Length.
    huge = {**ADMIN, 'Content-Length': str(11 * 1024 * 1024)}
    assert clean(client.post(f'{P}/campaigns/{w.cid}/imports', headers=huge, content=b'x')).status_code in {413, 400, 422}


# =============================================================================== D
def race(n, fn):
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)
    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(run, range(n)))


def test_d_two_admins_adjudicate_same_item_one_wins_one_409(client):
    for round_ in range(6):
        w = World(client, title=f'QA race {round_}')
        iid = w.add_items(1)[0]
        assert w.vote(iid, 'expert', 'a').status_code == 200
        winners = ['a', 'b']
        results = race(2, lambda i: client.put(f'{P}/items/{iid}/adjudication', headers=[ADMIN, ADMIN2][i],
                                               json={'outcome': 'winner', 'winning_candidate_id': winners[i], 'reason': f'admin {i}'}))
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409], (round_, codes, [r.text for r in results])
        final = w.judgments(iid)['adjudication']
        assert final['winning_candidate_id'] == winners[[r.status_code for r in results].index(200)]
        # Exactly one adjudication row, original vote untouched.
        with client.app.state.gateway.review_store.connection() as db:
            assert db.execute('SELECT count(*) FROM adjudications WHERE item_id=?', (iid,)).fetchone()[0] == 1
        assert w.judgments(iid)['judgments'][0]['winning_candidate_id'] == 'a'


def test_d_identical_concurrent_adjudications_are_idempotent(client):
    w = World(client)
    iid = w.add_items(1)[0]
    w.vote(iid, 'expert', 'a')
    results = race(6, lambda i: client.put(f'{P}/items/{iid}/adjudication', headers=[ADMIN, ADMIN2][i % 2],
                                           json={'outcome': 'winner', 'winning_candidate_id': 'a', 'reason': 'same'}))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len({r.json()['id'] for r in results}) == 1


def test_d_bulk_approve_races_individual_approvals_without_duplicates_or_5xx(client):
    for round_ in range(3):
        w = World(client, title=f'QA bulk race {round_}')
        ids = w.add_items(12)
        for iid in ids:
            assert w.vote(iid, 'expert', 'a').status_code == 200
        version = w.preview()['snapshot_version']
        def act(i):
            if i == 0:
                return ('bulk', w.bulk(version))
            if i == 1:
                return ('bulk2', w.bulk(version))
            return ('single', client.put(f'{P}/items/{ids[i]}/adjudication', headers=ADMIN2,
                                         json={'outcome': 'winner', 'winning_candidate_id': 'a', 'reason': 'one'}))
        out = race(6, act)
        for kind, r in out:
            assert r.status_code in {200, 409}, (kind, r.status_code, r.text)
            assert r.status_code < 500
        assert sum(1 for k, r in out if k.startswith('bulk') and r.status_code == 200) <= 1
        with client.app.state.gateway.review_store.connection() as db:
            assert db.execute('SELECT count(*) FROM adjudications a JOIN items i ON i.id=a.item_id WHERE i.campaign_id=?', (w.cid,)).fetchone()[0] <= 12
            dup = db.execute('SELECT item_id,count(*) c FROM adjudications GROUP BY item_id HAVING c>1').fetchall()
            assert not dup
        for iid in ids:
            j = w.judgments(iid)
            assert j['judgments'][0]['winning_candidate_id'] == 'a'
            assert j['adjudication'] is None or j['adjudication']['winning_candidate_id'] == 'a'


def test_d_parallel_batches_with_different_keys_make_exactly_one_batch(client, tmp_path):
    t = Training(client, tmp_path)
    approved_world(client)
    version = t.eligibility()['snapshot_version']
    results = race(5, lambda i: t.freeze(f'key-{i}', version))
    codes = sorted(r.status_code for r in results)
    assert all(c in {202, 409, 503} for c in codes), codes
    assert codes.count(202) == 1, codes
    assert t.batches()['total'] == 1 and len(t.jobs.list()) == 1
    for r in results:
        clean(r)


def test_d_approval_during_batch_freeze_keeps_batch_snapshot_immutable(client, tmp_path):
    from secai_gateway.state.review_training_store import digest
    t = Training(client, tmp_path)
    w = approved_world(client)
    # Four extra clear winners waiting to be approved while the batch is frozen.
    extra = World(client, title='QA late approvals')
    late = extra.add_items(4, prefix='Late')
    for iid in late:
        assert extra.vote(iid, 'expert', 'a').status_code == 200
    version = t.eligibility()['snapshot_version']

    def act(i):
        if i == 0:
            return t.freeze('freeze-1', version)
        return client.put(f'{P}/items/{late[i - 1]}/adjudication', headers=ADMIN2, json={'outcome': 'winner', 'winning_candidate_id': 'a', 'reason': 'late'})
    out = race(5, act)
    assert all(r.status_code in {200, 202, 409, 503} for r in out), [(r.status_code, r.text[:100]) for r in out]
    batches = t.batches()
    store = client.app.state.gateway.review_store
    for b in batches['items']:
        snap = store.batch_snapshot(b['id'])
        assert digest(snap) == b['snapshot_sha256'], 'stored snapshot no longer hashes to snapshot_sha256'
        assert len(snap['items']) == b['sample_count'] == len(t.items(b['id']))
    before = [(b['id'], b['snapshot_sha256'], b['sample_count'], [i['id'] for i in t.items(b['id'])]) for b in batches['items']]
    # More mutations after the freeze: bulk approve, late individual approvals, retries, settings.
    for iid in late:
        client.put(f'{P}/items/{iid}/adjudication', headers=ADMIN, json={'outcome': 'winner', 'winning_candidate_id': 'a', 'reason': 'late'})
    client.post(f'{P}/approvals/bulk', headers=ADMIN, json={'source': 'all', 'search': '', 'snapshot_version': w.preview()['snapshot_version']})
    client.put(f'{P}/live-settings', headers=ADMIN, json={'sample_percent': 0})
    client.post(f'{TRAIN}/batches/{batches["items"][0]["id"]}/retry', headers=ADMIN, json={'request_key': 'retry-x'}) if batches['items'] else None
    after = [(b['id'], b['snapshot_sha256'], b['sample_count'], [i['id'] for i in t.items(b['id'])]) for b in t.batches()['items']]
    assert after == before
    for b in t.batches()['items']:
        assert digest(store.batch_snapshot(b['id'])) == b['snapshot_sha256']
    # Items already inside a batch can never be re-adjudicated or re-voted.
    for _, _, _, ids in before:
        for iid in ids[:3]:
            assert client.put(f'{P}/items/{iid}/adjudication', headers=ADMIN, json={'outcome': 'skip', 'reason': 'x'}).status_code in {409}


def test_d_duplicate_live_samples_are_distinct_items_but_one_training_pair(client, tmp_path):
    state = client.app.state.gateway
    ids = []
    for _ in range(3):
        a = live(client).json()['assignment']
        ids.append(a['item_id'])
        client.put(f'{P}/assignments/{a["id"]}/judgment', headers=REVIEW, json={'decision': 'left'})
    assert len(set(ids)) == 3
    with state.review_store.connection() as db:
        keys = {r[0] for r in db.execute('SELECT review_pair_key(snapshot) FROM items')}
    # Prompt + both answers identical (modulo candidate order) => one canonical pair fingerprint.
    assert len(keys) == 1


def test_d_live_pairs_rate_limit_is_15_per_minute_with_429(mw_client):
    mw_client.app.state.gateway.settings.review_live_sample_rate = 0
    codes = [live(mw_client).status_code for _ in range(16)]
    assert codes[:15] == [200] * 15 and codes[15] == 429
    last = live(mw_client)
    assert last.status_code == 429 and last.json() == {'detail': 'rate limit exceeded'}
    # Other routes keep their own, higher, bucket.
    assert mw_client.get(f'{P}/me', headers=REVIEW).status_code == 200
    # Another route cannot be used to reset the live-pairs bucket.
    assert live(mw_client).status_code == 429


def test_d_rate_limit_response_has_retry_after(mw_client):
    mw_client.app.state.gateway.settings.review_live_sample_rate = 0
    for _ in range(15):
        live(mw_client)
    r = live(mw_client)
    assert r.status_code == 429 and r.headers.get('retry-after')


def test_d_rate_limit_cannot_be_bypassed_by_varying_path_ids(mw_client):
    codes = [mw_client.get(f'{P}/overview/guess-{i}', headers={'Authorization': 'Bearer wrong-%d' % i}).status_code for i in range(140)]
    assert 429 in codes


def test_d_rate_limit_not_bypassed_by_query_string_or_forwarded_headers(mw_client):
    mw_client.app.state.gateway.settings.review_live_sample_rate = 0
    for i in range(15):
        assert mw_client.post(f'{P}/live-pairs?cache={i}', headers={**REVIEW, 'X-Forwarded-For': f'10.0.0.{i}', 'X-Real-IP': f'10.1.0.{i}'},
                              json={'prompt_messages': [{'role': 'user', 'content': 'q'}], 'answer': 'a'}).status_code == 200
    r = mw_client.post(f'{P}/live-pairs?cache=new', headers={**REVIEW, 'X-Forwarded-For': '8.8.8.8'},
                       json={'prompt_messages': [{'role': 'user', 'content': 'q'}], 'answer': 'a'})
    assert r.status_code == 429


SECRETS = {
    'openai project': 'sk-proj-' + 'Ab1' * 10,
    'anthropic': 'sk-ant-api03-' + 'Zz9' * 10,
    'jwt': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcdefghijk1234567',
    'api_key assignment': 'api_key = ABCDEFGH12345678abcd',
    'db url': 'postgres://user:hunter2@db.internal/app',
    'card': '4111 1111 1111 1111',
    'ssn': '078-05-1120',
    'github pat': 'github_pat_' + 'A' * 45,
}


@pytest.mark.parametrize('name', list(SECRETS))
def test_d_live_pairs_redacts_secrets_before_storage_and_before_model(client, name):
    secret = SECRETS[name]
    state = client.app.state.gateway
    r = live(client, prompt_messages=[{'role': 'user', 'content': f'Please check {secret} now'}], answer=f'Answer mentions {secret}', source_group=f'sess {secret}')
    assert r.status_code == 200 and r.json()['offered'] is True, r.text
    needle = secret.split('Bearer ')[-1].split('://')[-1].split('= ')[-1]
    blob = r.text
    assert needle not in blob, f'{name} leaked in response'
    assert 'REDACTED' in blob
    assert all(needle not in str(c) for c in state.runner.calls), 'secret reached the model'
    iid = r.json()['assignment']['item_id']
    detail = client.get(f'{P}/overview/{iid}', headers=ADMIN).text
    assert needle not in detail
    # Not on disk either (DB file bytes, including WAL).
    path = state.review_store.path
    data = b''.join(p.read_bytes() for p in path.parent.glob(path.name + '*'))
    assert needle.encode() not in data


def test_d_pii_email_and_phone_current_behaviour(client):
    """Documents current sanitize coverage: card/SSN/secrets are redacted; email and phone are not."""
    email, phone = 'alice.example@example.org', '+84 912 345 678'
    r = live(client, answer=f'Contact {email} or {phone}')
    assert r.status_code == 200
    text = r.text
    assert email in text and phone in text  # finding T4-M4: not covered by redact_public_input


def test_d_secret_split_by_zero_width_chars_is_a_known_gap(client):
    secret = 'sk-proj-' + 'Ab1' * 10
    obfuscated = secret[:12] + '​' + secret[12:]
    r = live(client, answer=f'key {obfuscated}')
    assert r.status_code == 200
    # Known inherent limit of regex redaction; recorded, not asserted as safe.
    assert (obfuscated in r.text) or ('REDACTED' in r.text)


# =============================================================================== E
def test_e_error_bodies_never_leak_internals(client, tmp_path):
    t = Training(client, tmp_path)
    w = World(client)
    iid = w.add_items(1)[0]
    w.vote(iid, 'expert', 'a')
    probes = [
        ('GET', '/overview/nope', ADMIN, None), ('GET', '/assignments/nope', REVIEW, None), ('GET', '/campaigns/nope/export', ADMIN, None),
        ('GET', '/reward/training/batches/nope', ADMIN, None), ('GET', '/reward/training/runs/nope/events', ADMIN, None),
        ('PUT', f'/items/{iid}/adjudication', ADMIN, {'outcome': 'winner', 'winning_candidate_id': 'zzz', 'reason': 'x'}),
        ('POST', '/approvals/bulk', ADMIN, {'snapshot_version': '1' * 64}), ('GET', '/overview', None, None), ('GET', '/overview', REVIEW, None),
        ('POST', '/reward/training/batches', ADMIN, {'request_key': 'k', 'snapshot_version': '1' * 64}),
        ('PUT', '/live-settings', ADMIN, {'sample_percent': 'x'}),
    ]
    for method, path, headers, body in probes:
        r = client.request(method, P + path, headers=headers or {}, **({'json': body} if body is not None else {}))
        clean(r)
        assert r.status_code in {401, 403, 404, 409, 422}, (path, r.status_code)
    # Unknown-path 404 and 405 from the framework are generic.
    for path in ['/review-api/nope', '/review-api/../etc/passwd', '/nothing']:
        clean(client.get(path, headers=ADMIN))


def test_e_training_upstream_failures_do_not_leak_url_token_or_remote_body(client, tmp_path):
    t = Training(client, tmp_path)
    approved_world(client)
    version = t.eligibility()['snapshot_version']
    state = client.app.state.gateway

    def boom(request):
        raise httpx.ConnectError('connect failed to http://private-training/jobs token=private /home/vast/worker.py')
    state.reward_training_transport = httpx.MockTransport(boom)
    r = t.freeze('leak-1', version)
    assert r.status_code == 503, r.text
    clean(r, allow_5xx=True)
    assert 'private' not in r.text and 'http://' not in r.text

    def stdout_500(request):
        return httpx.Response(500, text='Traceback (most recent call last): File "/root/worker.py" Authorization: Bearer private STDOUT')
    state.reward_training_transport = httpx.MockTransport(stdout_500)
    batch_id = t.batches()['items'][0]['id']
    for path in [f'/batches/{batch_id}', f'/batches', f'/runs/{t.batches()["items"][0]["runs"][0]["id"]}', f'/runs/{t.batches()["items"][0]["runs"][0]["id"]}/events']:
        r = client.get(TRAIN + path, headers=ADMIN)
        assert r.status_code in {200, 503}, (path, r.status_code)
        for needle in ('Traceback', '/root/', 'STDOUT', 'Bearer private'):
            assert needle not in r.text, (path, needle)


def test_e_training_events_whitelist_structured_telemetry_and_preserve_cursor(client, tmp_path):
    """Training event responses expose only the public telemetry fields and preserve paging."""
    t = Training(client, tmp_path)
    approved_world(client)
    version = t.eligibility()['snapshot_version']
    created = t.freeze('pass-1', version)
    assert created.status_code == 202
    run = created.json()['runs'][0]
    state = client.app.state.gateway

    requests = []

    def hostile(request):
        requests.append(request)
        if request.url.path.endswith('/events'):
            return httpx.Response(200, json={'items': [
                {'seq': 8, 'time': 't', 'stage': 'training', 'epoch': 2, 'step': 4,
                 'total_steps': 12, 'loss': 0.25, 'stdout': 'WORKER-STDOUT',
                 'token': 'WORKER-TOKEN', 'unknown': 'private'},
            ], 'next_seq': 8})
        return httpx.Response(404, json={'detail': 'x'})
    state.reward_training_transport = httpx.MockTransport(hostile)
    r = client.get(f'{TRAIN}/runs/{run["id"]}/events?after_seq=5&limit=7', headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {'items': [{
        'seq': 8, 'time': 't', 'stage': 'training', 'epoch': 2, 'step': 4,
        'total_steps': 12, 'loss': 0.25,
    }], 'next_seq': 8}
    assert len(requests) == 1
    assert dict(requests[0].url.params) == {'after_seq': '5', 'limit': '7'}

    invalid_payloads = [
        {'items': [{'seq': True, 'time': 't', 'stage': 'training'}], 'next_seq': 8},
        {'items': [{'seq': 8, 'time': 1, 'stage': 'training'}], 'next_seq': 8},
        {'items': [{'seq': 8, 'time': 't', 'stage': 'private-stage'}], 'next_seq': 8},
        {'items': [{'seq': 8, 'time': 't', 'stage': 'training', 'epoch': True}], 'next_seq': 8},
        {'items': [{'seq': 8, 'time': 't', 'stage': 'training', 'loss': float('inf')}], 'next_seq': 8},
        {'items': [{'seq': 8, 'time': 't', 'stage': 'training'}], 'next_seq': True},
    ]
    for payload in invalid_payloads:
        state.reward_training_transport = httpx.MockTransport(
            lambda request, payload=payload: httpx.Response(200, json=payload))
        malformed = client.get(f'{TRAIN}/runs/{run["id"]}/events?after_seq=5&limit=7', headers=ADMIN)
        assert malformed.status_code == 503
        assert 'Traceback' not in malformed.text and 'WORKER-TOKEN' not in malformed.text


def test_e_real_training_service_events_expose_only_structured_telemetry(client, tmp_path):
    t = Training(client, tmp_path)
    approved_world(client)
    created = t.freeze('real-1', t.eligibility()['snapshot_version'])
    assert created.status_code == 202
    run = created.json()['runs'][0]
    ev = client.get(f'{TRAIN}/runs/{run["id"]}/events', headers=ADMIN)
    assert ev.status_code == 200
    allowed = {'seq', 'time', 'stage', 'epoch', 'step', 'total_steps', 'loss'}
    for item in ev.json()['items']:
        assert set(item) <= allowed, item
    body = client.get(f'{TRAIN}/batches/{created.json()["id"]}', headers=ADMIN).text
    for needle in ('private', 'Bearer', 'token', '/home', 'stdout', 'stderr'):
        assert needle not in body.replace('request_key', ''), needle


def test_e_export_does_not_include_tokens_paths_or_internal_fields(client):
    w = World(client)
    iid = w.add_items(1)[0]
    w.vote(iid, 'expert', 'a')
    w.adjudicate(iid)
    import io, zipfile
    for fmt in ('raw', 'preference'):
        r = client.get(f'{P}/campaigns/{w.cid}/export?format={fmt}', headers=ADMIN)
        assert r.status_code == 200 and r.headers['content-type'] == 'application/zip'
        assert re.fullmatch(r'attachment; filename="review-[0-9a-f-]{36}-%s\.zip"' % fmt, r.headers['content-disposition'])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert sorted(z.namelist()) == sorted([fmt + '.jsonl', 'manifest.json'])
        text = ''.join(z.read(n).decode() for n in z.namelist())
        for needle in ('admin-token', 'review-token', '/home/', '.db', 'Bearer', 'token_sha256'):
            assert needle not in text, needle
    assert client.get(f'{P}/campaigns/{w.cid}/export', headers=REVIEW).status_code == 403
    assert client.get(f'{P}/campaigns/{w.cid}/export').status_code == 401


def test_e_credentials_never_returned_by_reviewers_or_me(client):
    for path in ['/me', '/reviewers']:
        text = client.get(P + path, headers=ADMIN).text
        assert 'token' not in text.lower() and 'sha256' not in text.lower()
    assert 'admin' not in client.get(f'{P}/reviewers', headers=ADMIN).text.replace('"role"', '')  # only reviewer actors listed


def test_e_audit_events_do_not_record_content_or_secrets(client):
    state = client.app.state.gateway
    secret = 'sk-proj-' + 'Ab1' * 10
    a = live(client, answer='Answer ' + secret).json()['assignment']
    client.put(f'{P}/assignments/{a["id"]}/judgment', headers=REVIEW, json={'decision': 'left', 'comment': 'free text comment'})
    client.put(f'{P}/live-settings', headers=ADMIN, json={'sample_percent': 50})
    with state.review_store.connection() as db:
        rows = db.execute('SELECT action,details FROM audit_events').fetchall()
    blob = json.dumps([tuple(r) for r in rows])
    assert 'free text comment' not in blob and secret not in blob and 'Answer' not in blob
    assert {r['action'] for r in rows} >= {'live_pair.create', 'judgment.submit', 'live.settings.update'}


def test_e_openapi_and_docs_do_not_expose_token_hashes(client):
    text = json.dumps(client.app.openapi())
    for token in TOKENS:
        assert hashlib.sha256(token.encode()).hexdigest() not in text and token not in text


def test_e_500_on_internal_error_is_generic(tmp_path):
    """Force a storage failure: the client must see a generic body, not the SQL/path."""
    app = make_app(tmp_path)
    store = app.state.gateway.review_store

    def broken(*a, **k):
        raise sqlite3.OperationalError('unable to open database file /home/ict/secret/reviews.db')
    store.overview = broken
    r = TestClient(app, raise_server_exceptions=False).get(f'{P}/overview', headers=ADMIN)
    assert r.status_code == 500
    for needle in ('sqlite', '/home/', 'reviews.db', 'Traceback', 'OperationalError', 'unable to open'):
        assert needle not in r.text, needle


# ------------------------------------------------------- confirmed defects (xfail)
@pytest.mark.parametrize('route', ['judgment', 'campaign', 'adjudication'])
def test_c_lone_surrogate_in_json_string_is_422_not_500(client, route):
    w = World(client)
    iid = w.add_items(1)[0]
    aid = w.assign(iid, 'expert')['id']
    w.vote(iid, 'other', 'a') if False else None
    J = {'Content-Type': 'application/json'}
    if route == 'judgment':
        r = client.put(f'{P}/assignments/{aid}/judgment', headers={**REVIEW, **J}, content=b'{"decision":"left","comment":"\\ud800"}')
    elif route == 'campaign':
        r = client.post(f'{P}/campaigns', headers={**ADMIN, **J}, content=b'{"title":"\\ud800"}')
    else:
        assert w.vote(iid, 'expert', 'a').status_code == 200
        r = client.put(f'{P}/items/{iid}/adjudication', headers={**ADMIN, **J}, content=b'{"outcome":"skip","reason":"\\ud800"}')
    assert r.status_code == 422, r.status_code


@pytest.mark.parametrize('path', ['/overview', '/reward/training/batches'])
def test_c_huge_offset_is_not_a_500(client, tmp_path, path):
    Training(client, tmp_path)
    r = client.get(P + path, headers=ADMIN, params={'offset': '9' * 30})
    assert r.status_code in {200, 422}, r.status_code


@pytest.mark.xfail(strict=True, reason='T4-M7: duplicate JSON keys are silently accepted (last value wins) by live-pairs; not exploitable, recorded for completeness.')
def test_c_duplicate_json_keys_rejected(client):
    r = raw(client, 'POST', '/live-pairs', b'{"prompt_messages":[{"role":"user","content":"q"}],"answer":"a","answer":"b"}', headers=REVIEW)
    assert r.status_code == 422


@pytest.mark.parametrize('text', ['Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789', 'bearer abcdefghijklmnopqrstuvwxyz0123456789',
                                  'curl -H "Authorization: Bearer Zk3jH8sLq0PwX1yT7vN2mC9bR4dF6gA5" https://x'])
def test_d_bearer_token_in_pasted_http_header_is_redacted(client, text):
    r = live(client, answer='Header: ' + text)
    assert r.status_code == 200 and r.json()['offered'] is True
    token = re.search(r'([A-Za-z0-9]{30,})', text).group(1)
    assert token not in r.text
