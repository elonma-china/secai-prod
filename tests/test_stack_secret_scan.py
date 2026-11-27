"""stack/lib/secret_scan.py: luật, archive lồng (luồng), fail closed, allowlist, Qdrant scroll; giá trị không bao giờ lộ.

Bí mật giả được ghép lúc chạy (không nằm nguyên văn trong file này) và đều chứa MARK để chứng minh không bị in ra.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).resolve().parents[1] / "stack" / "lib" / "secret_scan.py")
MARK = "QzMarkVal"  # có trong mọi giá trị bí mật giả
GH = "gh" + "p_" + MARK + "A" * 27            # ghp_ + 36 ký tự
GH_SHORT = "gh" + "p_" + "A" * 10
GH_PAT = "github" + "_pat_" + MARK + "_" + "B" * 13   # github_pat_ + 23 ký tự
HF = "h" + "f_" + MARK + "C" * 21                       # hf_ + 30 ký tự
SK = "s" + "k-" + MARK + "-proj" + "D" * 12             # sk- + 26 ký tự
AWS = "AK" + "IA" + "QZMARKVAL" + "7" * 7               # AKIA + 16 ký tự hoa/số
AWS2 = "AS" + "IA" + "QZMARKVAL" + "8" * 7
PK = "-----BEGIN " + "RSA PRIVATE " + "KEY-----"
PW = "pass" + "word"
needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI không có")


def run(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    p = subprocess.run([sys.executable, "-I", SCRIPT, *args], env=env, cwd=cwd, text=True, capture_output=True, timeout=120)
    assert MARK not in p.stdout and MARK not in p.stderr
    assert MARK.lower() not in p.stdout.lower()
    return p


def findings(out: str) -> list[tuple[str, str]]:
    rows = []
    for line in out.splitlines():
        if line.startswith("FINDING\t"):
            _, rule, loc = line.split("\t")[:3]
            rows.append((rule, loc))
    return rows


def write(p: Path, text: str | bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode() if isinstance(text, str) else text)
    return p


def tar_bytes(members: dict[str, bytes], fmt: int = tarfile.PAX_FORMAT) -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w", format=fmt) as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return bio.getvalue()


def zstd_compress(src: Path, dst: Path) -> None:
    subprocess.run(["zstd", "-q", "-f", "-o", str(dst), str(src)], check=True)


# ---------------- luật ----------------

def test_rules_positive_and_negative(tmp_path: Path) -> None:
    d = tmp_path / "tree"
    write(d / "pk.txt", f"x\n{PK}\nMII{MARK}\n")
    write(d / "pk_neg.txt", "-----BEGIN PUBLIC KEY-----\n-----BEGIN CERTIFICATE-----\n")
    write(d / "pgp.asc", "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----\n")
    write(d / "gh.txt", f"t={GH}\nshort={GH_SHORT}\nx{GH}\n{GH_PAT}\n")
    write(d / "hf.txt", f"{HF}\nhf_short123\n")
    write(d / "sk.txt", f"key: {SK}\ntask-{MARK}-abcdefghijklmnopqrstuvwxyz\nsk-short\n")
    write(d / "aws.txt", f"{AWS}\n{AWS}9\nX{AWS}\n{AWS2}\n")
    write(d / "pw.txt", "\n".join([
        f"NEO4J_{PW.upper()}={MARK}",          # 1 +
        f"my{PW}={MARK}",                      # 2 -
        f"{PW}=" + "${X}",                     # 3 -
        f'{PW}="$1"',                          # 4 -
        f"{PW}=",                              # 5 -
        f'{PW}=""',                            # 6 -
        f"PASSWD = '{MARK}'",                  # 7 +
        f'"{PW}"="{MARK}"',                    # 8 +
        f"{PW}=%(pw)s",                        # 9 -
        f"{PW}={{{{ pw }}}}",                  # 10 -
        f"{PW}=<{MARK}>",                      # 11 -
        f"{PW}=({MARK})",                      # 12 -
        f"{PW}=[{MARK}]",                      # 13 -
        f"u=a&{PW}=&x=1",                      # 14 -
        f"{PW}: {MARK}",                       # 15 - (không có '=')
        f"user=a&{PW}={MARK}&x=1",             # 16 +
    ]) + "\n")
    write(d / "api.txt", "\n".join([
        "api_" + f"key=abc123{MARK}",          # 1 +
        "LLM_API_KEY=",                        # 2 -
        f"apikey=%(k)s{MARK}",                 # 3 -
        f"X-Api-Key={MARK}",                   # 4 +
        f"rapi_key={MARK}",                    # 5 -
        "API-" + f"KEY = \"{MARK}\"",          # 6 +
        "api_key=$OPENAI",                     # 7 -
    ]) + "\n")
    p = run(str(d))
    assert p.returncode == 1, p.stderr
    assert sorted(findings(p.stdout)) == sorted([
        ("private-key", "pk.txt:2"), ("private-key", "pgp.asc:1"),
        ("github-token", "gh.txt:1"), ("github-token", "gh.txt:4"),
        ("hf-token", "hf.txt:1"),
        ("sk-key", "sk.txt:1"),
        ("aws-access-key", "aws.txt:1"), ("aws-access-key", "aws.txt:4"),
        ("password-assign", "pw.txt:1"), ("password-assign", "pw.txt:7"), ("password-assign", "pw.txt:8"),
        ("password-assign", "pw.txt:16"),
        ("api-key-assign", "api.txt:1"), ("api-key-assign", "api.txt:4"), ("api-key-assign", "api.txt:6"),
    ])


def test_clean_tree_exit_0_and_script_scans_itself_clean(tmp_path: Path) -> None:
    write(tmp_path / "a.txt", "nothing to see\npassword_hint = none\n")
    p = run(str(tmp_path))
    assert (p.returncode, p.stdout) == (0, ""), p.stderr
    p = run(SCRIPT)
    assert p.returncode == 0, p.stdout + p.stderr


def test_chunk_boundary_found_once_with_right_lines(tmp_path: Path) -> None:
    chunk = 8192
    filler = ("abcdefghij" * 6 + "\n").encode()  # 61 byte mỗi dòng
    content = bytearray()
    lines = 0
    want = []
    # Đặt token vắt qua ranh giới đọc (bội chunk) và ranh giới cửa sổ (len - 4096) ở nhiều offset.
    for target in (chunk - 5, 2 * chunk - 1, 2 * chunk + 4096 - 7, 3 * chunk - 20, 4 * chunk + 3, 6 * chunk - 30):
        while len(content) + len(filler) < target:
            content += filler
            lines += 1
        content += b"x" * (target - len(content)) + b" "
        content += GH.encode() + b"\n"
        lines += 1
        want.append(("github-token", f"big.txt:{lines}"))
    for _ in range(300):
        content += filler
    write(tmp_path / "big.txt", bytes(content))
    # Bộ đệm đầu tiên kết thúc ở 2*chunk: lookahead của AKIA không được "thấy" hết dữ liệu ở đó rồi báo nhầm.
    edge = 2 * chunk - 20
    write(tmp_path / "edge_neg.txt", b"." * (edge - 1) + b" " + AWS.encode() + b"Z" + b"." * 9000)
    write(tmp_path / "edge_pos.txt", b"." * (edge - 1) + b" " + AWS.encode() + b"-" + b"." * 9000)
    p = run(str(tmp_path), env={**os.environ, "SECRET_SCAN_CHUNK": str(chunk)})
    assert p.returncode == 1, p.stderr
    assert findings(p.stdout) == want + [("aws-access-key", "edge_pos.txt:1")]


def test_binary_offset(tmp_path: Path) -> None:
    data = b"\0\1\2" + b"\xff" * 9997 + AWS.encode() + b"\0" * 100
    write(tmp_path / "blob.dat", data)
    p = run(str(tmp_path / "blob.dat"))
    assert findings(p.stdout) == [("aws-access-key", "blob.dat@10000")]


def test_env_files(tmp_path: Path) -> None:
    write(tmp_path / ".env", "A=1\n")
    write(tmp_path / "sub" / ".env.local", "B=2\n")
    write(tmp_path / ".env.example", f"{PW}={MARK}\n")
    write(tmp_path / ".env.sample", "C=3\n")
    write(tmp_path / "sub" / ".envrc", "D=4\n")
    p = run(str(tmp_path))
    assert sorted(findings(p.stdout)) == [("env-file", ".env"), ("env-file", "sub/.env.local"),
                                          ("password-assign", ".env.example:1")]


# ---------------- archive ----------------

@needs_zstd
def test_nested_tar_zst_tar_member(tmp_path: Path) -> None:
    inner = tar_bytes({"dir/f.txt": f"l1\nl2\n{PW}={MARK}\n".encode(), "dir/ok.txt": b"fine\n"})
    outer = tar_bytes({"inner.tar": inner, "dir/.env": b"X=1\n", "notes.bin": tar_bytes({"deep.txt": GH.encode()})})
    write(tmp_path / "a.tar", outer)
    (tmp_path / "pkg").mkdir()
    zstd_compress(tmp_path / "a.tar", tmp_path / "pkg" / "a.tar.zst")
    p = run(str(tmp_path / "pkg"))
    assert p.returncode == 1, p.stderr
    assert sorted(findings(p.stdout)) == [
        ("env-file", "a.tar.zst!dir/.env"),
        ("github-token", "a.tar.zst!notes.bin!deep.txt:1"),  # tar nhận theo magic ustar
        ("password-assign", "a.tar.zst!inner.tar!dir/f.txt:3"),
    ]
    p = run(str(tmp_path / "pkg" / "a.tar.zst"))
    assert ("password-assign", "a.tar.zst!inner.tar!dir/f.txt:3") in findings(p.stdout)


@needs_zstd
def test_tar_zst_member_inside_tar_and_snapshot(tmp_path: Path) -> None:
    write(tmp_path / "w" / "seg.tar", tar_bytes({"payload/x.json": f'{{"k": "{HF}"}}'.encode()}))
    zstd_compress(tmp_path / "w" / "seg.tar", tmp_path / "w" / "seg.tar.zst")
    snap = tar_bytes({"0/segments/s1.tar.zst": (tmp_path / "w" / "seg.tar.zst").read_bytes(),
                      "0/segments/s2.tar": tar_bytes({"vec.dat": b"\0" * 50 + AWS.encode()})})
    write(tmp_path / "pkg" / "qdrant" / "c.snapshot", snap)
    p = run(str(tmp_path / "pkg"))
    assert sorted(findings(p.stdout)) == [
        ("aws-access-key", "qdrant/c.snapshot!0/segments/s2.tar!vec.dat@50"),
        ("hf-token", "qdrant/c.snapshot!0/segments/s1.tar.zst!payload/x.json:1"),
    ]


def test_gz_and_tar_gz(tmp_path: Path) -> None:
    write(tmp_path / "nodes.csv.gz", gzip.compress(f"id,desc\n1,{SK}\n".encode()))
    write(tmp_path / "t.tgz", gzip.compress(tar_bytes({"m/k.txt": f"{AWS}\n".encode()})))
    write(tmp_path / "plain.gz", gzip.compress(tar_bytes({"in.txt": f"x\n{GH}\n".encode()})))
    p = run(str(tmp_path))
    assert sorted(findings(p.stdout)) == [
        ("aws-access-key", "t.tgz!m/k.txt:1"),
        ("github-token", "plain.gz!in.txt:2"),
        ("sk-key", "nodes.csv.gz:2"),
    ]


def test_zip(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "z.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("d/k.txt", f"{PK}\n")
        zf.writestr("d/.env", "A=1\n")
        zf.writestr("d/ok.txt", "ok\n")
    write(tmp_path / "outer.tar", tar_bytes({"inner.zip": (tmp_path / "z.zip").read_bytes()}))
    p = run(str(tmp_path))
    assert sorted(findings(p.stdout)) == [
        ("env-file", "outer.tar!inner.zip!d/.env"), ("env-file", "z.zip!d/.env"),
        ("private-key", "outer.tar!inner.zip!d/k.txt:1"), ("private-key", "z.zip!d/k.txt:1"),
    ]
    p = run(str(tmp_path), env={**os.environ, "SECRET_SCAN_ZIP_MAX": "100"})
    assert p.returncode == 2 and "z.zip" in p.stderr


def test_archive_name_without_magic_is_plain_content(tmp_path: Path) -> None:
    # Cây nguồn bên thứ ba có file text mang đuôi .zip (exploitdb 17419.zip): quét như file thường, ghi vào not_archives.
    write(tmp_path / "17419.zip", f"exploit notes\n{PW}={MARK}\n")
    write(tmp_path / "x.tar.gz", f"{GH}\n")
    write(tmp_path / "e.gz", b"")
    out = tmp_path / "s.json"
    p = run("--summary-json", str(out), str(tmp_path))
    assert p.returncode == 1, p.stderr
    assert sorted(findings(p.stdout)) == [("github-token", "x.tar.gz:1"), ("password-assign", "17419.zip:2")]
    assert sorted(json.loads(out.read_text())["not_archives"]) == ["17419.zip", "e.gz", "x.tar.gz"]
    # Có magic nhưng hỏng thì vẫn fail closed.
    with zipfile.ZipFile(tmp_path / "real.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", "x" * 5000)
    raw = (tmp_path / "real.zip").read_bytes()
    write(tmp_path / "real.zip", raw[: len(raw) - 30])
    p = run(str(tmp_path))
    assert p.returncode == 2 and "real.zip" in p.stderr


# ---------------- fail closed ----------------

@needs_zstd
def test_corrupt_tar_zst_exit_2(tmp_path: Path) -> None:
    write(tmp_path / "t.tar", tar_bytes({"f.txt": b"hello\n" * 5000}))
    zstd_compress(tmp_path / "t.tar", tmp_path / "good.tar.zst")
    raw = (tmp_path / "good.tar.zst").read_bytes()
    d = tmp_path / "pkg"
    write(d / "bad.tar.zst", raw[: len(raw) // 2])
    write(d / "clean.txt", "ok\n")
    p = run(str(d))
    assert p.returncode == 2
    errs = [ln for ln in p.stderr.splitlines() if "LỖI" in ln]
    assert errs and all(len(ln.splitlines()) == 1 for ln in errs) and "bad.tar.zst" in errs[0]
    write(d / "bad.tar.zst", b"not zstd at all")
    assert run(str(d)).returncode == 2
    write(d / "bad.tar.zst", raw + b"trailing garbage")  # tar đọc trọn, chỉ mã thoát zstd báo hỏng
    p = run(str(d))
    assert p.returncode == 2 and "bad.tar.zst" in p.stderr


def test_corrupt_tar_header_midway_exit_2(tmp_path: Path) -> None:
    data = bytearray(tar_bytes({"a.txt": b"a" * 600, "b.txt": f"{GH}\n".encode()}, fmt=tarfile.USTAR_FORMAT))
    second = 512 + 1024  # header a.txt + 600 byte dữ liệu làm tròn khối
    assert data[second:second + 5] == b"b.txt"
    data[second + 148:second + 156] = b"0000000\0"  # hỏng checksum header thứ hai
    write(tmp_path / "x.tar", bytes(data))
    p = run(str(tmp_path))
    assert p.returncode == 2 and "x.tar" in p.stderr
    write(tmp_path / "x.tar", bytes(data[:second + 300]))  # cắt giữa header
    assert run(str(tmp_path)).returncode == 2


def test_truncated_gz_exit_2(tmp_path: Path) -> None:
    raw = gzip.compress(os.urandom(50000))
    write(tmp_path / "c.gz", raw[:-20])
    p = run(str(tmp_path))
    assert p.returncode == 2 and "c.gz" in p.stderr
    (tmp_path / "c.gz").unlink()
    write(tmp_path / "t.tgz", gzip.compress(tar_bytes({"a.txt": b"ok\n"})) + b"junk")  # tar trọn, đuôi gzip hỏng
    p = run(str(tmp_path))
    assert p.returncode == 2 and "t.tgz" in p.stderr


@needs_zstd
def test_zstd_missing_exit_2(tmp_path: Path) -> None:
    write(tmp_path / "t.tar", tar_bytes({"f.txt": b"x"}))
    (tmp_path / "pkg").mkdir()
    zstd_compress(tmp_path / "t.tar", tmp_path / "pkg" / "t.tar.zst")
    empty = tmp_path / "emptybin"
    empty.mkdir()
    p = run(str(tmp_path / "pkg"), env={**os.environ, "PATH": str(empty)})
    assert p.returncode == 2 and "zstd" in p.stderr and "t.tar.zst" in p.stderr


def test_unreadable_and_missing_exit_2(tmp_path: Path) -> None:
    assert run(str(tmp_path / "nope")).returncode == 2
    os.mkfifo(tmp_path / "fifo")  # PATH không phải file thường: báo lỗi, không treo ở open()
    p = run(str(tmp_path / "fifo"))
    assert p.returncode == 2 and "fifo" in p.stderr
    if os.geteuid() != 0:
        f = write(tmp_path / "d" / "locked.txt", "x")
        f.chmod(0)
        try:
            p = run(str(tmp_path / "d"))
            assert p.returncode == 2 and "locked.txt" in p.stderr
        finally:
            f.chmod(0o600)


def test_usage_errors_exit_2(tmp_path: Path) -> None:
    assert run().returncode == 2
    assert run("--qdrant-url", "http://127.0.0.1:1").returncode == 2
    write(tmp_path / "allow.txt", "bogus-rule x\n")
    assert run("--allow", str(tmp_path / "allow.txt"), str(tmp_path)).returncode == 2


# ---------------- exclude / allow / output ----------------

def test_exclude_git_and_subtrees(tmp_path: Path) -> None:
    root = tmp_path / "poc-sources"
    write(root / ".git" / "config", f"{GH}\n")
    write(root / "a" / ".git" / "x", f"{GH}\n")
    write(root / "trickest-cve" / "r.md", f"{GH}\n")
    write(root / "_manual-untracked-backup-2026" / "r.md", f"{GH}\n")
    write(root / "keep" / "r.md", f"{GH}\n")
    write(tmp_path / "pkg.tar", tar_bytes({"poc-sources/trickest-cve/x.md": GH.encode(), "poc-sources/.git/c": GH.encode(),
                                           "poc-sources/keep/y.md": GH.encode()}))
    args = ["--exclude", ".git", "--exclude", "poc-sources/trickest-cve", "--exclude", "_manual-untracked-backup-*"]
    p = run(*args, str(root))
    assert findings(p.stdout) == [("github-token", "keep/r.md:1")]
    p = run(*args, str(tmp_path / "pkg.tar"))
    assert findings(p.stdout) == [("github-token", "pkg.tar!poc-sources/keep/y.md:1")]


def test_multiple_paths_are_prefixed(tmp_path: Path) -> None:
    write(tmp_path / "one" / "a.txt", f"{GH}\n")
    write(tmp_path / "two" / "b.txt", f"{GH}\n")
    p = run("one", "two/b.txt", cwd=tmp_path)
    assert sorted(findings(p.stdout)) == [("github-token", "b.txt:1"), ("github-token", "one/a.txt:1")]


def test_symlinks_skipped_and_counted(tmp_path: Path) -> None:
    outside = write(tmp_path / "outside" / "s.txt", f"{GH}\n")
    d = tmp_path / "d"
    d.mkdir()
    (d / "link.txt").symlink_to(outside)
    (d / "linkdir").symlink_to(outside.parent)
    (d / ".env").symlink_to(outside)
    out = tmp_path / "sum.json"
    p = run("--summary-json", str(out), str(d))
    assert p.returncode == 0 and findings(p.stdout) == []
    assert json.loads(out.read_text())["skipped_symlinks"] == 3


def test_allowlist(tmp_path: Path) -> None:
    d = tmp_path / "pkg"
    write(d / "exploits" / "php" / "1.txt", f"{PW}=admin{MARK}\n")
    write(d / "exploits" / "php" / "2.txt", f"{PW}=admin{MARK}\n{GH}\n")
    write(d / ".env", "A=1\n")
    allow = write(tmp_path / "allow.txt", "# mẫu khai thác công khai\n\npassword-assign exploits/*\n* .env\n")
    out = tmp_path / "s.json"
    p = run("--allow", str(allow), "--summary-json", str(out), str(d))
    assert p.returncode == 1
    assert findings(p.stdout) == [("github-token", "exploits/php/2.txt:2")]
    s = json.loads(out.read_text())
    assert s["allowed"] == 3 and s["counts_by_rule"] == {"github-token": 1}
    write(allow, "password-assign exploits/*\n* .env\ngithub-token exploits/php/2.txt\n")
    p = run("--allow", str(allow), str(d))
    assert p.returncode == 0 and p.stdout == ""


def test_max_print_and_summary_has_no_values(tmp_path: Path) -> None:
    for i in range(5):
        write(tmp_path / "t" / f"f{i}.txt", f"{GH}\n{PW}={MARK}\n")
    out = tmp_path / "s.json"
    p = run("--max-print", "3", "--summary-json", str(out), str(tmp_path / "t"))
    assert p.returncode == 1
    lines = p.stdout.splitlines()
    assert len(lines) == 4 and lines[-1] == "TRUNCATED\t7"
    text = out.read_text()
    assert MARK not in text and MARK.lower() not in text.lower()
    s = json.loads(text)
    assert set(s) >= {"findings", "counts_by_rule", "counts_by_top", "allowed", "scanned_files", "scanned_bytes",
                      "skipped_symlinks", "errors"}
    assert len(s["findings"]) == 10 and all(set(f) == {"rule", "location"} for f in s["findings"])
    assert s["counts_by_rule"] == {"github-token": 5, "password-assign": 5}
    assert s["counts_by_top"] == {f"f{i}.txt": 2 for i in range(5)}
    assert s["scanned_files"] == 5 and s["errors"] == []
    assert "phát hiện 10" in p.stderr


# ---------------- Qdrant ----------------

class _Qdrant(BaseHTTPRequestHandler):
    pages: dict = {}
    seen: list = []
    status = 200

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, body, self.headers.get("api-key")))
        if type(self).status != 200:
            self.send_response(type(self).status)
            self.end_headers()
            return
        res = type(self).pages[body.get("offset")]
        data = json.dumps({"result": res, "status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass


@pytest.fixture()
def qdrant():
    _Qdrant.seen, _Qdrant.status = [], 200
    _Qdrant.pages = {
        None: {"points": [{"id": 1, "payload": {"cve_id": "CVE-1", "text": "clean"}},
                          {"id": 2, "payload": {"text": f"ok {GH}", "x": f"{PW}={MARK}"}}], "next_page_offset": 3},
        3: {"points": [{"id": 3, "payload": {"text": "tiếng Việt sạch"}}], "next_page_offset": None},
    }
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Qdrant)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_qdrant_scroll_two_pages(qdrant: str, tmp_path: Path) -> None:
    key = "qk-" + MARK
    out = tmp_path / "s.json"
    p = run("--qdrant-url", qdrant, "--qdrant-collection", "coll", "--summary-json", str(out),
            env={**os.environ, "QDRANT_API_KEY": key})
    assert p.returncode == 1, p.stderr
    assert sorted(findings(p.stdout)) == [("github-token", "qdrant:coll#2"), ("password-assign", "qdrant:coll#2")]
    assert [(s[0], s[1].get("offset"), s[1]["with_vector"], s[1]["with_payload"], s[1]["limit"], s[2]) for s in _Qdrant.seen] == [
        ("/collections/coll/points/scroll", None, False, True, 1000, key),
        ("/collections/coll/points/scroll", 3, False, True, 1000, key)]
    s = json.loads(out.read_text())
    assert s["counts_by_top"] == {"qdrant:coll": 2} and MARK not in out.read_text()


def test_qdrant_http_error_exit_2(qdrant: str) -> None:
    _Qdrant.status = 500
    p = run("--qdrant-url", qdrant, "--qdrant-collection", "coll")
    assert p.returncode == 2 and "qdrant:coll" in p.stderr



# ───────────── chính sách gói dữ liệu (--policy data) và tầng secret thật (--known-secrets) ─────────────
sys.path.insert(0, str(Path(SCRIPT).parent))
import secret_scan as ss  # noqa: E402


def test_data_corpus_is_fixed_explicit_and_narrow() -> None:
    # Danh sách chủ dự án duyệt 2026-11-27: đổi phải sửa test này (review), không glob, không tiền tố rộng.
    assert ss.DATA_CORPUS == (
        ("exploitdb", "exploitdb.tar.zst!exploitdb/", False),
        ("poc-sources", "poc-sources.tar.zst!poc-sources/", False),
        ("poc/store", "poc.tar.zst!poc/store/", False),
        ("poc/catalog.db", "poc.tar.zst!poc/catalog.db", True),
        ("exploitdb-mcp-server/node_modules", "exploitdb-mcp-server.tar.zst!exploitdb-mcp-server/node_modules/", False),
        ("qdrant payload", "qdrant:cve_kb_hybrid#", False),
        ("qdrant snapshot", "qdrant/cve_kb_hybrid.snapshot!", False),
    )
    for _, prefix, exact in ss.DATA_CORPUS:
        assert not set("*?[]") & set(prefix)
        assert exact or prefix[-1] in "/!#"
    assert ss.SOFT_RULES == {"password-assign", "api-key-assign", "private-key", "aws-access-key", "sk-key"}
    assert ss.corpus_of("poc.tar.zst!poc/catalog.db") == "poc/catalog.db"
    assert ss.corpus_of("poc.tar.zst!poc/catalog.db-wal") is None
    assert ss.corpus_of("poc.tar.zst!poc/storeX/a") is None
    assert ss.corpus_of("exploitdb.tar.zst!exploitdbX/a") is None
    assert ss.corpus_of("MANIFEST.md") is None
    assert ss.corpus_of("qdrant:other_coll#1") is None


def _pkg(tmp_path: Path, members: dict[str, dict[str, bytes]], extra: dict[str, str] | None = None) -> Path:
    pkg = tmp_path / "pkg"
    for arch, files in members.items():
        raw = tmp_path / (arch + ".tar")
        write(raw, tar_bytes(files))
        if arch.endswith(".snapshot"):
            write(pkg / arch, raw.read_bytes())
        else:
            pkg.mkdir(parents=True, exist_ok=True)
            zstd_compress(raw, pkg / (arch + ".tar.zst"))
    for name, text in (extra or {}).items():
        write(pkg / name, text)
    return pkg


@needs_zstd
def test_policy_data_counts_soft_rules_in_corpus_and_blocks_elsewhere(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    soft = f"{PW}=admin{MARK}\n{PK}\n".encode()
    pkg = _pkg(tmp_path, {
        "exploitdb": {"exploitdb/exploits/php/1.php": soft},
        "poc": {"poc/store/ab/x.py": soft, "poc/catalog.db": b"\0" + soft, "poc/other.txt": soft},
        "exploitdb-mcp-server": {"exploitdb-mcp-server/node_modules/z/a.js": soft,
                                 "exploitdb-mcp-server/src/cfg.js": f"api_key=k{MARK}\n".encode()},
    }, {"MANIFEST.md": "# ok\n"})
    js = tmp_path / "s.json"
    p = run("--policy", "data", "--summary-json", str(js), str(pkg))
    assert p.returncode == 1, p.stderr
    assert sorted(findings(p.stdout)) == [
        ("api-key-assign", "exploitdb-mcp-server.tar.zst!exploitdb-mcp-server/src/cfg.js:1"),
        ("password-assign", "poc.tar.zst!poc/other.txt:1"),
        ("private-key", "poc.tar.zst!poc/other.txt:2"),
    ]
    summ = json.loads(js.read_text())
    assert summ["policy"] == "data"
    assert summ["corpus_counts"] == {
        "exploitdb": {"password-assign": 1, "private-key": 1},
        "exploitdb-mcp-server/node_modules": {"password-assign": 1, "private-key": 1},
        "poc/catalog.db": {"password-assign": 1, "private-key": 1},
        "poc/store": {"password-assign": 1, "private-key": 1},
    }
    assert MARK not in js.read_text()
    # mặc định (strict) thì corpus cũng chặn
    p2 = run(str(pkg))
    assert len(findings(p2.stdout)) == 11


@needs_zstd
def test_policy_data_hard_rules_still_block_inside_corpus(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    pkg = _pkg(tmp_path, {"poc-sources": {"poc-sources/a/t.txt": f"x {GH}\n".encode(),
                                           "poc-sources/a/.env": b"A=1\n",
                                           "poc-sources/b/h.txt": f"{HF}\n".encode()}})
    p = run("--policy", "data", str(pkg))
    assert p.returncode == 1
    assert sorted(r for r, _ in findings(p.stdout)) == ["env-file", "github-token", "hf-token"]


def _sources(tmp_path: Path, secret: str) -> list[str]:
    write(tmp_path / "prod.env", f"export ADMIN_API_KEY='{secret}'\nQDRANT_COLLECTION=cve_kb_hybrid_long\n"
          f"SHORT_TOKEN=abc123\nDB_URL=postgres://u:{secret}X@db:5432/x\nPLAIN_URL=http://example.org/very/long/path\n"
          "FLAG_PASSWORD=true\n")
    write(tmp_path / "docker-compose.yml", f"services:\n  neo4j:\n    environment:\n      NEO4J_AUTH: neo4j/${{NEO4J_PASSWORD:-{secret}N}}\n"
          f"      OTHER_SECRET: '{secret}S'\n")
    write(tmp_path / "run_sync.sh", f"#!/bin/sh\nexport NVD_API_KEY=\"{secret}V\"\ncurl -H 'apiKey: {secret}H' x\n")
    d = tmp_path / "rel" / "r1"
    write(d / "p.json", json.dumps({"service": {"environment": {"REWARD_SERVICE_TOKEN": secret + "J", "MODEL": "long-model-name-x"}}}))
    return [f"env:{tmp_path / 'prod.env'}", f"compose:{tmp_path / 'docker-compose.yml'}", f"shell:{tmp_path / 'run_sync.sh'}",
            f"json:{tmp_path / 'rel'}/*/*.json", f"?env:{tmp_path / 'missing.env'}"]


def test_known_secret_sources_parsed_with_filters(tmp_path: Path) -> None:
    secret = MARK + "Sec7zz"   # 15 ký tự
    specs = _sources(tmp_path, secret)
    got = {b.decode(): lab for b, lab in ss.load_known(specs, err=io.StringIO())}
    want = {secret, f"postgres://u:{secret}X@db:5432/x", secret + "X", secret + "N", secret + "S", secret + "V", secret + "H",
            secret + "J"}
    assert set(got) == want
    # không lấy: tên khoá không phải secret, < 12 ký tự, giá trị hiển nhiên, URL không credential
    assert "cve_kb_hybrid_long" not in got and "abc123" not in got and "long-model-name-x" not in got
    assert got[secret].endswith("prod.env#ADMIN_API_KEY")
    assert got[secret + "J"] == "json:r1/p.json#REWARD_SERVICE_TOKEN"


def test_known_secret_required_source_missing_exit_2(tmp_path: Path) -> None:
    write(tmp_path / "a.txt", "x\n")
    p = run("--known-secrets", f"env:{tmp_path / 'nope.env'}", str(tmp_path / "a.txt"))
    assert p.returncode == 2 and "nope.env" in p.stderr
    p = run("--known-secrets", "bogus:x", str(tmp_path / "a.txt"))
    assert p.returncode == 2


@needs_zstd
def test_known_secret_matches_everywhere_even_in_corpus_exit_3(tmp_path: Path) -> None:
    secret = MARK + "Sec7zz"
    specs = _sources(tmp_path, secret)
    (tmp_path / "pkg").mkdir()
    big = b"y" * 20000 + secret.encode() + b"\n"   # vắt qua ranh giới khúc nhỏ
    pkg = _pkg(tmp_path, {"exploitdb": {"exploitdb/a.txt": big}}, {"MANIFEST.md": f"note {secret}V\n"})
    js = tmp_path / "s.json"
    args = [x for sp in specs for x in ("--known-secrets", sp)]
    p = run("--policy", "data", "--summary-json", str(js), *args, str(pkg), env={**os.environ, "SECRET_SCAN_CHUNK": "8192"})
    assert p.returncode == 3, p.stderr
    rows = [l.split("\t") for l in p.stdout.splitlines() if l.startswith("FINDING\tknown-secret")]
    # MANIFEST chứa cả secret lẫn secret+V: hai nguồn, cùng vị trí
    assert sorted((r[2], r[3].rsplit("#", 1)[-1]) for r in rows) == [
        ("MANIFEST.md:1", "ADMIN_API_KEY"), ("MANIFEST.md:1", "NVD_API_KEY"), ("exploitdb.tar.zst!exploitdb/a.txt:1", "ADMIN_API_KEY")]
    assert any(r[3].endswith("run_sync.sh#NVD_API_KEY") for r in rows)
    assert "KHỚP SECRET THẬT" in p.stderr
    summ = js.read_text()
    assert MARK not in summ and json.loads(summ)["known_secret_hits"] == 3
    for f in tmp_path.rglob("*.json"):
        if f.name == "s.json":
            assert MARK not in f.read_text()


def test_known_secret_gh_token_via_fake_gh(tmp_path: Path) -> None:
    b = tmp_path / "bin"
    write(b / "gh", f"#!/bin/sh\necho {GH}\n").chmod(0o755)
    write(tmp_path / "f.txt", f"token {GH}\n")
    env = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}"}
    p = run("--known-secrets", "gh", str(tmp_path / "f.txt"), env=env)
    assert p.returncode == 3
    assert any(l.split("\t")[1:] == ["known-secret", "f.txt:1", "gh auth token"] for l in p.stdout.splitlines())


def test_known_secret_in_qdrant_payload_blocks_even_in_corpus(qdrant: str, tmp_path: Path) -> None:
    secret = MARK + "Payload1"
    _Qdrant.pages = {None: {"points": [{"id": 7, "payload": {"text": f"{PW}=x{MARK} and {secret}"}}], "next_page_offset": None}}
    write(tmp_path / "k.env", f"SOME_API_KEY={secret}\n")
    p = run("--policy", "data", "--known-secrets", f"env:{tmp_path / 'k.env'}", "--qdrant-url", qdrant,
            "--qdrant-collection", "cve_kb_hybrid")
    assert p.returncode == 3, p.stderr
    assert findings(p.stdout) == [("known-secret", "qdrant:cve_kb_hybrid#7")]   # password= trong payload chỉ được đếm
