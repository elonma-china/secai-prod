#!/usr/bin/env python3
"""registry.py [--auth USER:TOKEN_FILE] LỆNH ... - hỏi registry / Docker Hub, chỉ đọc (trừ hub-delete-tag).

  digest <name:tag>            -> sha256:<hex> (Docker-Content-Digest của manifest/index, Registry HTTP API v2)
  tags <name>                  -> mỗi tag một dòng (Registry API v2)
  hub-repo <ns>/<repo>         -> "private" hoặc "public" (Docker Hub API, trường is_private; cần --auth)
  hub-tags <ns>/<repo>         -> mỗi tag một dòng (Docker Hub API; repo private cần --auth)
  hub-delete-tag <ns>/<repo> <tag>   xoá một tag (cần token Docker Hub có quyền Delete)

Ví dụ name: ghcr.io/elonma-china/secai-stack, qdrant/qdrant (Docker Hub, thêm library/ cho image chính thức),
docker.io/<ns>/secai-data. Mặc định ẩn danh. --auth USER:TOKEN_FILE: token đọc trong tiến trình này từ tệp (phải thuộc user
đang chạy, mode 600/400), dùng làm Basic auth khi xin token registry và khi đăng nhập Docker Hub API; không lên argv/log.
Mã thoát: 0 đạt; 1 không có/không có quyền (401/403/404); 2 lỗi mạng/định dạng/sai cú pháp.
Env cho test: HUB_API_URL (mặc định https://hub.docker.com).
"""
import base64
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
TIMEOUT = 30
AUTH = None   # (user, token) từ --auth


def hub_api():
    return os.environ.get("HUB_API_URL", "https://hub.docker.com").rstrip("/")


def load_auth(spec):
    user, _, path = spec.partition(":")
    if not user or not path:
        raise ValueError("--auth cần USER:TOKEN_FILE")
    st = os.stat(path)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) not in (0o600, 0o400):
        raise ValueError("tệp token phải thuộc user đang chạy và mode 600/400")
    with open(path, encoding="utf-8") as f:
        tok = f.read().strip()
    if not tok:
        raise ValueError("tệp token rỗng")
    return user, tok


