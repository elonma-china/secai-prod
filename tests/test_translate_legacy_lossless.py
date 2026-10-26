"""Legacy /ai/translate(+/stream) must NEVER alter, redact or delete source information.

Offline: a scripted runner stands in for the model. Covers nested placeholders, the
completeness check + retry + 502 TRANSLATION_INTEGRITY, widened credential detection,
CJK/scrubber bypass, echo-strip ordering, placeholder collision, whole-text masking.
"""
from __future__ import annotations

import json
import re

import httpx
import pytest

from secai_gateway.api.literal_mask import (
    PlaceholderIntegrityError, mask_literals, restore_checked, unmask_literals,
)
from secai_gateway.app import create_app
from secai_gateway.api.text_chunk import split_for_translation


def _roundtrip(raw: str) -> tuple[str, list[str]]:
    masked, lits = mask_literals(raw)
    assert unmask_literals(masked, lits) == raw
    assert restore_checked(masked, lits) == raw
    return masked, lits


# ── (1) nested placeholders, URL query credentials, fenced code ───────────────────
def test_fenced_code_credentials_restore_verbatim():
    raw = "```\npassword=Abc123\nuser=root\n```"
    masked, lits = _roundtrip(raw)
    assert "Abc123" not in masked and "root" not in masked
    assert lits == [raw]


def test_url_query_credentials_not_swallowed():
    raw = "Truy cập https://x.com/login?user=admin&password=Abc@123 để đăng nhập"
    masked, lits = _roundtrip(raw)
    assert "Abc@123" not in masked and "admin" not in masked
    assert masked.endswith("để đăng nhập")  # trailing prose is NOT part of the secret


def test_no_orphan_literals_and_dense_ids():
    masked, lits = mask_literals("```\npassword=Abc123\n``` rồi 10.0.0.5")
    ids = [int(i) for i in re.findall(r"⟦L(\d+)⟧", masked)]
    assert ids == list(range(len(lits)))


# ── (2) completeness check ────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "mật khẩu: , IP ⟦L1⟧",                  # dropped
    "mật khẩu: ⟦L0⟧ ⟦L0⟧ IP ⟦L1⟧",          # duplicated
    "mật khẩu: ⟦L0⟧ IP ⟦L1⟧ ⟦L9⟧",          # invented
    "mật khẩu: ⟦L9⟧ IP ⟦L1⟧",               # renumbered
    "mật khẩu: ⟦L0⟧ IP ⟦L1",                # stray bracket
])
def test_incomplete_output_is_rejected(bad):
    masked, lits = mask_literals("mật khẩu: Abc@123\nIP 10.0.0.5")
    assert len(lits) == 2
    with pytest.raises(PlaceholderIntegrityError):
        restore_checked(bad, lits)


@pytest.mark.parametrize("variant", ["[L0]", "<L0>", "［L0］", "〈L0〉", "⟨L0⟩", "⟦ L 0 ⟧", "《L0》"])
def test_bracket_variants_are_accepted(variant):
    lits = ["Abc@123"]
    assert restore_checked(f"Password: {variant}", lits) == "Password: Abc@123"


def test_reordering_is_allowed():
    assert restore_checked("⟦L1⟧ then ⟦L0⟧", ["a", "b"]) == "b then a"


# ── helpers for endpoint tests ────────────────────────────────────────────────────
class ScriptedRunner:
    """complete() returns outputs from ``script`` (callable(prompt, n) -> str)."""

    def __init__(self, script):
        self.script = script
        self.calls: list[str] = []

    async def complete(self, profile_name, prompt, session_id=None, **kw):
        self.calls.append(prompt)
        return self.script(prompt, len(self.calls)), {"run_id": "r", "profile": profile_name}

    async def stream(self, profile_name, prompt, session_id=None, **kw):  # pragma: no cover
        raise AssertionError("translate must not use the token stream")
        yield


def _payload(prompt: str) -> str:
    m = re.search(r"<untrusted_retrieved_content[^>]*>\n(.*)\n</untrusted_retrieved_content>", prompt, re.S)
    return m.group(1) if m else prompt


def _identity(prompt, n):
    return _payload(prompt)


