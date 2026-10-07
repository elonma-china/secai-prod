"""A/B review backend QA: permission matrix (P1), immutability of user votes (P2),
end-to-end API flow F1-F5/F7 and live sampling/cooldown behaviour.

Everything runs on a temporary SQLite database; the training gateway is an in-process
httpx.MockTransport, so no Vast/GPU or external service is contacted.
"""
import hashlib
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from test_review_auth import client, ADMIN, REVIEW, OTHER
from test_review_live import configure, PAYLOAD

PREFIX = '/review-api'
TRAIN = PREFIX + '/reward/training'


# --------------------------------------------------------------------------- P1
# Routes that any valid reviewer/admin identity may call (ownership is enforced inside the
# store). Every other /review-api route is admin-only and must be 401/403 for others.
ANY_IDENTITY = {
    ('GET', '/review-api/me'),
    ('GET', '/review-api/campaigns'),
    ('GET', '/review-api/assignments'),
    ('GET', '/review-api/assignments/{aid}'),
    ('PUT', '/review-api/assignments/{aid}/judgment'),
    ('POST', '/review-api/live-pairs'),
    ('POST', '/review-api/report-templates/revise'),
    ('POST', '/review-api/report-templates/render'),
}


def all_routes(app):
    # Taken from the real app's OpenAPI document: FastAPI wraps included routers lazily, so
    # app.routes does not list the individual /review-api endpoints.
    found = set()
    for path, operations in app.openapi()['paths'].items():
        if path.startswith(PREFIX + '/'):
            for method in operations:
                if method.upper() in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
                    found.add((method.upper(), path))
    return sorted(found)


def admin_routes(app):
    return [r for r in all_routes(app) if r not in ANY_IDENTITY]


def concrete(path):
    return re.sub(r'\{[^}]+\}', 'x', path)


def request(c, method, path, headers=None, body=None):
    # '{}' is not a valid body for most routes: auth must be decided before the body is parsed.
    kw = {'headers': {**(headers or {}), 'Content-Type': 'application/json'}}
    if method in {'POST', 'PUT', 'PATCH'}:
        kw['content'] = body if body is not None else '{}'
    return c.request(method, concrete(path), **kw)


def test_route_inventory_is_complete_and_classified(client):
    routes = all_routes(client.app)
    assert len(routes) >= 35
    # Everything that is not explicitly "any identity" is treated as admin-only. A new route
    # therefore starts as admin-only and this file must be touched to relax it deliberately.
    assert ANY_IDENTITY <= set(routes), ANY_IDENTITY - set(routes)
    required = {
        '/review-api/overview', '/review-api/overview/{iid}', '/review-api/approvals/preview',
        '/review-api/approvals/bulk', '/review-api/live-settings',
        '/review-api/reward/training/eligibility', '/review-api/reward/training/batches',
        '/review-api/reward/training/batches/{bid}', '/review-api/reward/training/batches/{bid}/items',
        '/review-api/reward/training/batches/{bid}/retry', '/review-api/reward/training/runs/{rid}',
        '/review-api/reward/training/runs/{rid}/events', '/review-api/campaigns/{cid}/export',
        '/review-api/campaigns/{cid}/imports', '/review-api/campaigns/{cid}/items',
        '/review-api/campaigns/{cid}/stats', '/review-api/items/{iid}/adjudication',
        '/review-api/items/{iid}/judgments',
    }
    assert required <= {p for _, p in admin_routes(client.app)}


def pytest_generate_tests(metafunc):
    if 'admin_route' in metafunc.fixturenames:
        # Built from the real app (same factory as the fixture) so no route is missed.
        import hashlib as _h, json as _j, tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from fastapi import FastAPI
        from secai_gateway.api.review_auth import load_credentials
        from secai_gateway.api.reviews import router
        from secai_gateway.state.review_store import ReviewStore
        tmp = Path(tempfile.mkdtemp())
        f = tmp / 'c.json'
        f.write_text(_j.dumps({'credentials': [{'token_sha256': _h.sha256(b'x').hexdigest(), 'actor_id': 'a', 'role': 'admin'}]}))
        app = FastAPI()
        app.state.gateway = SimpleNamespace(settings=SimpleNamespace(review_enabled=True), review_credentials=load_credentials(f), review_store=ReviewStore(tmp / 'r.db'))
        app.include_router(router)
        routes = admin_routes(app)
        metafunc.parametrize('admin_route', routes, ids=[f'{m} {p.removeprefix(PREFIX)}' for m, p in routes])


@pytest.fixture
def tolerant(client):
    # A 5xx in an admin route must be reported as a status code, not raised into the test.
    return TestClient(client.app, raise_server_exceptions=False)


