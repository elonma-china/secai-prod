"""The v2 translator must have scoped decoding without changing legacy calls."""
import json
from types import SimpleNamespace

import httpx
import pytest
from linch.types import ProviderRequest, SystemBlock
from openai import AsyncOpenAI


@pytest.mark.asyncio
async def test_translate_provider_sends_scoped_temperature_on_wire(monkeypatch):
    from secai_gateway.agent import providers

    monkeypatch.setattr(providers, "get_settings", lambda: SimpleNamespace(
        llm_provider="vllm", qwen_api_key="test", qwen_base_url="http://test/v1",
        qwen_context_window=32768, qwen_model="DuyTa/Cyber-F1-AWQ",
        llm_temperature=0.85, llm_top_p=0.95, llm_top_k=20,
        llm_min_p=0.0, llm_presence_penalty=1.1, llm_repetition_penalty=1.0,
    ))
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
                              headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        for override in (None, 0.0):
            provider, model = providers.build_llm_provider(temperature_override=override)
            provider._client = AsyncOpenAI(api_key="test", base_url="http://test/v1",
                                           http_client=http_client)
            request = ProviderRequest(model=model, system=[SystemBlock(text="test")],
                                      tools=[], messages=[])
            async for _ in provider.stream(request):
                pass
    assert [body["temperature"] for body in bodies] == [0.85, 0.0]
    assert all(body["model"] == "DuyTa/Cyber-F1-AWQ" for body in bodies)


@pytest.mark.asyncio
async def test_only_translate_v2_uses_dedicated_agent(monkeypatch):
    from secai_gateway.agent import runner as module
    from linch.events import ResultEvent
    from linch.types import Usage

    calls = []

    class Session:
        active_run_id = "test-run"
        meta = {}

        def __init__(self, name):
            self.name = name

        async def run(self, prompt, *args, **kwargs):
            calls.append((self.name, prompt))
            yield ResultEvent(subtype="success", stop_reason="end_turn",
                              total_usage=Usage(), duration_ms=0, final_text=self.name)

    class Agent:
        def __init__(self, name):
            self.name = name

        async def session(self, **kwargs):
            return Session(self.name)

    built = []

    def build_provider(*, temperature_override=None):
        built.append(temperature_override)
        return object(), "DuyTa/Cyber-F1-AWQ"

    monkeypatch.setattr(module, "build_llm_provider", build_provider)
    monkeypatch.setattr(module, "make_gateway_agent", lambda **kwargs: Agent("translate_v2"))
    scoped = module.LinchRunner.__new__(module.LinchRunner)
    scoped.strict_transform_errors = False
    scoped.agent = Agent("legacy")
    scoped._translation_agent = None
    scoped._session_locks = {}
    scoped.tools = {}
    scoped.session_store = object()
    scoped.run_store = object()
    scoped.ledger = object()

    assert (await scoped.complete("translate", "old"))[0] == "legacy"
    assert (await scoped.complete("translate_v2", "new"))[0] == "translate_v2"
    assert (await scoped.complete("translate_v2", "again"))[0] == "translate_v2"
    assert built == [0.0]
    assert calls == [("legacy", "old"), ("translate_v2", "new"),
                     ("translate_v2", "again")]


@pytest.mark.asyncio
async def test_translate_agent_has_isolated_sessions_and_no_tools(tmp_path):
    from secai_gateway.agent.runner import LinchRunner

    runner = LinchRunner(tmp_path / "gateway.db")
    translator = runner._agent_for("translate_v2")
    assert translator is not runner.agent
    assert translator.session_store is runner.agent.session_store

    old_session = await runner.agent.session(meta={"profile": "translate"})
    new_session = await translator.session(meta={"profile": "translate_v2"})
    assert old_session.id != new_session.id
    runner._apply_profile(new_session, "translate_v2", "source")
    assert new_session.tools_override.schemas() == []
    assert new_session.meta["profile"] == "translate_v2"
