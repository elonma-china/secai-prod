"""stack/pack.sh va stack/restore-data.sh: goi du lieu co checksum, khong dung 49 GB that, docker la stub."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

STACK = Path(__file__).resolve().parents[1] / "stack"
EXPECTED = ["qdrant/cve_kb_hybrid.snapshot", "neo4j/neo4j.dump", "exploitdb.tar.zst", "poc.tar.zst",
            "poc-sources.tar.zst", "exploitdb-mcp-server.tar.zst"]


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, env=env, text=True, capture_output=True)


def stub_docker(tmp: Path, creates_store: bool = False) -> tuple[dict[str, str], Path]:
    binp = tmp / "bin"
    binp.mkdir(exist_ok=True)
    log = tmp / "docker.log"
    body = f'#!/bin/sh\necho "$@" >> {log}\n'
    if creates_store:  # gia lap `docker run ... neo4j-admin database load`
        body += 'if [ "$1" = run ]; then mkdir -p "$DATA_ROOT/_state/neo4j_data/databases/neo4j" && touch "$DATA_ROOT/_state/neo4j_data/databases/neo4j/neostore"; fi\n'
    (binp / "docker").write_text(body)
    (binp / "docker").chmod(0o755)
    return {**os.environ, "PATH": f"{binp}:{os.environ['PATH']}"}, log


def make_pkg(pkg: Path) -> None:
    """Goi nho hop le: moi tarball chua thu muc cap 1 trung ten."""
    src = pkg.parent / "src"
    for d, f in (("exploitdb", "a.txt"), ("poc", "catalog.db"), ("poc-sources", "t.yaml"), ("exploitdb-mcp-server", "package.json")):
        (src / d).mkdir(parents=True, exist_ok=True)
        (src / d / f).write_text(d)
    (src / "poc" / "store").mkdir(exist_ok=True)
    (src / "poc" / "store" / "x").write_text("x")
    for name, d in (("exploitdb", "exploitdb"), ("poc", "poc"), ("poc-sources", "poc-sources"), ("exploitdb-mcp-server", "exploitdb-mcp-server")):
        subprocess.run(f"tar -C {src} -cf - {d} | zstd -q -o {pkg}/{name}.tar.zst", shell=True, check=True)
    (pkg / "qdrant").mkdir(); (pkg / "qdrant" / "cve_kb_hybrid.snapshot").write_text("snap")
    (pkg / "neo4j").mkdir(); (pkg / "neo4j" / "neo4j.dump").write_text("dump")
    sums = "".join(f"{hashlib.sha256((pkg / f).read_bytes()).hexdigest()}  {f}\n" for f in EXPECTED)
    (pkg / "SHA256SUMS").write_text(sums)


def test_restore_refuses_corrupt_package(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); (pkg / "poc.tar.zst").write_bytes(b"x")
    (pkg / "SHA256SUMS").write_text("0" * 64 + "  poc.tar.zst\n")
    data = tmp_path / "data"
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**os.environ, "DATA_ROOT": str(data)})
    assert r.returncode != 0 and "SHA256" in r.stderr and "poc.tar.zst" in r.stderr
    assert not data.exists() or not any(data.iterdir())      # chua nap gi


def test_restore_requires_manifest_files(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); (pkg / "SHA256SUMS").write_text("")
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**os.environ, "DATA_ROOT": str(tmp_path / "d")})
    assert r.returncode != 0 and "qdrant" in r.stderr
    assert not (tmp_path / "d").exists()


def test_restore_refuses_corrupt_valid_listed_file_among_good(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    (pkg / "poc.tar.zst").write_bytes(b"tampered")
    data = tmp_path / "data"
    env, log = stub_docker(tmp_path)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data)})
    assert r.returncode != 0 and "SHA256" in r.stderr and "poc.tar.zst" in r.stderr
    assert not data.exists() and not log.exists()


def test_restore_success_and_idempotent(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    data = tmp_path / "data"
    env, log = stub_docker(tmp_path, creates_store=True)
    env["DATA_ROOT"] = str(data)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env=env)
    assert r.returncode == 0, r.stderr
    assert (data / "exploitdb" / "a.txt").read_text() == "exploitdb"
    assert (data / "poc" / "catalog.db").exists() and (data / "poc" / "store" / "x").exists()
    assert (data / "poc-sources" / "t.yaml").exists()
    assert (data / "exploitdb-mcp-server" / "package.json").read_text() == "exploitdb-mcp-server"
    assert (data / "qdrant" / "cve_kb_hybrid.snapshot").read_text() == "snap"
    assert (data / "_state" / "qdrant_storage").is_dir() and (data / "_state" / "neo4j_logs").is_dir()
    assert "neo4j-admin database load" in log.read_text()
    assert "PUT" in r.stdout and "cve_kb_hybrid" in r.stdout          # in lenh qdrant, khong tu goi khi thieu --qdrant-url
    # du lieu co san khong bi ghi de; chay lai khong lam gi
    (data / "exploitdb" / "a.txt").write_text("local-edit")
    log.unlink()
    r2 = run("bash", str(STACK / "restore-data.sh"), str(pkg), env=env)
    assert r2.returncode == 0, r2.stderr
    assert (data / "exploitdb" / "a.txt").read_text() == "local-edit"
    assert not log.exists()                                           # store neo4j da co: khong nap lai
    assert not [p for p in data.iterdir() if p.name.startswith(".incoming")]


def test_pack_data_refuses_missing_source_dir(tmp_path):
    src = tmp_path / "src"; (src / "exploitdb").mkdir(parents=True)
    r = run("bash", str(STACK / "pack.sh"), "data", str(src), str(tmp_path / "out"),
            env={**os.environ, "QDRANT_URL": "http://127.0.0.1:1"})
    assert r.returncode != 0 and "poc" in r.stderr and "missing" in r.stderr.lower()
    assert not (tmp_path / "out").exists()


def test_pack_image_never_pushes(tmp_path):
    env, log = stub_docker(tmp_path)
    r = run("bash", str(STACK / "pack.sh"), "image", "HEAD", env=env)
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "build" in calls and "push" not in calls
    assert "ghcr.io/elonma-china/secai-agent-gateway:main-" in r.stdout
    assert "docker push" in r.stdout                                  # in lenh de chu du an chay tay


def test_pack_bad_args_print_usage(tmp_path):
    r = run("bash", str(STACK / "pack.sh"), "bogus")
    assert r.returncode != 0 and "usage" in r.stderr.lower()