def test_p1_admin_route_requires_admin(tolerant, admin_route):
    method, path = admin_route
    assert request(tolerant, method, path).status_code == 401, 'no token'
    assert request(tolerant, method, path, {'Authorization': 'Bearer not-a-real-token'}).status_code == 401, 'unknown token'
    # Valid JSON that does not fit the schema: auth must still win over body validation.
    reviewer = request(tolerant, method, path, REVIEW)
    assert reviewer.status_code == 403, f'reviewer got {reviewer.status_code} (auth must precede body validation)'
    if method in {'POST', 'PUT', 'PATCH'}:
        # Non-JSON content type (not parsed as JSON by FastAPI): still 401/403 first.
        raw = {'Content-Type': 'text/plain'}
        assert tolerant.request(method, concrete(path), headers=raw, content='x').status_code == 401
        assert tolerant.request(method, concrete(path), headers={**REVIEW, **raw}, content='x').status_code == 403
    admin = request(tolerant, method, path, ADMIN)
    assert admin.status_code not in {401, 403}, f'admin got {admin.status_code}'
    assert admin.status_code < 500, f'admin request crashed: {admin.status_code}'


PYDANTIC_BODY_ROUTES = [
    ('POST', '/review-api/campaigns'), ('PATCH', '/review-api/campaigns/{cid}'),
    ('POST', '/review-api/items/{iid}/assignments'), ('PUT', '/review-api/items/{iid}/adjudication'),
]


@pytest.mark.xfail(strict=True, reason='Bug B-1: four routes with a FastAPI-declared body return 422 for malformed JSON before authentication (no token, reviewer and admin alike); other admin routes read the body only after auth.')
@pytest.mark.parametrize('route', PYDANTIC_BODY_ROUTES, ids=lambda r: f'{r[0]} {r[1].removeprefix(PREFIX)}')
def test_p1_malformed_json_still_unauthorized_before_body_parse(tolerant, route):
    method, path = route
    assert request(tolerant, method, path, body='not json').status_code == 401
    assert request(tolerant, method, path, REVIEW, body='not json').status_code == 403


@pytest.mark.parametrize('route', sorted(ANY_IDENTITY), ids=lambda r: f'{r[0]} {r[1].removeprefix(PREFIX)}')
def test_p1_identity_routes_need_a_token_but_not_admin(tolerant, route):
    method, path = route
    assert request(tolerant, method, path).status_code == 401
    assert request(tolerant, method, path, {'Authorization': 'Bearer nope'}).status_code == 401
    assert request(tolerant, method, path, REVIEW).status_code not in {401, 403}


def test_p1_reviewer_cannot_see_other_reviewers_assignment(tolerant, client):
    w = World(client)
    item = w.add_items(1)[0]
    aid = w.assign(item, 'expert')['id']
    assert tolerant.get(f'{PREFIX}/assignments/{aid}', headers=OTHER).status_code == 404
    assert tolerant.put(f'{PREFIX}/assignments/{aid}/judgment', headers=OTHER, json={'decision': 'left'}).status_code == 404
    # Admin has no override either: not the admin's assignment.
    assert tolerant.put(f'{PREFIX}/assignments/{aid}/judgment', headers=ADMIN, json={'decision': 'left'}).status_code == 404
    assert w.judgments(item)['judgments'] == []


# ----------------------------------------------------------------------- world
class World:
    """Tiny API driver: admin creates a QA campaign, reviewers vote through the API."""

    def __init__(self, client, title='QA A/B flow'):
        self.c = client
        self.cid = self.ok(client.post(f'{PREFIX}/campaigns', headers=ADMIN, json={'title': title})).json()['id']
        self.ids = {}
        self.assignments = {}

    @staticmethod
    def ok(response, status=200):
        assert response.status_code == status, response.text
        return response

    def add_items(self, n, prefix='Question', start=0, group='group', keys=None):
        lines = []
        names = []
        for i in range(start, start + n):
            ext = f'{prefix}-{i}'
            names.append(ext)
            lines.append(json.dumps({'external_id': ext, 'source_group': f'{group}-{prefix}-{i}',
                'prompt_messages': [{'role': 'user', 'content': f'{prefix} {i}'}],
                'candidates': [{'id': 'a', 'text': f'Good {prefix} {i}'}, {'id': 'b', 'text': f'Bad {prefix} {i}'}]}))
        self.ok(self.c.post(f'{PREFIX}/campaigns/{self.cid}/imports', headers=ADMIN, content='\n'.join(lines).encode()))
        page = self.ok(self.c.get(f'{PREFIX}/campaigns/{self.cid}/items?limit=100', headers=ADMIN)).json()
        self.ids = {r['external_id']: r['id'] for r in page['items']}
        return [self.ids[n_] for n_ in names]

    def assign(self, iid, who):
        rows = self.ok(self.c.post(f'{PREFIX}/items/{iid}/assignments', headers=ADMIN, json={'reviewer_ids': [who]})).json()['items']
        self.assignments[(iid, who)] = rows[0]
        return rows[0]

    def vote(self, iid, who, kind, headers=None):
        a = self.assignments.get((iid, who)) or self.assign(iid, who)
        if kind in {'a', 'b'}:
            body = {'decision': 'left' if a['left_candidate_id'] == kind else 'right'}
        elif kind in {'both_bad', 'insufficient_context'}:
            body = {'decision': kind, 'comment': 'Both answers are unusable'}
        else:
            body = {'decision': kind}
        headers = headers or {'expert': REVIEW, 'other': OTHER}[who]
        return self.c.put(f'{PREFIX}/assignments/{a["id"]}/judgment', headers=headers, json=body)

    def judgments(self, iid):
        return self.ok(self.c.get(f'{PREFIX}/items/{iid}/judgments', headers=ADMIN)).json()

    def adjudicate(self, iid, outcome='winner', winner='a', reason='Reviewed by admin'):
        body = {'outcome': outcome, 'reason': reason}
        if outcome == 'winner':
            body['winning_candidate_id'] = winner
        return self.c.put(f'{PREFIX}/items/{iid}/adjudication', headers=ADMIN, json=body)

    def overview(self, status='pending', source='all', search='', offset=0, limit=20):
        r = self.c.get(f'{PREFIX}/overview', headers=ADMIN, params={'status': status, 'source': source, 'search': search, 'offset': offset, 'limit': limit})
        return self.ok(r).json()

    def preview(self, source='all', search=''):
        return self.ok(self.c.get(f'{PREFIX}/approvals/preview', headers=ADMIN, params={'source': source, 'search': search})).json()

    def bulk(self, version, source='all', search=''):
        return self.c.post(f'{PREFIX}/approvals/bulk', headers=ADMIN, json={'source': source, 'search': search, 'snapshot_version': version})


