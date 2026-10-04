"""Guard the Devspace-only review shortcut without changing gateway auth."""

import importlib.util
import json
from pathlib import Path


def _server():
    path = Path(__file__).resolve().parents[1] / "secai-fe-devspace" / "serve_static.py"
    spec = importlib.util.spec_from_file_location("devspace_static_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_shortcut_requires_tailnet_on_both_ends():
    server = _server()
    assert server._tailnet_connection("100.92.150.53", "100.74.146.12")
    assert not server._tailnet_connection("192.168.1.4", "100.74.146.12")
    assert not server._tailnet_connection("100.92.150.53", "127.0.0.1")
    assert not server._tailnet_connection("invalid", "100.74.146.12")


def test_review_shortcut_loads_only_configured_roles(tmp_path, monkeypatch):
    server = _server()
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"admin": "admin-private", "reviewer": "reviewer-private"}))
    monkeypatch.setattr(server, "REVIEW_TOKENS_FILE", path)
    assert server._review_token("admin") == "admin-private"
    assert server._review_token("reviewer") == "reviewer-private"
    assert server._review_token("other") is None
    path.unlink()
    assert server._review_token("admin") is None
