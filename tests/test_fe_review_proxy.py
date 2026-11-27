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


def test_review_shortcut_accepts_configured_loopback_tunnel(monkeypatch, tmp_path):
    server = _server()
    monkeypatch.setattr(server, 'REVIEW_LOOPBACK_FILE', tmp_path / 'missing')
    monkeypatch.setattr(server, 'REVIEW_ALLOW_LOOPBACK', True)
    assert server._review_connection('127.0.0.1', '127.0.0.1')
    assert not server._review_connection('192.168.1.4', '127.0.0.1')
    monkeypatch.setattr(server, 'REVIEW_ALLOW_LOOPBACK', False)
    assert not server._review_connection('127.0.0.1', '127.0.0.1')


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


def test_tailnet_shortcut_is_opt_in(monkeypatch, tmp_path):
    """Nhánh tin tailnet chỉ bật khi DEVSPACE_REVIEW_ALLOW_TAILNET=true; nhánh loopback giữ điều kiện file marker."""
    server = _server()
    monkeypatch.setattr(server, "REVIEW_LOOPBACK_FILE", tmp_path / "missing")
    monkeypatch.setattr(server, "REVIEW_ALLOW_LOOPBACK", False)
    monkeypatch.setattr(server, "REVIEW_ALLOW_TAILNET", False)
    assert not server._review_connection("100.92.150.53", "100.74.146.12")
    assert not server._review_connection("127.0.0.1", "127.0.0.1")
    monkeypatch.setattr(server, "REVIEW_ALLOW_TAILNET", True)
    assert server._review_connection("100.92.150.53", "100.74.146.12")
    assert not server._review_connection("192.168.1.4", "100.74.146.12")
    assert not server._review_connection("127.0.0.1", "127.0.0.1")  # tailnet bật không mở loopback
    marker = tmp_path / ".review-loopback.local"
    marker.write_text("")
    monkeypatch.setattr(server, "REVIEW_LOOPBACK_FILE", marker)
    monkeypatch.setattr(server, "REVIEW_ALLOW_TAILNET", False)
    assert server._review_connection("127.0.0.1", "127.0.0.1")  # loopback chỉ cần file marker
    assert not server._review_connection("100.92.150.53", "100.74.146.12")


def test_tailnet_flag_reads_env(monkeypatch):
    monkeypatch.delenv("DEVSPACE_REVIEW_ALLOW_TAILNET", raising=False)
    assert _server().REVIEW_ALLOW_TAILNET is False
    monkeypatch.setenv("DEVSPACE_REVIEW_ALLOW_TAILNET", "true")
    assert _server().REVIEW_ALLOW_TAILNET is True