KINDS = ('tie', 'both_bad', 'skip', 'insufficient_context', 'missing', 'conflict')


def build_mixed(client, good=22):
    """`good` clear single-vote winners, one two-reviewer consistent winner and six items that
    must never be bulk-approved (tie, both bad, skip, insufficient, missing vote, conflict)."""
    w = World(client)
    w.good = w.add_items(good, prefix='Question')
    for iid in w.good:
        assert w.vote(iid, 'expert', 'a').status_code == 200
    w.agreed = w.add_items(1, prefix='Agreed')[0]
    assert w.vote(w.agreed, 'expert', 'a').status_code == 200
    assert w.vote(w.agreed, 'other', 'a').status_code == 200
    w.bad = {}
    for kind in KINDS:
        iid = w.add_items(1, prefix=kind.title())[0]
        w.bad[kind] = iid
        if kind in {'tie', 'both_bad', 'skip', 'insufficient_context'}:
            assert w.vote(iid, 'expert', kind).status_code == 200
        elif kind == 'missing':
            assert w.vote(iid, 'expert', 'a').status_code == 200
            w.assign(iid, 'other')  # second reviewer assigned but has not voted
        else:
            assert w.vote(iid, 'expert', 'a').status_code == 200
            assert w.vote(iid, 'other', 'b').status_code == 200
    # An assignment nobody answered yet is not an A/B record at all.
    w.unanswered = w.add_items(1, prefix='Unanswered')[0]
    w.assign(w.unanswered, 'expert')
    return w


# ------------------------------------------------------------------- F1 + P2
def test_f1_user_vote_creates_complete_record(client):
    w = World(client)
    iid = w.add_items(1)[0]
    a = w.assign(iid, 'expert')
    detail = client.get(f'{PREFIX}/assignments/{a["id"]}', headers=REVIEW).json()
    # The reviewer sees only the question and the two answers (no ids, no provenance).
    assert detail['prompt_messages'][-1]['content'] == 'Question 0'
    assert {detail['left']['text'], detail['right']['text']} == {'Good Question 0', 'Bad Question 0'}
    assert set(detail['left']) == {'text'} and set(detail['right']) == {'text'}
    assert detail['status'] == 'pending'
    assert w.vote(iid, 'expert', 'a').status_code == 200
    over = client.get(f'{PREFIX}/overview/{iid}', headers=ADMIN).json()
    assert over['question'] == 'Question 0' and over['user_choice'] == 'A' and over['user_winner_id'] == 'a'
    assert [c['id'] for c in over['item']['candidates']] == ['a', 'b']
    (j,) = over['judgments']
    assert j['reviewer_id'] == 'expert' and j['submitted_at'] and j['winning_candidate_id'] == 'a'
    assert over['adjudication'] is None and over['complete'] is True
    # Reviewers cannot open the admin A/B view.
    assert client.get(f'{PREFIX}/overview/{iid}', headers=REVIEW).status_code == 403
    assert client.get(f'{PREFIX}/overview', headers=REVIEW).status_code == 403


