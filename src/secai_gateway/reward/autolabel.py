"""Reward-model-assisted auto-labeling (2026-11-11): closes the human-review -> reward-model
-> more-training-data loop without touching chat latency or UX.

On a sampled fraction of ordinary chat turns (independent of the devspace "Nhận lời mời
đánh giá A/B" checkbox and its live-pair widget — this runs for ALL chat traffic, entirely
server-side), this:
  1. generates N-1 extra candidate answers to the same question, in parallel,
  2. scores all N candidates (the real answer + the extras) with the reward model,
  3. submits the best/worst pair as a pending item under a dedicated "reward-model-auto"
     reviewer identity — landing in the SAME "Chờ duyệt" queue a human-reviewed live pair
     would, with its own judgment already filled in.

The reward model never approves anything itself: an admin still has to adjudicate the item
(same as any other) before it counts toward a training batch. This is strictly a
fire-and-forget background job, scheduled only AFTER the chat response has already been
fully sent — every failure is swallowed, since a scoring hiccup must never surface to chat.
"""
from __future__ import annotations

import asyncio
import secrets
from uuid import uuid4

AUTO_REVIEWER_ID = 'reward-model-auto'
AUTO_REVIEWER_ROLE = 'reviewer'


def maybe_schedule(state, sid: str, user_text: str, final_answer: str) -> None:
    """Call right after a chat turn's response is fully built — never awaited, never
    raises. No-op unless both `review_enabled` and `reward_enabled` are on and the
    sampling roll hits; see `config.reward_autolabel_sample_rate`."""
    settings = state.settings
    if not getattr(settings, 'review_enabled', False) or not getattr(settings, 'reward_enabled', False):
        return
    rate = getattr(settings, 'reward_autolabel_sample_rate', 0.0)
    if rate <= 0 or secrets.randbelow(1_000_000) >= rate * 1_000_000:
        return
    if not (user_text or '').strip() or not (final_answer or '').strip():
        return
    task = asyncio.create_task(_run(state, sid, user_text, final_answer))
    # Keep a reference so the task isn't GC'd mid-flight (asyncio only holds a weak one);
    # drop it from the set once done regardless of outcome.
    if not hasattr(state, 'reward_autolabel_tasks'):
        state.reward_autolabel_tasks = set()
    state.reward_autolabel_tasks.add(task)
    task.add_done_callback(state.reward_autolabel_tasks.discard)


async def _run(state, sid: str, user_text: str, final_answer: str) -> None:
    try:
        from ..security import redact_public_input
        from .schema import ScoreInput
        from ..api.review_reward import score_with_reward_model, RewardUnavailable
        from ..api.review_auth import ReviewActor
        from ..api.review_schemas import ReviewItemInput
        from ..api.reviews import _generate_alternative

        settings = state.settings
        messages = [{'role': 'user', 'content': redact_public_input(user_text).text}]
        actor = ReviewActor(actor_id=AUTO_REVIEWER_ID, role=AUTO_REVIEWER_ROLE)
        n = max(2, min(4, getattr(settings, 'reward_autolabel_n', 3)))

        texts = [redact_public_input(final_answer).text]
        extra = await asyncio.gather(
            *[_generate_alternative(state, actor, messages) for _ in range(n - 1)],
            return_exceptions=True,
        )
        for t in extra:
            if isinstance(t, str) and t.strip():
                texts.append(redact_public_input(t).text)

        seen, unique_texts = set(), []
        for t in texts:
            if t.strip() and t not in seen:
                seen.add(t)
                unique_texts.append(t)
        if len(unique_texts) < 2:
            return

        score_payload = ScoreInput.model_validate({'prompt': messages, 'answers': unique_texts})
        try:
            result = await score_with_reward_model(state, score_payload)
        except RewardUnavailable:
            return
        scores = result['scores']
        ranked = sorted(zip(unique_texts, scores), key=lambda pair: pair[1], reverse=True)
        best_text, best_score = ranked[0]
        worst_text, worst_score = ranked[-1]
        if best_text == worst_text or best_score - worst_score < 1e-9:
            return  # no real preference signal — skip rather than fabricate one

        candidates = [
            {'id': 'cand-a', 'text': best_text, 'provenance': {'source': 'reward_autolabel', 'model': 'unknown'}},
            {'id': 'cand-b', 'text': worst_text, 'provenance': {'source': 'reward_autolabel', 'model': 'unknown'}},
        ]
        item = ReviewItemInput.model_validate({
            'external_id': str(uuid4()), 'prompt_messages': messages, 'candidates': candidates,
            'source_group': sid, 'evidence': '',
            'provenance': {
                'source': 'reward_autolabel', 'retrieved_context': 'unknown',
                'comparison': f'best-of-{len(unique_texts)}, reward margin={best_score - worst_score:.4f}',
            },
        })
        await asyncio.to_thread(state.review_store.create_autolabeled_item, item, 'cand-a', AUTO_REVIEWER_ID)
    except Exception:
        return  # best-effort only — must never surface to the chat response
