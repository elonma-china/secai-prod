#!/usr/bin/env python3
"""registry.py [--gh-auth USER] digest <ref> | tags <repo> - hỏi registry (chỉ đọc) theo Docker Registry HTTP API v2.

  digest ghcr.io/elonma-china/secai-stack:2026.11.27-abcd1234   -> sha256:<hex> (Docker-Content-Digest của manifest/index)
  digest qdrant/qdrant:v1.10.1                                   -> Docker Hub (thêm library/ cho image chính thức)
  tags ghcr.io/elonma-china/secai-data                          -> mỗi tag một dòng

Mặc định ẩn danh: tag/manifest không đọc được (package private, sai tên) thì thoát 1. Lỗi mạng/định dạng: thoát 2.
--gh-auth USER (chỉ ghcr.io): xin token đọc bằng `gh auth token` chạy trong chính tiến trình này; token không lên argv/log.
Không pull, không ghi gì. Dùng cho stack/publish.sh (digest image nền, kiểm digest sau khi push, tìm bản dữ liệu mới nhất).
"""
import base64
import json
import re
import subprocess
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
GH_AUTH_USER = None   # đặt bởi --gh-auth


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
    if host == "registry-1.docker.io" and "/" not in repo:
        repo = "library/" + repo
    if host == "docker.io":
        host = "registry-1.docker.io"
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", repo) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("ref không hợp lệ: " + ref)
    return host, repo, tag


def _open(req):
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def _token(www, repo):
    m = dict(re.findall(r'(\w+)="([^"]*)"', www or ""))
    realm = m.get("realm")
    if not realm or not realm.startswith("https://"):
        raise RuntimeError("registry không trả realm token hợp lệ")
    q = {"scope": f"repository:{repo}:pull"}
    if m.get("service"):
        q["service"] = m["service"]
    hdr = {}
    if GH_AUTH_USER and realm.startswith("https://ghcr.io/"):
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        tok = r.stdout.strip()
        if r.returncode != 0 or not tok:
            raise PermissionError("gh auth token không trả token")
        hdr["Authorization"] = "Basic " + base64.b64encode(f"{GH_AUTH_USER}:{tok}".encode()).decode()
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
    except urllib.error.HTTPError as e:   # GHCR private: token ẩn danh bị từ chối
        raise PermissionError(f"không có quyền đọc ẩn danh {host}/{repo} (HTTP {e.code})") from None
    if not tok:
        raise PermissionError(f"không có token đọc ẩn danh cho {host}/{repo}")
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


def main(argv):
    global GH_AUTH_USER
    if len(argv) >= 3 and argv[1] == "--gh-auth":
        GH_AUTH_USER = argv[2]
        argv = argv[:1] + argv[3:]
    if len(argv) != 3 or argv[1] not in ("digest", "tags"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    try:
        if argv[1] == "digest":
            print(digest(argv[2]))
        else:
            for t in tags(argv[2]):
                print(t)
        return 0
    except (PermissionError, ) as e:
        print(f"registry.py: {e}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as e:
        print(f"registry.py: HTTP {e.code} cho {argv[2]}", file=sys.stderr)
        return 1 if e.code in (401, 403, 404) else 2
    except (urllib.error.URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as e:
        print(f"registry.py: lỗi: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