def test_f1_live_pair_record_has_question_two_answers_choice_submitter_time(client):
    state = configure(client)
    r = client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()
    aid = r['assignment']['id']
    assert client.put(f'{PREFIX}/assignments/{aid}/judgment', headers=REVIEW, json={'decision': 'left'}).status_code == 200
    page = client.get(f'{PREFIX}/overview?source=live', headers=ADMIN).json()
    assert page['total'] == 1 and page['items'][0]['source'] == 'live'
    d = client.get(f"{PREFIX}/overview/{page['items'][0]['id']}", headers=ADMIN).json()
    assert d['question'] == 'Question'
    assert {c['text'] for c in d['item']['candidates']} == {'Original answer', 'Alternative answer'}
    assert d['judgments'][0]['reviewer_id'] == 'expert' and d['judgments'][0]['submitted_at']
    assert d['user_choice'] in {'A', 'B'}
    assert len(state.runner.calls) == 1


def test_p2_admin_approval_never_changes_user_vote(client):
    w = World(client)
    iid = w.add_items(1)[0]
    assert w.vote(iid, 'expert', 'a').status_code == 200
    before = w.judgments(iid)['judgments']
    # Admin disagrees with the user and picks the other answer: stored separately.
    adj = w.adjudicate(iid, winner='b')
    assert adj.status_code == 200
    after = w.judgments(iid)
    assert after['judgments'] == before
    assert after['judgments'][0]['winning_candidate_id'] == 'a'
    assert after['adjudication']['winning_candidate_id'] == 'b' and after['adjudication']['id'] != before[0]['id']
    # Re-approving with another outcome is rejected and changes nothing.
    for other in (w.adjudicate(iid, winner='a'), w.adjudicate(iid, outcome='skip')):
        assert other.status_code == 409
    again = w.adjudicate(iid, winner='b')  # identical replay is idempotent
    assert again.status_code == 200 and again.json()['id'] == adj.json()['id']
    assert w.judgments(iid) == after
    # The submitted vote is frozen for the user too (even the original owner).
    assert w.vote(iid, 'expert', 'b').status_code == 409
    assert w.judgments(iid) == after
    # There is no route that rewrites a judgment except the owner-only PUT.
    judge_routes = {r for r in all_routes(client.app) if 'judgment' in r[1]}
    assert judge_routes == {('PUT', '/review-api/assignments/{aid}/judgment'), ('GET', '/review-api/items/{iid}/judgments')}


def test_p2_adjudication_rules(client):
    w = World(client)
    pending, answered = w.add_items(2)
    assign = w.assign(pending, 'expert')
    assert w.adjudicate(pending).status_code == 409  # no submitted vote yet
    assert w.vote(answered, 'expert', 'a').status_code == 200
    assert w.adjudicate(answered, winner='zzz').status_code == 422  # unknown candidate
    assert client.put(f'{PREFIX}/items/{answered}/adjudication', headers=ADMIN, json={'outcome': 'tie', 'winning_candidate_id': 'a', 'reason': 'x'}).status_code == 422
    assert w.judgments(answered)['adjudication'] is None
    assert assign['id']


# -------------------------------------------------------------- F2 / F3 / F4 / F7
def test_f2_single_approval_moves_item_out_of_pending_and_into_queue(client):
    w = build_mixed(client)
    total_pending = w.overview()['total']
    assert total_pending == 22 + 1 + 6  # unanswered assignment is not a record
    first = w.good[0]
    assert w.adjudicate(first).status_code == 200
    assert w.overview()['total'] == total_pending - 1
    queue = w.overview('approved')
    assert queue['total'] == 1 and queue['items'][0]['id'] == first
    assert queue['items'][0]['admin_choice'] == 'A' and queue['items'][0]['adjudication']['winning_candidate_id'] == 'a'
    # Exclusion (skip) is kept, retrievable by filter and not approved.
    assert w.adjudicate(w.bad['skip'], outcome='skip', reason='Excluded').status_code == 200
    excl = w.overview('excluded')
    assert [r['id'] for r in excl['items']] == [w.bad['skip']] and excl['total'] == 1
    assert w.overview('approved')['total'] == 1
    assert w.overview('all')['total'] == total_pending  # every voted record stays reachable via 'all'


def test_f3_bulk_preview_counts_current_filter_beyond_one_page(client):
    w = build_mixed(client)
    page = w.overview(limit=5)
    assert len(page['items']) == 5 and page['total'] == 29
    p = w.preview()
    assert p['eligible_count'] == 23 and p['skipped_count'] == 6  # >20 = more than one default page
    assert re.fullmatch('[0-9a-f]{64}', p['snapshot_version'])
    # The count follows the filter that the admin has applied, including unseen pages.
    assert w.preview(search='Question 1')['eligible_count'] == 11  # 1, 10..19
    assert w.preview(search='Tie')['eligible_count'] == 0 and w.preview(search='Tie')['skipped_count'] == 1
    assert w.preview(source='live')['eligible_count'] == 0
    assert w.preview(source='qa')['eligible_count'] == 23
    # Nothing was changed by previewing.
    assert w.overview()['total'] == 29 and w.overview('approved')['total'] == 0


