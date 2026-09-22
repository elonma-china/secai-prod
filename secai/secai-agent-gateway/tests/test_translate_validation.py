"""HTTP-level contract tests for POST /ai/translate — TR4 (#9) + TR5 (#14b).

TR4: on the smoke deploy (:8132), a broken LLM config made every translate request come
back HTTP 200 with translated_text/translation == "" — a blank/whitespace-only completion
silently treated as a SUCCESSFUL response instead of a failure. Prod (:8131) never hit this
(18/18 OK there), but the contract gap is real: nothing in the code distinguished "the model
produced no translation" from "the model produced an empty (but valid) translation". Fixed in
routes.py's `translate_assemble()`/`render()`: a blank completion (after unmask+prompt-echo
strip) for a non-empty input now raises HTTP 502 instead of returning 200-empty.

TR5: source_lang="xx"/target_lang="zz" (or any other non-language garbage) used to flow
straight into the model prompt and come back HTTP 200 with the code silently ignored — the
caller had no signal the language selection wasn't honored. Fixed via an ISO-639-1 allow-list
in `_validate_translate_langs()`/`_validate_lang_code()` (routes.py): a code that isn't a real
language (and isn't None/"auto") now 400s. `vi`/`en` (the only codes prod/docs/tests use) and
"auto" (the FE's implicit default when the field is omitted) must keep working.

No live LLM is used — a scripted FakeRunner stands in for state.runner, exactly like
tests/test_gateway.py's FakeRunner. This only exercises the /ai/translate route's own
validation/error-mapping, not translation quality.
"""
from __future__ import annotations

import httpx
import pytest

from secai_gateway.app import create_app


class ScriptedRunner:
    """Same shape as test_gateway.py's FakeRunner — implements the `complete()` interface
    the non-stream endpoints use — but with a caller-controlled response so tests can force
    the "empty completion" (TR4) case without a real model."""

    def __init__(self, response: str = "bản dịch giả lập") -> None:
        self.response = response
        self.calls: list[tuple[str, str, str | None]] = []

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        self.calls.append((profile_name, prompt, session_id))
        return self.response, {"run_id": "run-test", "profile": profile_name}


async def _post(runner, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runner)),
                                 base_url="http://test") as client:
        return await client.post("/ai/translate", json=payload)


# ── TR4: empty completion must not 200 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_completion_returns_error_not_200_empty():
    """The exact smoke-observed shape: non-empty input, valid langs, but the model returns
    "" — must now be a real error status, never HTTP 200 with translated_text=""."""
    runner = ScriptedRunner(response="")
    resp = await _post(runner, {"text": "Cross-site scripting cho phép chèn mã độc.",
                                "source_lang": "vi", "target_lang": "en"})
    assert resp.status_code >= 500, resp.text
    assert resp.status_code != 200


@pytest.mark.asyncio
async def test_whitespace_only_completion_also_errors():
    # Whitespace-only is just as empty as "" for the caller — must not slip past a bare
    # falsy-string check.
    runner = ScriptedRunner(response="   \n  ")
    resp = await _post(runner, {"text": "Máy chủ bị lộ mật khẩu quản trị.", "target_lang": "en"})
    assert resp.status_code >= 500, resp.text


# ── success path: shape must be UNCHANGED ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_non_empty_completion_keeps_200_and_old_response_shape():
    runner = ScriptedRunner(response="Cross-site scripting allows injecting malicious code.")
    resp = await _post(runner, {"text": "Cross-site scripting cho phép chèn mã độc.",
                                "target_lang": "en"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # shape must be exactly the pre-existing contract: translated_text + translation
    # (same string in both) + metadata — nothing added/removed/renamed.
    assert set(body.keys()) == {"translated_text", "translation", "metadata"}
    assert body["translated_text"] == body["translation"]
    assert body["translated_text"].strip() != ""
    assert "allows injecting" in body["translated_text"]


# ── TR5: garbage lang codes must 400, valid ones must not be rejected ───────────────────

@pytest.mark.asyncio
async def test_garbage_target_lang_is_rejected():
    runner = ScriptedRunner()
    resp = await _post(runner, {"text": "Kẻ tấn công khai thác lỗ hổng.",
                                "source_lang": "vi", "target_lang": "zz"})
    assert resp.status_code == 400, resp.text
    assert runner.calls == []  # rejected before ever reaching the model


@pytest.mark.asyncio
async def test_garbage_source_lang_is_rejected():
    runner = ScriptedRunner()
    resp = await _post(runner, {"text": "An attacker exploits a vulnerability.",
                                "source_lang": "xx", "target_lang": "en"})
    assert resp.status_code == 400, resp.text
    assert runner.calls == []


@pytest.mark.parametrize("target_lang", ["en", "vi"])
@pytest.mark.asyncio
async def test_prod_used_target_langs_are_never_rejected(target_lang):
    # The only two codes the FE (TranslateScreen.tsx) and docs/api-guide.md actually send —
    # must keep working, unchanged, after the allow-list fix.
    runner = ScriptedRunner(response="translated")
    resp = await _post(runner, {"text": "Lỗ hổng bảo mật nghiêm trọng.", "target_lang": target_lang})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_source_and_target_vi_en_pair_not_rejected():
    runner = ScriptedRunner(response="translated")
    resp = await _post(runner, {"text": "Kẻ tấn công khai thác lỗ hổng.",
                                "source_lang": "vi", "target_lang": "en"})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_omitted_lang_fields_still_hit_auto_detect_branch():
    # FE's "auto" UI option omits the field entirely — must NOT be rejected/changed.
    runner = ScriptedRunner(response="translated")
    resp = await _post(runner, {"text": "Lỗ hổng bảo mật nghiêm trọng."})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("field", ["source_lang", "target_lang"])
@pytest.mark.asyncio
async def test_explicit_auto_sentinel_is_accepted(field):
    runner = ScriptedRunner(response="translated")
    resp = await _post(runner, {"text": "Lỗ hổng bảo mật nghiêm trọng.",
                                "target_lang": "en", field: "auto"})
    assert resp.status_code == 200, resp.text


# ── empty input: distinct 400, not a wasted model call / not a 200-empty ────────────────

@pytest.mark.asyncio
async def test_empty_input_text_is_400_and_never_calls_the_model():
    runner = ScriptedRunner()
    resp = await _post(runner, {"text": "  ", "target_lang": "en"})
    assert resp.status_code == 400, resp.text
    assert runner.calls == []