def split_ref(ref):
    """-> (host, repo, tag). Docker Hub: registry-1.docker.io, image chính thức thêm library/."""
    if "@" in ref:
        raise ValueError("ref có digest; dùng name:tag")
    first = ref.split("/", 1)[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        host, rest = first, ref.split("/", 1)[1]
    else:
        host, rest = "registry-1.docker.io", ref
    last = rest.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = rest.rsplit(":", 1)
    else:
        repo, tag = rest, "latest"
    if host in ("docker.io", "index.docker.io"):
        host = "registry-1.docker.io"
    if host == "registry-1.docker.io" and "/" not in repo:
        repo = "library/" + repo
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", repo) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("ref không hợp lệ: " + ref)
    return host, repo, tag


def _open(req):
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def _basic():
    return "Basic " + base64.b64encode(f"{AUTH[0]}:{AUTH[1]}".encode()).decode()


def _token(www, repo):
    m = dict(re.findall(r'(\w+)="([^"]*)"', www or ""))
    realm = m.get("realm")
    if not realm or not realm.startswith("https://"):
        raise RuntimeError("registry không trả realm token hợp lệ")
    q = {"scope": f"repository:{repo}:pull"}
    if m.get("service"):
        q["service"] = m["service"]
    hdr = {"Authorization": _basic()} if AUTH else {}
    with _open(urllib.request.Request(realm + "?" + urllib.parse.urlencode(q), headers=hdr)) as r:
        d = json.load(r)
    return d.get("token") or d.get("access_token")


def request(host, path, repo, method="GET", accept=ACCEPT):
    url = f"https://{host}{path}"
    hdr = {"Accept": accept}
    try:
        return _open(urllib.request.Request(url, method=method, headers=hdr))
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        www = e.headers.get("WWW-Authenticate", "")
    try:
        tok = _token(www, repo)
    except urllib.error.HTTPError as e:   # repo private: token ẩn danh/không đủ quyền bị từ chối
        raise PermissionError(f"không có quyền đọc {host}/{repo} (HTTP {e.code})") from None
    if not tok:
        raise PermissionError(f"không có token đọc cho {host}/{repo}")
    hdr["Authorization"] = "Bearer " + tok
    return _open(urllib.request.Request(url, method=method, headers=hdr))


def digest(ref):
    host, repo, tag = split_ref(ref)
    with request(host, f"/v2/{repo}/manifests/{tag}", repo, method="HEAD") as r:
        d = r.headers.get("Docker-Content-Digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", d):
        raise RuntimeError("registry không trả Docker-Content-Digest")
    return d


def tags(name):
    host, repo, _ = split_ref(name + ":x")
    with request(host, f"/v2/{repo}/tags/list?n=1000", repo, accept="application/json") as r:
        return json.load(r).get("tags") or []


# ───────────── Docker Hub API (hub.docker.com) ─────────────
def _hub_jwt():
    if not AUTH:
        raise PermissionError("Docker Hub API cần --auth USER:TOKEN_FILE")
    tries = (("/v2/auth/token", {"identifier": AUTH[0], "secret": AUTH[1]}, "access_token"),
             ("/v2/users/login", {"username": AUTH[0], "password": AUTH[1]}, "token"))
    last = None
    for path, body, key in tries:
        req = urllib.request.Request(hub_api() + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with _open(req) as r:
                t = json.load(r).get(key)
            if t:
                return t
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (401, 403):
                raise PermissionError(f"Docker Hub từ chối đăng nhập (HTTP {e.code}): token sai/hết hạn") from None
    raise RuntimeError(f"Docker Hub không trả JWT ({last})")


def _hub_repo_path(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*", name):
        raise ValueError("cần <namespace>/<repo>: " + name)
    return f"/v2/repositories/{name}"


def _hub(path, method="GET"):
    hdr = {"Accept": "application/json"}
    if AUTH:
        hdr["Authorization"] = "Bearer " + _hub_jwt()
    return _open(urllib.request.Request(hub_api() + path, method=method, headers=hdr))


def hub_repo(name):
    with _hub(_hub_repo_path(name) + "/") as r:
        d = json.load(r)
    if not isinstance(d.get("is_private"), bool):
        raise RuntimeError("Docker Hub không trả is_private")
    return "private" if d["is_private"] else "public"


def hub_tags(name):
    out, path = [], _hub_repo_path(name) + "/tags?page_size=100"
    for _ in range(50):
        with _hub(path) as r:
            d = json.load(r)
        out += [t["name"] for t in d.get("results", []) if isinstance(t, dict) and t.get("name")]
        nxt = d.get("next")
        if not nxt:
            return out
        path = urllib.parse.urlsplit(nxt)._replace(scheme="", netloc="").geturl()
    raise RuntimeError("quá nhiều trang tag")


def hub_delete_tag(name, tag):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("tag không hợp lệ")
    with _hub(_hub_repo_path(name) + f"/tags/{tag}/", method="DELETE") as r:
        r.read()


CMDS = {"digest": 1, "tags": 1, "hub-repo": 1, "hub-tags": 1, "hub-delete-tag": 2}


def main(argv):
    global AUTH
    args = argv[1:]
    try:
        if len(args) >= 2 and args[0] == "--auth":
            AUTH = load_auth(args[1])
            args = args[2:]
    except (OSError, ValueError) as e:
        print(f"registry.py: --auth: {e}", file=sys.stderr)
        return 2
    if not args or args[0] not in CMDS or len(args) != CMDS[args[0]] + 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    cmd = args[0]
    try:
        if cmd == "digest":
            print(digest(args[1]))
        elif cmd == "tags":
            print("\n".join(tags(args[1])))
        elif cmd == "hub-repo":
            print(hub_repo(args[1]))
        elif cmd == "hub-tags":
            print("\n".join(hub_tags(args[1])))
        else:
            hub_delete_tag(args[1], args[2])
        return 0
    except PermissionError as e:
        print(f"registry.py: {e}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as e:
        print(f"registry.py: HTTP {e.code} cho {args[1]}", file=sys.stderr)
        return 1 if e.code in (401, 403, 404) else 2
    except (urllib.error.URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as e:
        print(f"registry.py: lỗi: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