async def _post(runner, path, payload):
    app = create_app(runner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(path, json=payload)


@pytest.mark.asyncio
async def test_drop_then_retry_succeeds():
    def script(prompt, n):
        out = _payload(prompt)
        return out.replace("⟦L1⟧", "") if n == 1 else out
    r = ScriptedRunner(script)
    resp = await _post(r, "/ai/translate", {"text": "Tài khoản: root\nMật khẩu: Abc@123", "target_lang": "en"})
    assert resp.status_code == 200
    assert "Abc@123" in resp.json()["translated_text"] and "root" in resp.json()["translated_text"]
    assert len(r.calls) == 2 and "LƯU Ý BẮT BUỘC" in r.calls[1]


@pytest.mark.asyncio
async def test_persistent_loss_is_502_integrity_not_silent_200():
    r = ScriptedRunner(lambda p, n: _payload(p).replace("⟦L1⟧", ""))
    resp = await _post(r, "/ai/translate", {"text": "Tài khoản: root\nMật khẩu: Abc@123", "target_lang": "en"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "TRANSLATION_INTEGRITY"
    assert len(r.calls) == 2


@pytest.mark.asyncio
async def test_stream_integrity_failure_is_explicit_error_frame():
    r = ScriptedRunner(lambda p, n: _payload(p).replace("⟦L1⟧", ""))
    resp = await _post(r, "/ai/translate/stream", {"text": "Tài khoản: root\nMật khẩu: Abc@123", "target_lang": "en"})
    assert resp.status_code == 200
    assert "TRANSLATION_INTEGRITY" in resp.text and "event: error" in resp.text
    finals = [json.loads(b.split("data: ", 1)[1]) for b in resp.text.split("\n\n") if b.startswith("event: final")]
    assert finals and finals[0]["translated_text"] == ""


@pytest.mark.asyncio
async def test_stream_happy_path_restores_everything_and_uses_assembled_path():
    r = ScriptedRunner(_identity)
    resp = await _post(r, "/ai/translate/stream", {"text": "Mật khẩu: Abc@123 tại 10.0.0.5", "target_lang": "en"})
    assert resp.status_code == 200
    assert "Abc@123" in resp.text and "10.0.0.5" in resp.text and "⟦L" not in resp.text


@pytest.mark.asyncio
async def test_temperature_zero_agent_for_translate():
    from secai_gateway.agent import runner as runner_mod
    import inspect
    assert '"translate"' in inspect.getsource(runner_mod.LinchRunner._agent_for)


# ── (3) widened credential detection (all must be masked AND round-trip) ──────────
@pytest.mark.parametrize("raw,secret", [
    ("Mật khẩu là Abc@123", "Abc@123"),
    ("The password is Abc@123", "Abc@123"),
    ("pass Abc@123", "Abc@123"),
    ("passwd Abc@123", "Abc@123"),
    ("Pwd - Abc@123", "Abc@123"),
    ("mk: Abc@123", "Abc@123"),
    ('{"password": "Abc@123", "user": "root"}', "Abc@123"),
    ('{"password": "Abc@123", "user": "root"}', "root"),
    ("DB_PASSWORD=Abc@123", "Abc@123"),
    ("db_password: Abc@123", "Abc@123"),
    ("MẬT KHẨU：Abc@123", "Abc@123"),
    ("密码：Abc@123", "Abc@123"),
    ("mysql -u root -pSecr3t!", "Secr3t!"),
    ("pw Hunter2!", "Hunter2!"),
    ("Mật khẩu của root là 'P@ss w0rd'", "P@ss w0rd"),
    ("mật khẩu: Abc 123 xyz", "Abc 123 xyz"),
    ("Liên hệ bob.smith@example.com", "bob.smith@example.com"),
    ("Gọi +84 912 345 678 ngay", "+84 912 345 678"),
    ("postgres://app:S3cret@db.internal:5432/prod", "postgres://app:S3cret@db.internal:5432/prod"),
    ("user:Sup3rPw@db.internal", "user:Sup3rPw@db.internal"),
])
def test_widened_credentials_masked_and_lossless(raw, secret):
    masked, lits = _roundtrip(raw)
    assert any(secret in lit for lit in lits), (masked, lits)
    assert secret not in masked


def test_two_labelled_credentials_on_one_line_stay_separate():
    masked, lits = _roundtrip("mật khẩu = hunter2, tài khoản = root")
    assert "hunter2" in lits and "root" in lits


def test_plain_prose_is_left_alone():
    masked, lits = mask_literals("Please pass the salt and review the report.")
    assert lits == [] and masked == "Please pass the salt and review the report."


# ── (4)+(5) CJK kept, no scrubbers, echo-strip rules ──────────────────────────────
@pytest.mark.asyncio
async def test_cjk_and_scrubbed_phrases_survive_for_non_cjk_target():
    out_text = ("Xin chào 张伟 và 北京\nKết luận về mục 2: mật khẩu root là x\n"
                "Gọi qdrant_search(query='x') <think>ghi chú</think> <script>alert(1)</script> onerror=1")
    r = ScriptedRunner(lambda p, n: out_text)
    for path in ("/ai/translate", "/ai/translate/stream"):
        resp = await _post(r, path, {"text": "hello", "target_lang": "vi"})
        assert resp.status_code == 200
        body = resp.text if path.endswith("stream") else resp.json()["translated_text"]
        body = body.encode().decode("unicode_escape") if False else body
        for frag in ("张伟", "北京", "Kết luận về mục 2", "qdrant_search(query='x')",
                     "<script>alert(1)</script>", "onerror=1"):
            assert frag in body or json.dumps(frag, ensure_ascii=False)[1:-1] in body, (path, frag)
        assert "&lt;script" not in body and "\u200b" not in body


@pytest.mark.asyncio
async def test_echo_strip_does_not_eat_source_phrase_or_restored_literals():
    src = "Dịch văn bản sau đây giúp tôi: user=root\npassword=x\nHết"
    r = ScriptedRunner(_identity)
    resp = await _post(r, "/ai/translate", {"text": src, "target_lang": "en"})
    assert resp.json()["translated_text"] == src


@pytest.mark.asyncio
async def test_echo_head_still_stripped_when_not_in_source():
    # the model echoing the instruction head (not in the source) is removed, literals kept
    r = ScriptedRunner(lambda p, n: p)  # echoes the whole prompt like the FakeRunner
    resp = await _post(r, "/ai/translate", {"text": "Mật khẩu: Abc@123", "target_lang": "en"})
    out = resp.json()["translated_text"]
    assert "Abc@123" in out and "Dịch văn bản sau" not in out and "untrusted_retrieved" not in out


# ── (6) collision with placeholder-looking source text ────────────────────────────
def test_source_placeholder_lookalikes_are_preserved():
    raw = "Giá trị ⟦L0⟧ và [L1] và mật khẩu: Abc@123 và ⟦ lẻ"
    masked, lits = _roundtrip(raw)
    assert "⟦L0⟧" in lits and "[L1]" in lits and any("Abc@123" in x for x in lits)
    # the model-visible text carries only OUR dense placeholders
    ids = [int(i) for i in re.findall(r"⟦L(\d+)⟧", masked)]
    assert ids == list(range(len(lits)))


@pytest.mark.asyncio
async def test_endpoint_lookalike_source_roundtrips():
    src = "Giá trị ⟦L0⟧ và mật khẩu: Abc@123"
    resp = await _post(ScriptedRunner(_identity), "/ai/translate", {"text": src, "target_lang": "en"})
    assert resp.status_code == 200 and resp.json()["translated_text"] == src


# ── (7) whole-text masking before chunking ────────────────────────────────────────
def test_hard_split_never_cuts_inside_placeholder():
    text = "a" * 95 + "⟦L12⟧" + "b" * 50
    pieces = split_for_translation(text, 100)
    assert "".join(c + s for c, s in pieces) == text
    for c, _ in pieces:
        assert c.count("⟦") == c.count("⟧")


@pytest.mark.asyncio
async def test_label_value_split_across_chunk_boundary_stays_masked():
    value = "Abc@123456789XYZ"
    text = "x" * 790 + "\n\nMật khẩu:\n\n" + value + "\n\n" + "y" * 900
    r = ScriptedRunner(_identity)
    resp = await _post(r, "/ai/translate", {"text": text, "target_lang": "en"})
    assert resp.status_code == 200
    assert resp.json()["translated_text"] == text
    assert all(value not in p for p in r.calls)


@pytest.mark.asyncio
async def test_long_input_with_credentials_roundtrips_across_chunks():
    paras = [f"Đoạn {i}: máy chủ 10.0.0.{i} mật khẩu: Secret{i}Pass " + "văn bản " * 60 for i in range(30)]
    text = "\n\n".join(paras)
    r = ScriptedRunner(_identity)
    resp = await _post(r, "/ai/translate", {"text": text, "target_lang": "en"})
    assert resp.status_code == 200
    assert len(r.calls) >= 2
    assert resp.json()["translated_text"] == text
    assert all("Secret" not in p for p in r.calls)


def test_url_literal_excludes_trailing_punctuation():
    from secai_gateway.api.literal_mask import mask_literals, unmask_literals
    src = "Tại https://portal.example.com/login?next=/admin, hãy đổi mật khẩu. Xem http://a.b/c."
    masked, lits = mask_literals(src)
    assert "https://portal.example.com/login?next=/admin" in lits
    assert "http://a.b/c" in lits
    assert not any(l.endswith((",", ".", ";", ":")) for l in lits)
    assert masked.count("),") == 0 and ", hãy" in masked
    assert unmask_literals(masked, lits) == src
