"""Legacy /ai/translate must not strip Han ideographs when the TARGET is zh/ja.

Regression for TXT-DIRECT-vi-ja / TXT-DIRECT-vi-zh-Hans (ias.id.vn, 2026-10-09, still
failing after the v3 guard 5493394). The IAS text panel calls the legacy
``POST /api/ais/translate`` -> ``/ai/translate`` (reads ``e.translation``), not
``/ai/v3/translate``. That path runs ``complete()`` -> ``sanitize_output_language()``
-> ``remove_chinese_content()``, a chat-oriented "VI/EN-only" filter that deletes every
CJK ideograph. Observed output kept only kana, CJK punctuation, Latin and digits
("がネットワークシステムになをした", "，：、、、。，、Web ，，。").
"""
from __future__ import annotations

import httpx
import pytest

from secai_gateway.app import create_app

JA = "インシデント対応チームは、企業のネットワークシステムで異常を検出した。"
ZH = "事件响应团队在企业网络系统中发现了异常。"
SOURCE = "Đội ứng cứu sự cố phát hiện bất thường trên hệ thống mạng của doanh nghiệp."


class ScriptedRunner:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        return self.response, {"run_id": "run-test", "profile": profile_name}


async def _post(runner, path: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runner)),
                                 base_url="http://test") as client:
        return await client.post(path, json=payload)


# "zh-Hans" reaches this endpoint as "zh" via translate_langs()' alias; the alias is
# tested where it is defined, so these cases use the normalized code.
@pytest.mark.parametrize("target,output", [("ja", JA), ("zh", ZH)],
                         ids=["TXT-DIRECT-vi-ja", "TXT-DIRECT-vi-zh-Hans"])
@pytest.mark.parametrize("field", ["target_lang", "language"])
@pytest.mark.asyncio
async def test_cjk_target_keeps_han_ideographs(target, output, field):
    resp = await _post(ScriptedRunner(output), "/ai/translate",
                       {"text": SOURCE, "source_lang": "vi", field: target})
    assert resp.status_code == 200, resp.text
    assert resp.json()["translation"] == output
    assert resp.json()["translated_text"] == output


@pytest.mark.parametrize("target,output", [("ja", JA), ("zh", ZH)], ids=["ja", "zh"])
@pytest.mark.asyncio
async def test_cjk_target_stream_final_keeps_han_ideographs(target, output):
    resp = await _post(ScriptedRunner(output), "/ai/translate/stream",
                       {"text": SOURCE, "source_lang": "vi", "target_lang": target})
    assert resp.status_code == 200, resp.text
    assert output in resp.text  # token + final frames carry the untouched translation


@pytest.mark.asyncio
async def test_non_cjk_target_still_strips_leaked_han():
    resp = await _post(ScriptedRunner("Phát hiện 异常 trên mạng."), "/ai/translate",
                       {"text": "Anomaly detected on the network.", "source_lang": "en",
                        "target_lang": "vi"})
    assert resp.status_code == 200, resp.text
    assert "异常" not in resp.json()["translation"]
