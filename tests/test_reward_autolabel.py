"""Reward-model-assisted auto-labeling (reward/autolabel.py): background best-of-N that
turns a sampled chat turn into a pending item under the 'reward-model-auto' reviewer —
never blocks the chat response, never approves itself, no-op unless explicitly enabled.
"""
from types import SimpleNamespace

import httpx
import pytest

from test_review_auth import client, ADMIN
from secai_gateway.reward import autolabel


class FakeRunner:
    """Each call returns the next queued answer (or the last one once exhausted)."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        async def delete(sid):
            pass
        self.session_store = SimpleNamespace(delete=delete)

    async def complete(self, profile, prompt, session_id=None):
        self.calls.append((profile, prompt, session_id))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer, {'model': 'fake'}


def configure(client, *, answers=('Alt 1', 'Alt 2'), sample_rate=1.0, n=3, reward_enabled=True, review_enabled=True):
    state = client.app.state.gateway
    state.settings.review_enabled = review_enabled
    state.settings.reward_enabled = reward_enabled
    state.settings.reward_autolabel_sample_rate = sample_rate
    state.settings.reward_autolabel_n = n
    state.settings.review_live_timeout_seconds = 1
    state.settings.reward_service_url = 'http://reward.internal'
    state.settings.reward_service_token = 'upstream-token'
    state.settings.reward_artifact_sha256 = 'a' * 64
    state.settings.reward_timeout_seconds = 1
    state.runner = FakeRunner(answers)
    return state


def mock_scores(state, scores_by_answer):
    """scores_by_answer: dict[text] -> score. Any text not listed scores 0."""
    def handler(request):
        import json
        body = json.loads(request.content)
        scores = [scores_by_answer.get(a, 0.0) for a in body['answers']]
        return httpx.Response(200, json={'artifact_id': 'a' * 64, 'scores': scores})
    state.reward_transport = httpx.MockTransport(handler)


def pending_items(client):
    return client.get('/review-api/overview', headers=ADMIN, params={'status': 'pending'}).json()['items']


async def test_disabled_by_default_schedules_nothing(client):
    state = configure(client, sample_rate=0)
    mock_scores(state, {})
    before = len(pending_items(client))
    autolabel.maybe_schedule(state, 'sess-1', 'Câu hỏi?', 'Câu trả lời thật.')
    assert not getattr(state, 'reward_autolabel_tasks', set())
    assert len(pending_items(client)) == before


async def test_reward_disabled_schedules_nothing(client):
    state = configure(client, reward_enabled=False)
    mock_scores(state, {})
    autolabel.maybe_schedule(state, 'sess-1', 'Câu hỏi?', 'Câu trả lời thật.')
    assert not getattr(state, 'reward_autolabel_tasks', set())


async def test_best_of_n_creates_a_pending_item_with_the_reward_models_pick(client):
    # 3 candidates total (the real answer + 2 generated extras) scored 0.1 / 1.0 / 5.0 —
    # only the two EXTREMES (best, worst) become the pair; the middle one is dropped.
    state = configure(client, answers=['Weaker candidate', 'Middling candidate'])
    mock_scores(state, {'Real answer': 5.0, 'Weaker candidate': 0.1, 'Middling candidate': 1.0})
    before = {i['id'] for i in pending_items(client)}
    await autolabel._run(state, 'sess-1', 'Câu hỏi thật?', 'Real answer')
    after = pending_items(client)
    new_items = [i for i in after if i['id'] not in before]
    assert len(new_items) == 1
    detail = client.get(f"/review-api/overview/{new_items[0]['id']}", headers=ADMIN).json()
    candidates = {c['text']: c['id'] for c in detail['item']['candidates']}
    assert set(candidates) == {'Real answer', 'Weaker candidate'}  # the middling one never surfaces
    assert detail['judgments'][0]['reviewer_id'] == autolabel.AUTO_REVIEWER_ID
    winner_id = detail['judgments'][0]['winning_candidate_id']
    assert candidates['Real answer'] == winner_id


async def test_no_preference_signal_creates_nothing(client):
    # Every candidate collapses to the same text once deduplicated -> nothing to compare.
    state = configure(client, answers=['Real answer'])
    mock_scores(state, {'Real answer': 1.0})
    before = len(pending_items(client))
    await autolabel._run(state, 'sess-1', 'Câu hỏi?', 'Real answer')
    assert len(pending_items(client)) == before


async def test_reward_service_failure_is_swallowed(client):
    state = configure(client, answers=['Weaker', 'Stronger'])
    def handler(request):
        return httpx.Response(503)
    state.reward_transport = httpx.MockTransport(handler)
    before = len(pending_items(client))
    await autolabel._run(state, 'sess-1', 'Câu hỏi?', 'Real answer')  # must not raise
    assert len(pending_items(client)) == before


async def test_admin_still_has_to_approve_before_it_affects_eligibility(client):
    state = configure(client, answers=['Weaker candidate', 'Stronger candidate'])
    mock_scores(state, {'Real answer': 1.0, 'Weaker candidate': 0.1, 'Stronger candidate': 5.0})
    before = client.get('/review-api/reward/training/eligibility', headers=ADMIN, params={'purpose': 'production'}).json()
    await autolabel._run(state, 'sess-1', 'Câu hỏi?', 'Real answer')
    after = client.get('/review-api/reward/training/eligibility', headers=ADMIN, params={'purpose': 'production'}).json()
    assert after['valid_new_count'] == before['valid_new_count']  # pending, not yet adjudicated