def test_f3_bulk_approves_only_clear_complete_consistent_winners(client):
    w = build_mixed(client)
    assert w.adjudicate(w.good[0]).status_code == 200  # individually approved earlier
    p = w.preview()
    assert p['eligible_count'] == 22 and p['skipped_count'] == 6
    # Wrong / stale confirmation token is refused and approves nothing.
    assert w.bulk('0' * 64).status_code == 409
    assert w.overview('approved')['total'] == 1
    result = w.bulk(p['snapshot_version'])
    assert result.status_code == 200 and result.json() == {'approved': 22, 'skipped': 6}
    approved = {r['id'] for r in w.overview('approved', limit=100)['items']}
    assert approved == set(w.good) | {w.agreed}
    assert approved.isdisjoint(w.bad.values())
    # Tie / both bad / skip / insufficient / missing vote / conflict stay pending, unadjudicated.
    pending = w.overview(limit=100)
    assert {r['id'] for r in pending['items']} == set(w.bad.values()) and pending['total'] == 6
    for kind, iid in w.bad.items():
        assert w.judgments(iid)['adjudication'] is None, kind
    # Clear winner matches the user's own vote, and the user vote is unchanged.
    for iid in w.good + [w.agreed]:
        j = w.judgments(iid)
        assert j['adjudication']['winning_candidate_id'] == 'a'
        assert all(v['winning_candidate_id'] == 'a' for v in j['judgments'])
    # Replay of the same confirmation after the approvals is rejected (nothing to approve twice).
    assert w.bulk(p['snapshot_version']).status_code == 409
    assert w.preview()['eligible_count'] == 0
    assert w.overview('approved')['total'] == 23


def test_f3_bulk_confirmation_goes_stale_when_data_changes(client):
    w = build_mixed(client, good=3)
    p = w.preview()
    late = w.add_items(1, prefix='Late')[0]
    assert w.vote(late, 'expert', 'a').status_code == 200  # new feedback arrives after the preview
    assert w.bulk(p['snapshot_version']).status_code == 409
    assert w.overview('approved')['total'] == 0
    assert w.bulk(w.preview()['snapshot_version']).json()['approved'] == 5


def test_f3_bulk_respects_filter_source_and_search(client):
    w = build_mixed(client)
    p = w.preview(search='Question 2')  # 2, 20, 21
    assert p['eligible_count'] == 3
    assert w.bulk(p['snapshot_version'], search='Question 2').json()['approved'] == 3
    assert w.overview('approved')['total'] == 3
    assert w.preview()['eligible_count'] == 20
    # Wrong filter with a token minted for another filter is a stale confirmation.
    assert w.bulk(p['snapshot_version'], source='qa').status_code == 409


def test_f7_filters_search_and_pagination_after_actions(client):
    w = build_mixed(client)
    w.adjudicate(w.bad['skip'], outcome='skip', reason='Excluded')
    w.adjudicate(w.bad['both_bad'], outcome='both_bad', reason='Both bad')
    w.bulk(w.preview()['snapshot_version'])
    assert w.overview('approved')['total'] == 23
    assert w.overview('excluded')['total'] == 2
    assert w.overview('pending')['total'] == 4
    assert w.overview('all')['total'] == 29
    ids = []
    for offset in (0, 10, 20):
        page = w.overview('approved', offset=offset, limit=10)
        assert page['offset'] == offset and page['total'] == 23
        ids += [r['id'] for r in page['items']]
    assert len(ids) == 23 and len(set(ids)) == 23
    s = w.overview('approved', search='question 7')
    assert [r['question'] for r in s['items']] == ['Question 7'] and s['total'] == 1
    assert w.overview('excluded', search='Skip')['total'] == 1
    assert w.overview('pending', search='Skip')['total'] == 0
    assert client.get(f'{PREFIX}/overview?limit=101', headers=ADMIN).status_code == 422
    assert client.get(f'{PREFIX}/overview?status=bogus', headers=ADMIN).status_code == 422


