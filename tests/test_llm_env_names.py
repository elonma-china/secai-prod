"""LLM endpoint settings: canonical LLM_* env names, legacy QWEN_* still accepted."""
from secai_gateway.config import Settings

_KEYS = ("BASE_URL", "API_KEY", "MODEL", "CONTEXT_WINDOW")


def _clear(monkeypatch):
    for k in _KEYS:
        monkeypatch.delenv(f"LLM_{k}", raising=False)
        monkeypatch.delenv(f"QWEN_{k}", raising=False)


def test_canonical_llm_names(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "k-new")
    monkeypatch.setenv("LLM_MODEL", "DuyTa/Cyber-F1-AWQ")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "65536")
    s = Settings(_env_file=None)
    assert (s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_context_window) == (
        "http://llm.invalid/v1", "k-new", "DuyTa/Cyber-F1-AWQ", 65536)


def test_legacy_qwen_names_still_work(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("QWEN_BASE_URL", "http://old.invalid/v1")
    monkeypatch.setenv("QWEN_MODEL", "old-model")
    monkeypatch.setenv("QWEN_CONTEXT_WINDOW", "4096")
    s = Settings(_env_file=None)
    assert (s.llm_base_url, s.llm_model, s.llm_context_window) == ("http://old.invalid/v1", "old-model", 4096)


def test_llm_name_wins_over_legacy(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("QWEN_MODEL", "old-model")
    monkeypatch.setenv("LLM_MODEL", "new-model")
    assert Settings(_env_file=None).llm_model == "new-model"


def test_kwargs_and_defaults(monkeypatch):
    _clear(monkeypatch)
    s = Settings(_env_file=None, llm_model="m")
    assert s.llm_model == "m" and s.llm_context_window == 32768
    assert "llm_api_key" not in repr(s)
