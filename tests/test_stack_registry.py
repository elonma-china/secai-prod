"""stack/lib/registry.py: hợp đồng mã thoát mà publish.sh dựa vào để quyết định có push hay không.

0 có; 1 không có quyền (401/403); 2 lỗi mạng/định dạng/429/5xx; 3 chỉ khi manifest 404 lúc đã đọc được (tag chưa có).
hub-repo trả private/public theo is_private (gate "chỉ push dữ liệu vào repo private"). Không mạng thật: _open bị thay.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "stack" / "lib" / "registry.py"
DG = "sha256:" + "ab" * 32
REALM = 'Bearer realm="https://auth.example/token",service="registry.example",scope="repository:o/r:pull"'


class Resp(io.BytesIO):
    def __init__(self, body: bytes | dict | list = b"", headers: dict | None = None):
        super().__init__(json.dumps(body).encode() if isinstance(body, (dict, list)) else body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(url: str, code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"HTTP {code}", headers or {}, io.BytesIO(b""))


@pytest.fixture
def reg(monkeypatch):
    """Module mới cho mỗi test (AUTH/_JWT là trạng thái cấp module), không ghi .pyc vào stack/lib."""
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("registry_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.calls = []

    def route(fn):
        def _open(req):
            mod.calls.append((req.get_method(), req.full_url, req.get_header("Authorization")))
            return fn(req)
        monkeypatch.setattr(mod, "_open", _open)
    mod.route = route
    return mod


def run(reg, capsys, *args) -> tuple[int, str, str]:
    rc = reg.main(["registry.py", *args])
    out = capsys.readouterr()
    return rc, out.out.strip(), out.err


def token_file(tmp_path: Path, mode: int = 0o600) -> str:
    p = tmp_path / "hub.token"
    p.write_text("dckr_pat_FAKEtoken\n")
    p.chmod(mode)
    return f"ns:{p}"


# ───────────── digest (Registry HTTP API v2) ─────────────
def test_digest_anonymous_200(reg, capsys):
    reg.route(lambda req: Resp(headers={"Docker-Content-Digest": DG}))
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t") == (0, DG, "")
    assert reg.calls == [("HEAD", "https://ghcr.io/v2/o/r/manifests/t", None)]


def test_digest_token_flow_then_200(reg, capsys):
    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
            assert q == {"scope": ["repository:o/r:pull"], "service": ["registry.example"]}
            return Resp({"token": "T0K"})
        if req.get_header("Authorization") != "Bearer T0K":
            raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
        return Resp(headers={"Docker-Content-Digest": DG})
    reg.route(fn)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[:2] == (0, DG)


def test_digest_404_after_auth_is_3(reg, capsys):
    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            return Resp({"access_token": "T0K"})
        if not req.get_header("Authorization"):
            raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
        raise http_error(req.full_url, 404)
    reg.route(fn)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 3


def test_digest_404_anonymous_registry_is_3(reg, capsys):
    reg.route(lambda req: (_ for _ in ()).throw(http_error(req.full_url, 404)))
    assert run(reg, capsys, "digest", "registry.example/o/r:t")[0] == 3


@pytest.mark.parametrize("code,want", [(401, 1), (403, 1), (429, 2), (500, 2), (503, 2)])
def test_digest_token_endpoint_errors(reg, capsys, code, want):
    # chỉ 401/403 ở token endpoint là "không có quyền"; 429/5xx là lỗi tạm, KHÔNG được hiểu là "tag chưa có" (3)
    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            raise http_error(req.full_url, code)
        raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
    reg.route(fn)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == want


@pytest.mark.parametrize("code,want", [(401, 1), (403, 1), (429, 2), (500, 2), (502, 2)])
def test_digest_manifest_errors_after_token(reg, capsys, code, want):
    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            return Resp({"token": "T0K"})
        if not req.get_header("Authorization"):
            raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
        raise http_error(req.full_url, code)
    reg.route(fn)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == want


@pytest.mark.parametrize("code", [429, 500, 503])
def test_digest_manifest_errors_anonymous(reg, capsys, code):
    reg.route(lambda req: (_ for _ in ()).throw(http_error(req.full_url, code)))
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 2


@pytest.mark.parametrize("www", ["", 'Bearer realm="http://auth.example/token"', 'Basic realm="x"'])
def test_digest_bad_or_missing_challenge_is_2(reg, capsys, www):
    reg.route(lambda req: (_ for _ in ()).throw(http_error(req.full_url, 401, {"WWW-Authenticate": www} if www else {})))
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 2
    assert not any(u.startswith("http://") for _, u, _ in reg.calls)   # không bao giờ xin token qua http


@pytest.mark.parametrize("body", [{"token": ""}, {}])
def test_digest_empty_token_is_1(reg, capsys, body):
    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            return Resp(body)
        raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
    reg.route(fn)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 1


@pytest.mark.parametrize("resp", [Resp(headers={}), Resp(headers={"Docker-Content-Digest": "sha256:xyz"})])
def test_digest_missing_or_bad_header_is_2(reg, capsys, resp):
    reg.route(lambda req: resp)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 2


def test_digest_token_endpoint_odd_json_is_2(reg, capsys):
    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            return Resp(["not", "a", "dict"])
        raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
    reg.route(fn)
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 2


def test_digest_network_error_is_2(reg, capsys):
    reg.route(lambda req: (_ for _ in ()).throw(urllib.error.URLError("no route")))
    assert run(reg, capsys, "digest", "ghcr.io/o/r:t")[0] == 2


@pytest.mark.parametrize("ref,url", [
    ("qdrant/qdrant:v1.10.1", "https://registry-1.docker.io/v2/qdrant/qdrant/manifests/v1.10.1"),
    ("redis:7-alpine", "https://registry-1.docker.io/v2/library/redis/manifests/7-alpine"),
    ("docker.io/ns/secai-data:2026.11.27", "https://registry-1.docker.io/v2/ns/secai-data/manifests/2026.11.27"),
    ("docker.elastic.co/elasticsearch/elasticsearch:8.15.3",
     "https://docker.elastic.co/v2/elasticsearch/elasticsearch/manifests/8.15.3"),
])
def test_digest_ref_normalisation(reg, capsys, ref, url):
    reg.route(lambda req: Resp(headers={"Docker-Content-Digest": DG}))
    assert run(reg, capsys, "digest", ref)[0] == 0
    assert reg.calls[0][1] == url


@pytest.mark.parametrize("ref", ["ghcr.io/o/r@" + DG, "ghcr.io/O/R:t", "ghcr.io/o/r:t;rm"])
def test_digest_bad_ref_is_2_without_network(reg, capsys, ref):
    reg.route(lambda req: pytest.fail("không được gọi mạng"))
    assert run(reg, capsys, "digest", ref)[0] == 2


def test_digest_with_auth_sends_basic_to_token_endpoint(reg, capsys, tmp_path):
    seen = {}

    def fn(req):
        if req.full_url.startswith("https://auth.example/"):
            seen["auth"] = req.get_header("Authorization")
            return Resp({"token": "T0K"})
        if not req.get_header("Authorization"):
            raise http_error(req.full_url, 401, {"WWW-Authenticate": REALM})
        return Resp(headers={"Docker-Content-Digest": DG})
    reg.route(fn)
    rc, out, err = run(reg, capsys, "--auth", token_file(tmp_path), "digest", "docker.io/ns/secai-data:2026.11.27")
    assert rc == 0 and out == DG
    assert seen["auth"].startswith("Basic ") and "dckr_pat_FAKEtoken" not in out + err


# ───────────── --auth ─────────────
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_auth_file_must_be_private(reg, capsys, tmp_path, mode):
    reg.route(lambda req: pytest.fail("không được gọi mạng"))
    rc, _, err = run(reg, capsys, "--auth", token_file(tmp_path, mode), "hub-repo", "ns/secai-data")
    assert rc == 2 and "600/400" in err


def test_auth_bad_spec_and_empty_file(reg, capsys, tmp_path):
    reg.route(lambda req: pytest.fail("không được gọi mạng"))
    assert run(reg, capsys, "--auth", "no-colon", "hub-repo", "ns/x")[0] == 2
    p = tmp_path / "empty.token"; p.write_text("\n"); p.chmod(0o600)
    assert run(reg, capsys, "--auth", f"ns:{p}", "hub-repo", "ns/x")[0] == 2
    assert run(reg, capsys, "--auth", f"ns:{tmp_path / 'missing'}", "hub-repo", "ns/x")[0] == 2


def test_usage_errors_are_2(reg, capsys):
    reg.route(lambda req: pytest.fail("không được gọi mạng"))
    assert run(reg, capsys)[0] == 2
    assert run(reg, capsys, "tags", "ghcr.io/o/r")[0] == 2   # lệnh cũ không dùng đã bỏ
    assert run(reg, capsys, "digest")[0] == 2
    assert run(reg, capsys, "hub-delete-tag", "ns/r")[0] == 2


# ───────────── Docker Hub API ─────────────
def hub_router(repo_body=None, pages=None, login_code=None, repo_code=None):
    logins = []

    def fn(req):
        path = urllib.parse.urlsplit(req.full_url).path
        if req.get_method() == "POST" and path in ("/v2/auth/token", "/v2/users/login"):
            logins.append(path)
            if login_code:
                raise http_error(req.full_url, login_code)
            return Resp({"access_token": "JWT1"} if path == "/v2/auth/token" else {"token": "JWT1"})
        assert req.get_header("Authorization") == "Bearer JWT1"
        if path == "/v2/repositories/ns/secai-data/":
            if repo_code:
                raise http_error(req.full_url, repo_code)
            return Resp(repo_body)
        if path == "/v2/repositories/ns/secai-data/tags":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
            return Resp(pages[int(q.get("page", ["1"])[0]) - 1])
        if req.get_method() == "DELETE" and path.startswith("/v2/repositories/ns/secai-data/tags/"):
            return Resp(b"")
        raise http_error(req.full_url, 404)
    return fn, logins


@pytest.mark.parametrize("body,rc,out", [({"is_private": True}, 0, "private"), ({"is_private": False}, 0, "public"),
                                         ({}, 2, ""), ({"is_private": "true"}, 2, "")])
def test_hub_repo_visibility(reg, capsys, tmp_path, body, rc, out):
    fn, logins = hub_router(repo_body=body)
    reg.route(fn)
    got = run(reg, capsys, "--auth", token_file(tmp_path), "hub-repo", "ns/secai-data")
    assert got[:2] == (rc, out)
    assert logins == ["/v2/auth/token"]


@pytest.mark.parametrize("code,want", [(401, 1), (403, 1), (404, 1), (429, 2), (500, 2)])
def test_hub_repo_http_errors(reg, capsys, tmp_path, code, want):
    # repo chưa có (404) hay không đọc được (401/403) => 1: publish.sh KHÔNG push (push sẽ tự tạo repo, có thể public)
    fn, _ = hub_router(repo_code=code)
    reg.route(fn)
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-repo", "ns/secai-data")[0] == want


@pytest.mark.parametrize("code,want", [(401, 1), (403, 1)])
def test_hub_login_rejected(reg, capsys, tmp_path, code, want):
    fn, _ = hub_router(login_code=code)
    reg.route(fn)
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-repo", "ns/secai-data")[0] == want


def test_hub_login_falls_back_then_gives_up_with_2(reg, capsys, tmp_path):
    fn, logins = hub_router(login_code=500)
    reg.route(fn)
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-repo", "ns/secai-data")[0] == 2
    assert logins == ["/v2/auth/token", "/v2/users/login"]


def test_hub_without_auth_is_anonymous_and_401_is_1(reg, capsys):
    reg.route(lambda req: (_ for _ in ()).throw(http_error(req.full_url, 401)))
    assert run(reg, capsys, "hub-repo", "ns/secai-data")[0] == 1
    assert reg.calls == [("GET", "https://hub.docker.com/v2/repositories/ns/secai-data/", None)]


def test_hub_tags_paginates_and_logs_in_once(reg, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://hub.example/")
    nxt = "https://hub.example/v2/repositories/ns/secai-data/tags?page_size=100&page={}"
    pages = [{"results": [{"name": "2026.11.27"}, {"name": "init"}], "next": nxt.format(2)},
             {"results": [{"name": "2026.11.28"}, {"x": 1}, "junk"], "next": nxt.format(3)},
             {"results": [{"name": "2026.11.28.2"}], "next": None}]
    fn, logins = hub_router(pages=pages)
    reg.route(fn)
    rc, out, _ = run(reg, capsys, "--auth", token_file(tmp_path), "hub-tags", "ns/secai-data")
    assert rc == 0 and out.split() == ["2026.11.27", "init", "2026.11.28", "2026.11.28.2"]
    assert logins == ["/v2/auth/token"]   # JWT dùng lại cho mọi trang
    assert all(u.startswith("https://hub.example/") for _, u, _ in reg.calls)


def test_hub_tags_too_many_pages_is_2(reg, capsys, tmp_path):
    loop = {"results": [{"name": "a"}], "next": "https://hub.docker.com/v2/repositories/ns/secai-data/tags?page=1"}
    fn, _ = hub_router(pages=[loop])
    reg.route(fn)
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-tags", "ns/secai-data")[0] == 2


def test_hub_delete_tag(reg, capsys, tmp_path):
    fn, _ = hub_router()
    reg.route(fn)
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-delete-tag", "ns/secai-data", "2026.11.20")[0] == 0
    assert reg.calls[-1][:2] == ("DELETE", "https://hub.docker.com/v2/repositories/ns/secai-data/tags/2026.11.20/")
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-delete-tag", "ns/secai-data", "../x")[0] == 2


@pytest.mark.parametrize("name", ["NS/secai-data", "ns", "ns/x/y", "ns/../x"])
def test_hub_bad_repo_name_is_2(reg, capsys, tmp_path, name):
    reg.route(lambda req: Resp({"access_token": "JWT1"}))
    assert run(reg, capsys, "--auth", token_file(tmp_path), "hub-repo", name)[0] == 2
