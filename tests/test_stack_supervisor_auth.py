"""supervisor.healthy() gửi Basic auth khi container có OPENCODE_SERVER_PASSWORD; không có thì y như cũ."""
import base64
import importlib.util
import pathlib

_SRC = pathlib.Path(__file__).resolve().parent.parent / "stack/images/pentestcode-vm/supervisor.py"


def _load():
    spec = importlib.util.spec_from_file_location("vm_supervisor", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    status = 200


def _run(monkeypatch, env):
    for k in ("OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sup, seen = _load(), {}

    def fake_urlopen(req, timeout=None):
        seen["req"] = req
        return _Resp()

    monkeypatch.setattr(sup.urllib.request, "urlopen", fake_urlopen)
    assert sup.healthy(4101) is True
    return seen["req"]


def _auth(req):
    return req.get_header("Authorization") if hasattr(req, "get_header") else None


def test_no_password_sends_no_auth(monkeypatch):
    assert _auth(_run(monkeypatch, {})) is None


def test_password_sends_basic_auth(monkeypatch):
    req = _run(monkeypatch, {"OPENCODE_SERVER_PASSWORD": "s3cret"})
    assert _auth(req) == "Basic " + base64.b64encode(b"ias:s3cret").decode()
    assert req.full_url == "http://127.0.0.1:4101/global/health"


def test_username_override(monkeypatch):
    req = _run(monkeypatch, {"OPENCODE_SERVER_PASSWORD": "p", "OPENCODE_SERVER_USERNAME": "bob"})
    assert _auth(req) == "Basic " + base64.b64encode(b"bob:p").decode()