# --------------------------------------------------------------- F4 / F5 / F6
class Training:
    def __init__(self, client, tmp_path, minimum=3):
        from secai_gateway.reward.training_jobs import TrainingJobs
        from secai_gateway.reward.training_service import create_training_service
        self.client = client
        gateway = client.app.state.gateway
        self.store = gateway.review_store
        s = gateway.settings
        s.reward_training_enabled = True
        s.reward_training_url = 'http://private-training'
        s.reward_training_token = 'private'
        self.jobs = TrainingJobs(tmp_path / 'jobs')
        remote = TestClient(create_training_service(self.jobs, token_sha256=hashlib.sha256(b'private').hexdigest()))
        self.posts = 0

        def transport(req):
            if req.method == 'POST':
                self.posts += 1
            response = remote.request(req.method, req.url.path, headers=dict(req.headers), content=req.content)
            return httpx.Response(response.status_code, content=response.content)

        gateway.reward_training_transport = httpx.MockTransport(transport)
        self.set_minimum(minimum)

    def set_minimum(self, n):
        with self.store.connection(True) as db:
            db.execute("UPDATE training_policy SET minimum_pairs=? WHERE purpose='demo'", (n,))

    def eligibility(self, purpose='demo'):
        r = self.client.get(f'{TRAIN}/eligibility', headers=ADMIN, params={'purpose': purpose})
        assert r.status_code == 200, r.text
        return r.json()

    def freeze(self, key, version=None, headers=None):
        version = version or self.eligibility()['snapshot_version']
        return self.client.post(f'{TRAIN}/batches', headers=headers or ADMIN, json={'request_key': key, 'purpose': 'demo', 'snapshot_version': version})

    def batches(self):
        return self.client.get(f'{TRAIN}/batches', headers=ADMIN).json()

    def items(self, bid):
        out = []
        for offset in range(0, 1000, 100):
            page = self.client.get(f'{TRAIN}/batches/{bid}/items', headers=ADMIN, params={'offset': offset, 'limit': 100}).json()
            out += page['items']
            if offset + 100 >= page['total']:
                return out


def approved_world(client, good=22):
    w = build_mixed(client, good=good)
    p = w.preview()
    assert w.bulk(p['snapshot_version']).status_code == 200
    return w


def test_f4_eligibility_counts_dedups_and_explains_lock(client, tmp_path):
    t = Training(client, tmp_path, minimum=1000)
    w = approved_world(client)
    e = t.eligibility()
    # Only approved winners count; pending/excluded do not. 23 unique approved pairs.
    assert e['valid_new_count'] == 23 and e['minimum_pairs'] == 1000
    assert e['eligible'] is False
    assert any('1000' in r for r in e['blocked_reasons'])  # reason shown to the admin
    assert t.freeze('too-few', e['snapshot_version']).status_code == 409
    assert t.batches()['total'] == 0 and t.jobs.list() == []
    # An approved duplicate of an already approved pair (same prompt and answers) is deduplicated.
    dup = World(client, title='QA duplicate campaign')
    (d,) = dup.add_items(1, prefix='Question', start=3)
    assert dup.vote(d, 'expert', 'a').status_code == 200 and dup.adjudicate(d).status_code == 200
    assert t.eligibility()['valid_new_count'] == 23
    # Pending items never count.
    assert w.overview('pending')['total'] == 6
    # Production purpose is never unlocked by demo data.
    prod = t.eligibility('production')
    assert prod['eligible'] is False and prod['valid_new_count'] == 0
    # With the training service disabled the lock is explained too.
    t.set_minimum(3)
    client.app.state.gateway.settings.reward_training_enabled = False
    e2 = t.eligibility()
    assert e2['eligible'] is False and e2['blocked_reasons']
    client.app.state.gateway.settings.reward_training_enabled = True
    assert t.eligibility()['eligible'] is True


def test_f4_items_leave_queue_when_batched_and_new_approvals_start_new_queue(client, tmp_path):
    t = Training(client, tmp_path)
    w = approved_world(client)
    assert w.overview('approved')['total'] == 23
    r = t.freeze('first')
    assert r.status_code == 202, r.text
    assert w.overview('approved')['total'] == 0
    assert t.eligibility()['valid_new_count'] == 0


