"""Raw-tarball fetcher unit tests — no network. Tarballs are built in memory so the
extraction/selection logic is exercised without hitting codeload."""
from __future__ import annotations

import io
import tarfile

from secai_gateway.poc.fetch_github import _MAX_FILE, _iter_tar, _pick, _tarball_urls


def _make_targz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_iter_tar_strips_toplevel_and_filters() -> None:
    data = _make_targz({
        "repo-main/exploit.py": b"import socket\n",
        "repo-main/../evil.py": b"pwned\n",              # path traversal — dropped
        "repo-main/huge.py": b"x" * (_MAX_FILE + 5),     # oversize — dropped
        "repo-main/image.bin": b"\x00\x01\x02",          # non-code suffix — dropped
    })
    out = dict(_iter_tar(data))
    # only the valid code file survives, and the "repo-main/" top-level dir is stripped
    assert out == {"exploit.py": b"import socket\n"}


def test_iter_tar_bad_gzip_returns_nothing() -> None:
    assert list(_iter_tar(b"not a tarball")) == []


def test_pick_prefers_exploit_over_readme_and_deps() -> None:
    files = [
        ("README.md", b"# just documentation, nothing to run here\n"),
        ("CVE-2021-44228_exploit.py",
         b"import requests, socket, subprocess\npayload = 'x'\n# CVE-2021-44228 RCE\n"),
        ("vendor/lib/helper.py", b"import requests\n" * 50),  # in a skip-dir → ignored
    ]
    blob, rel = _pick(files, "CVE-2021-44228")
    assert rel == "CVE-2021-44228_exploit.py"
    assert b"payload" in blob


def test_pick_empty_returns_none() -> None:
    assert _pick([("README.md", b"")], "cve-x") is None


def test_tarball_urls_branch_order_and_dedup() -> None:
    assert _tarball_urls("octo", "cat", "develop") == [
        "https://codeload.github.com/octo/cat/tar.gz/develop",
        "https://codeload.github.com/octo/cat/tar.gz/main",
        "https://codeload.github.com/octo/cat/tar.gz/master",
    ]
    # a default 'master' branch must not appear twice
    assert _tarball_urls("o", "r", "master") == [
        "https://codeload.github.com/o/r/tar.gz/master",
        "https://codeload.github.com/o/r/tar.gz/main",
    ]