def test_f5_batch_is_immutable_and_replay_makes_no_second_batch_or_job(client, tmp_path):
    t = Training(client, tmp_path)
    w = approved_world(client)
    version = t.eligibility()['snapshot_version']
    assert t.freeze('k1', version, headers=REVIEW).status_code == 403
    assert t.batches()['total'] == 0
    r = t.freeze('k1', version)
    assert r.status_code == 202, r.text
    batch = r.json()
    assert batch['sample_count'] == 23 and len(batch['runs']) == 1 and len(t.jobs.list()) == 1
    assert re.fullmatch('[0-9a-f]{64}', batch['snapshot_sha256'])
    frozen_items = t.items(batch['id'])
    assert len(frozen_items) == 23
    frozen_json = json.dumps(frozen_items, sort_keys=True)
    # Double submit / replay with the same request key (even with a different version).
    for replay_version in (version, '0' * 64):
        again = t.freeze('k1', replay_version)
        assert again.status_code == 202 and again.json()['id'] == batch['id']
    assert t.batches()['total'] == 1 and len(t.jobs.list()) == 1 and t.posts == 1
    # A new key for the same (now consumed) data cannot make a second batch.
    assert t.freeze('k2', version).status_code == 409
    assert t.batches()['total'] == 1 and len(t.jobs.list()) == 1
    # Later review work does not touch the frozen batch: user vote, admin decision, new approval.
    batched = w.good[0]
    assert w.vote(batched, 'expert', 'b').status_code == 409
    assert w.adjudicate(batched, winner='b').status_code == 409
    late = w.add_items(1, prefix='Fresh', start=50)[0]
    assert w.vote(late, 'expert', 'b').status_code == 200 and w.adjudicate(late, winner='b').status_code == 200
    detail = client.get(f'{TRAIN}/batches/{batch["id"]}', headers=ADMIN).json()
    assert detail['snapshot_sha256'] == batch['snapshot_sha256'] and detail['sample_count'] == 23
    assert json.dumps(t.items(batch['id']), sort_keys=True) == frozen_json
    assert late not in {i['id'] for i in t.items(batch['id'])}
    assert w.overview('approved')['items'][0]['id'] == late and w.overview('approved')['total'] == 1
    assert t.eligibility()['valid_new_count'] == 1
    # The same pair re-imported in another campaign is not eligible for a new batch.
    dup = World(client, title='QA duplicate campaign')
    (d,) = dup.add_items(1, prefix='Question', start=3)
    assert dup.vote(d, 'expert', 'a').status_code == 200 and dup.adjudicate(d).status_code == 200
    assert d not in {r['id'] for r in w.overview('approved', limit=100)['items']}
    assert t.eligibility()['valid_new_count'] == 1


def test_f5_concurrent_double_submit_creates_one_batch(client, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    t = Training(client, tmp_path)
    approved_world(client)
    version = t.eligibility()['snapshot_version']
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: t.freeze('same', version), range(4)))
    assert all(r.status_code in {202, 503} for r in results), [r.text for r in results]
    ids = {r.json()['id'] for r in results if r.status_code == 202}
    assert len(ids) == 1
    assert t.batches()['total'] == 1 and len(t.jobs.list()) == 1


def test_f5_f6_retry_creates_new_attempt_keeps_dataset_and_history(client, tmp_path):
    t = Training(client, tmp_path)
    approved_world(client)
    batch = t.freeze('first').json()
    bid, run1 = batch['id'], batch['runs'][0]
    frozen = json.dumps(t.items(bid), sort_keys=True)
    retry = lambda key, headers=ADMIN: client.post(f'{TRAIN}/batches/{bid}/retry', headers=headers, json={'request_key': key})
    assert retry('r1', REVIEW).status_code == 403
    assert retry('r1').status_code == 409  # active attempt: nothing to retry
    t.store.update_training_run(run1['id'], {'status': 'failed', 'error': 'GPU unavailable'})
    posts_before = t.posts
    r = retry('r1')
    assert r.status_code == 202, r.text
    run2 = r.json()
    assert run2['id'] != run1['id'] and run2['batch_id'] == bid and t.posts == posts_before + 1
    detail = client.get(f'{TRAIN}/batches/{bid}', headers=ADMIN).json()
    assert [x['id'] for x in detail['runs']] == [run1['id'], run2['id']]
    assert detail['runs'][0]['status'] == 'failed' and detail['runs'][0]['error'] == 'GPU unavailable'  # old attempt history kept
    assert detail['snapshot_sha256'] == batch['snapshot_sha256'] and detail['sample_count'] == batch['sample_count']
    assert json.dumps(t.items(bid), sort_keys=True) == frozen
    # Replay of the retry key and a second retry while active never add attempts or remote jobs.
    assert retry('r1').json()['id'] == run2['id']
    assert retry('r2').status_code == 409
    assert len(client.get(f'{TRAIN}/batches/{bid}', headers=ADMIN).json()['runs']) == 2
    assert t.batches()['total'] == 1 and t.posts == posts_before + 1
    # Both attempts are addressable (run detail), the old one with its failure.
    got = client.get(f'{TRAIN}/runs/{run1["id"]}', headers=ADMIN).json()
    assert got['status'] == 'failed' and got['error'] == 'GPU unavailable'
    assert client.get(f'{TRAIN}/runs/{run2["id"]}/events', headers=REVIEW).status_code == 403
    # Connection errors are shown as errors, not faked progress.
    client.app.state.gateway.reward_training_transport = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError('down')))
    t.store.update_training_run(run2['id'], {'status': 'failed', 'error': 'down'})
    assert client.get(f'{TRAIN}/batches/{bid}', headers=ADMIN).json()['status'] == 'failed'


def test_f6_batch_history_lists_date_id_count_status(client, tmp_path):
    t = Training(client, tmp_path)
    approved_world(client)
    bid = t.freeze('hist').json()['id']
    page = t.batches()
    (b,) = page['items']
    assert b['id'] == bid and b['created_at'] and b['sample_count'] == 23 and b['status'] in {'queued', 'dispatching'}
    assert 'snapshot' not in b and 'partitions' not in b
    assert client.get(f'{TRAIN}/batches/missing', headers=ADMIN).status_code == 404
    assert client.get(f'{TRAIN}/batches/{bid}/items?limit=5&offset=20', headers=ADMIN).json()['items'].__len__() == 3


# ----------------------------------------------------------- live sampling d)
def defaults():
    from secai_gateway.config import Settings
    f = Settings.model_fields
    return f['review_live_sample_rate'].default, f['review_live_cooldown_seconds'].default


def test_d_defaults_are_10_percent_and_600_seconds(client):
    rate, cooldown = defaults()
    assert (rate, cooldown) == (0.1, 600)
    s = client.app.state.gateway.settings
    s.review_live_sample_rate, s.review_live_cooldown_seconds = rate, cooldown
    got = client.get(f'{PREFIX}/live-settings', headers=ADMIN).json()
    assert got == {'sample_percent': 10, 'scope': 'global', 'cooldown_seconds': 600}


def test_d_default_rate_is_10_percent_boundary(client, monkeypatch):
    import secrets
    state = configure(client, 0.1)
    real = secrets.randbelow
    draw = {'value': 0}
    monkeypatch.setattr(secrets, 'randbelow', lambda n: draw['value'] if n == 1_000_000 else real(n))
    draw['value'] = 99_999
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()['offered'] is True
    state.review_live_next.clear()
    draw['value'] = 100_000  # exactly 10% of the range: first value that is not invited
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json() == {'offered': False}
    assert len(state.runner.calls) == 1  # no model call for a non-sampled turn


def test_d_admin_changes_rate_user_cannot(client):
    state = configure(client, 0.1)
    path = f'{PREFIX}/live-settings'
    assert client.put(path, headers=REVIEW, json={'sample_percent': 100}).status_code == 403
    assert client.put(path, headers=OTHER, json={'sample_percent': 100}).status_code == 403
    assert client.put(path, headers={}, json={'sample_percent': 100}).status_code == 401
    assert client.get(path, headers=ADMIN).json()['sample_percent'] == 10
    # Users cannot smuggle a rate/cooldown through the invitation request either.
    for extra in ({'sample_percent': 100}, {'cooldown_seconds': 0}):
        assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json={**PAYLOAD, **extra}).status_code == 422
    assert client.put(path, headers=ADMIN, json={'sample_percent': 0}).json()['sample_percent'] == 0
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json() == {'offered': False}
    assert client.put(path, headers=ADMIN, json={'sample_percent': 100}).json()['cooldown_seconds'] == 600
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()['offered'] is True
    assert state.review_live_next['expert'] > time.monotonic() + 590
    with state.review_store.connection() as db:
        rows = db.execute("SELECT actor_id,details FROM audit_events WHERE action='live.settings.update' ORDER BY rowid").fetchall()
    assert [json.loads(r['details'])['after_percent'] for r in rows] == [0, 100]
    assert {r['actor_id'] for r in rows} == {'admin'}


def test_d_current_behaviour_cooldown_blocks_second_offer_per_actor(client):
    state = configure(client)
    aid = client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()['assignment']['id']
    # Documented current behaviour: blocked right after the *offer*, whether or not it was answered.
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json() == {'offered': False}
    assert client.put(f'{PREFIX}/assignments/{aid}/judgment', headers=REVIEW, json={'decision': 'left'}).status_code == 200
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json() == {'offered': False}
    # Another actor has its own cooldown; the original user is only released after expiry.
    assert client.post(f'{PREFIX}/live-pairs', headers=OTHER, json=PAYLOAD).json()['offered'] is True
    state.review_live_next['expert'] = time.monotonic() - 1
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()['offered'] is True
    assert len(state.runner.calls) == 3


@pytest.mark.xfail(strict=True, reason='Known gap: cooldown starts when the invitation is made (reviews.py live_pair), not when the user submits the vote.')
def test_d_requirement_cooldown_counts_from_submission(client):
    state = configure(client)
    aid = client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()['assignment']['id']
    state.review_live_next['expert'] = time.monotonic() + 100  # the invitation was made ~500 s ago
    assert client.put(f'{PREFIX}/assignments/{aid}/judgment', headers=REVIEW, json={'decision': 'left'}).status_code == 200
    # Required: 600 s counted from the submission just made.
    assert state.review_live_next['expert'] - time.monotonic() > 590


@pytest.mark.xfail(strict=True, reason='Known gap: cooldown is spent even when generating answer B fails, so the user gets no invitation for 10 minutes.')
def test_d_requirement_failed_generation_does_not_spend_cooldown(client):
    state = configure(client)

    async def fail(*args):
        raise RuntimeError('model unavailable')

    good = state.runner.complete
    state.runner.complete = fail
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json() == {'offered': False}
    state.runner.complete = good
    assert client.post(f'{PREFIX}/live-pairs', headers=REVIEW, json=PAYLOAD).json()['offered'] is True
