"""stack/pack.sh va stack/restore-data.sh: goi du lieu co checksum, khong dung 49 GB that, docker la stub."""
from __future__ import annotations

import gzip
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
NEO4J_FILES = ["neo4j/counts.json", "neo4j/schema.cypher", "neo4j/nodes-CVE.header.csv", "neo4j/nodes-CVE.csv.gz",
               "neo4j/rels-AFFECTS.header.csv", "neo4j/rels-AFFECTS.csv.gz"]
EXPECTED = ["qdrant/cve_kb_hybrid.snapshot", *NEO4J_FILES, "exploitdb.tar.zst", "poc.tar.zst",
            "poc-sources.tar.zst", "exploitdb-mcp-server.tar.zst"]
PW = "pw-SECRET-123"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, env=env, text=True, capture_output=True)


def base_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}


def stub_docker(tmp: Path, nodes: str = "CVE", rels: str = "AFFECTS") -> tuple[dict[str, str], Path]:
    """docker gia: ghi argv + ten bien env (khong ghi gia tri). `import full` tao store; `exec cypher-shell` tra so dem
    tu tmp/fake/{nodes,rels}.out (mac dinh khop make_pkg: CVE=2, AFFECTS=1)."""
    binp = tmp / "bin"
    binp.mkdir(exist_ok=True)
    fake = tmp / "fake"
    fake.mkdir(exist_ok=True)
    (fake / "nodes.out").write_text(f'k, c\n"{nodes}", 2\n')
    (fake / "rels.out").write_text(f'k, c\n"{rels}", 1\n')
    log = tmp / "docker.log"
    (binp / "docker").write_text(f"""#!/bin/sh
echo "ARGV: $*" >> {log}
echo "ENV: NEO4J_AUTH=${{NEO4J_AUTH:+set}} NEO4J_PASSWORD=${{NEO4J_PASSWORD:+set}} auth_ok=$([ "$NEO4J_AUTH" = "neo4j/{PW}" ] && echo y)" >> {log}
nd="$DATA_ROOT/_state/neo4j_data"
case "$*" in
  *"database import full"*) mkdir -p "$nd/databases/neo4j" && touch "$nd/databases/neo4j/neostore" ;;
  *"State.Running"*) cat {fake}/running 2>/dev/null || echo true ;;
  *"RETURN 1"*) [ ! -e {fake}/running ] ;;
  *"labels(n)"*) cat {fake}/nodes.out ;;
  *"type(r)"*) cat {fake}/rels.out ;;
  *"touch /data/.secai-restore-complete"*) touch "$nd/.secai-restore-complete" ;;
esac
""")
    (binp / "docker").chmod(0o755)
    return {**base_env(), "PATH": f"{binp}:{os.environ['PATH']}", "NEO4J_PASSWORD": PW}, log


def make_neo4j_export(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "counts.json").write_text('{"exported_at": "2026-11-24T00:00:00+00:00", "nodes": {"CVE": 2}, "relationships": {"AFFECTS": 1}}\n')
    (d / "schema.cypher").write_text("CREATE CONSTRAINT `cve_id` FOR (n:`CVE`) REQUIRE (n.`id`) IS UNIQUE;\n")
    (d / "nodes-CVE.header.csv").write_text(":ID,id:string,:LABEL\n")
    (d / "nodes-CVE.csv.gz").write_bytes(gzip.compress(b'0,"CVE-1",CVE\n1,"CVE-2",CVE\n'))
    (d / "rels-AFFECTS.header.csv").write_text(":START_ID,:END_ID,:TYPE\n")
    (d / "rels-AFFECTS.csv.gz").write_bytes(gzip.compress(b"0,1,AFFECTS\n"))


def write_sums(pkg: Path, files: list[str]) -> None:
    (pkg / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((pkg / f).read_bytes()).hexdigest()}  {f}\n" for f in files))


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
    make_neo4j_export(pkg / "neo4j")
    write_sums(pkg, EXPECTED)


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
    env, log = stub_docker(tmp_path)
    env["DATA_ROOT"] = str(data)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env=env)
    assert r.returncode == 0, r.stderr
    assert (data / "exploitdb" / "a.txt").read_text() == "exploitdb"
    assert (data / "poc" / "catalog.db").exists() and (data / "poc" / "store" / "x").exists()
    assert (data / "poc-sources" / "t.yaml").exists()
    assert (data / "exploitdb-mcp-server" / "package.json").read_text() == "exploitdb-mcp-server"
    assert (data / "qdrant" / "cve_kb_hybrid.snapshot").read_text() == "snap"
    assert (data / "_state" / "qdrant_storage").is_dir() and (data / "_state" / "neo4j_logs").is_dir()
    assert "neo4j-admin database import full" in log.read_text()
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


def test_restore_rejects_garbage_sums_line(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    lines = (pkg / "SHA256SUMS").read_text().splitlines()
    lines = ["zzzz  poc.tar.zst" if l.endswith("  poc.tar.zst") else l for l in lines]
    (pkg / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    (pkg / "poc.tar.zst").write_bytes(b"corrupt")
    data = tmp_path / "data"
    env, log = stub_docker(tmp_path)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data)})
    assert r.returncode != 0 and "SHA256" in r.stderr and "poc.tar.zst" in r.stderr
    assert not data.exists() and not log.exists()


def test_restore_rejects_package_missing_sums_entry(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    lines = [l for l in (pkg / "SHA256SUMS").read_text().splitlines() if not l.endswith("  poc.tar.zst")]
    (pkg / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**os.environ, "DATA_ROOT": str(tmp_path / "d")})
    assert r.returncode != 0 and "poc.tar.zst" in r.stderr and "SHA256" in r.stderr
    assert not (tmp_path / "d").exists()


def test_restore_nonroot_prints_gateway_chown_instruction(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    data = tmp_path / "data"
    env, _ = stub_docker(tmp_path)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data)})
    assert r.returncode == 0, r.stderr
    d = data / "poc"
    assert f"sudo chown -R 10001:10100 {d} && sudo chmod -R g+rwX {d}" in r.stderr
    assert (d.stat().st_mode & 0o020) and (d / "store").stat().st_mode & 0o020      # g+w da duoc dat


def test_restore_root_chowns_poc_to_gateway(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    data = tmp_path / "data"
    env, _ = stub_docker(tmp_path)
    chown_log = tmp_path / "chown.log"
    (tmp_path / "bin" / "id").write_text("#!/bin/sh\necho 0\n")
    (tmp_path / "bin" / "chown").write_text(f'#!/bin/sh\necho "$@" >> {chown_log}\n')
    for n in ("id", "chown"):
        (tmp_path / "bin" / n).chmod(0o755)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data), "VM_SHARED_GID": "10200"})
    assert r.returncode == 0, r.stderr
    assert f"-R 10001:10200 {data}/poc" in chown_log.read_text()
    assert str(data / "exploitdb") not in chown_log.read_text()
    # tar khong giu owner nguon: chay lai khong chown them (poc da co)
    chown_log.unlink()
    assert run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data)}).returncode == 0
    assert not chown_log.exists()


def test_restore_warns_on_empty_existing_dir(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    data = tmp_path / "data"; (data / "poc-sources").mkdir(parents=True)
    env, _ = stub_docker(tmp_path)
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data)})
    assert r.returncode == 0 and "poc-sources is empty" in r.stderr


def _fake_src(tmp_path):
    src = tmp_path / "src"
    for d in ("exploitdb", "poc/store", "poc-sources"):
        (src / d).mkdir(parents=True)
    (src / "poc" / "catalog.db").write_text("x")
    (tmp_path / "exploitdb-mcp-server").mkdir()
    make_neo4j_export(tmp_path / "neo4j-export")
    return src


def test_pack_data_deletes_server_snapshot_and_partial_out_on_failure(tmp_path):
    src = _fake_src(tmp_path)
    binp = tmp_path / "bin"; binp.mkdir(); log = tmp_path / "curl.log"
    (binp / "curl").write_text(
        f'#!/bin/sh\necho "$@" >> {log}\n'
        'case "$*" in\n'
        '  *"-X POST"*) echo \'{"result":{"name":"snap1"}}\' ;;\n'
        '  *"-X DELETE"*) ;;\n'
        '  *) exit 22 ;;      # tai snapshot that bai\n'
        'esac\n')
    (binp / "curl").chmod(0o755)
    out = tmp_path / "out"
    r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-export", str(tmp_path / "neo4j-export"), str(src), str(out),
            env={**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "QDRANT_URL": "http://q:6333"})
    assert r.returncode != 0
    calls = log.read_text()
    assert "-X DELETE" in calls and "/collections/cve_kb_hybrid/snapshots/snap1" in calls.split("-X DELETE")[1]
    assert not out.exists()                                           # khong de goi do dang


def test_pack_data_unparsable_snapshot_response_lists_snapshots(tmp_path):
    src = _fake_src(tmp_path)
    binp = tmp_path / "bin"; binp.mkdir()
    (binp / "curl").write_text('#!/bin/sh\ncase "$*" in *"-X POST"*) echo oops ;; *) echo LISTING-OF-SNAPSHOTS ;; esac\n')
    (binp / "curl").chmod(0o755)
    r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-export", str(tmp_path / "neo4j-export"), str(src), str(tmp_path / "out"),
            env={**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "QDRANT_URL": "http://q:6333"})
    assert r.returncode != 0 and "LISTING-OF-SNAPSHOTS" in (r.stdout + r.stderr) and "orphan" in r.stderr.lower()


def test_pack_data_excludes_secrets_and_git_from_mcp_tarball(tmp_path):
    src = _fake_src(tmp_path)
    mcp = tmp_path / "exploitdb-mcp-server"
    (mcp / ".git").mkdir(); (mcp / ".git" / "config").write_text("g")
    (mcp / "node_modules" / "pkg").mkdir(parents=True); (mcp / "node_modules" / "pkg" / "index.js").write_text("j")
    (mcp / "build").mkdir(); (mcp / "build" / "a.js").write_text("b")
    (mcp / ".env").write_text("SECRET=1"); (mcp / "tls.pem").write_text("p"); (mcp / "build" / "k.key").write_text("k")
    binp = tmp_path / "bin"; binp.mkdir(); log = tmp_path / "curl.log"
    (binp / "curl").write_text(
        f'#!/bin/sh\necho "$@" >> {log}\n'
        'case "$*" in\n'
        '  *"-X POST"*) echo \'{"result":{"name":"snap1"}}\' ;;\n'
        '  *"-X DELETE"*) ;;\n'
        '  *"-o "*) while [ $# -gt 0 ]; do [ "$1" = -o ] && echo snap > "$2"; shift; done ;;\n'
        '  *) echo \'{"result":{"points_count":7}}\' ;;\n'
        'esac\n')
    (binp / "curl").chmod(0o755)
    (binp / "docker").write_text("#!/bin/sh\nexit 1\n"); (binp / "docker").chmod(0o755)   # khong duoc goi khi dung --neo4j-export
    out = tmp_path / "out"
    r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-export", str(tmp_path / "neo4j-export"), str(src), str(out),
            env={**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "QDRANT_URL": "http://q:6333"})
    assert r.returncode == 0, r.stderr
    assert ".env" in r.stderr and "tls.pem" in r.stderr and "k.key" in r.stderr
    names = subprocess.run(f"zstd -dc {out}/exploitdb-mcp-server.tar.zst | tar -t", shell=True, text=True, capture_output=True).stdout
    assert "exploitdb-mcp-server/node_modules/pkg/index.js" in names and "exploitdb-mcp-server/build/a.js" in names
    assert ".git" not in names and ".env" not in names and ".pem" not in names and ".key" not in names
    assert "7 points" in (out / "MANIFEST.md").read_text()
    assert subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=out).returncode == 0
    assert "-X DELETE" in log.read_text()


def test_pack_data_accepts_relative_src_root(tmp_path):
    """tar -C <work> ... -C <src>: src tương đối từng bị tính từ <work> nên pack chết ở poc.tar.zst."""
    src = _fake_src(tmp_path)
    (src / "poc" / "store" / "a").write_text("a")
    binp = tmp_path / "bin"; binp.mkdir()
    (binp / "curl").write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  *"-X POST"*) echo \'{"result":{"name":"snap1"}}\' ;;\n'
        '  *"-X DELETE"*) ;;\n'
        '  *"-o "*) while [ $# -gt 0 ]; do [ "$1" = -o ] && echo snap > "$2"; shift; done ;;\n'
        '  *) echo \'{"result":{"points_count":7}}\' ;;\n'
        'esac\n')
    (binp / "curl").chmod(0o755)
    r = subprocess.run(["bash", str(STACK / "pack.sh"), "data", "--neo4j-export", "neo4j-export", "--mcp-dir", "exploitdb-mcp-server",
                        "src", "out"], cwd=tmp_path, text=True, capture_output=True,
                       env={**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "QDRANT_URL": "http://q:6333"})
    assert r.returncode == 0, r.stderr
    names = subprocess.run(f"zstd -dc {tmp_path}/out/poc.tar.zst | tar -t", shell=True, text=True, capture_output=True).stdout
    assert "poc/catalog.db" in names and "poc/store/a" in names


def _pack_poc_sources(tmp_path):
    src = _fake_src(tmp_path)
    for p in ("nuclei-templates/a.yaml", "nuclei-templates/.git/HEAD", "trickest-cve/x.md", "PoC-in-GitHub/y.json",
              "_manual-untracked-backup-2026-09-02/z", "priority/Nginx-Rift/poc.py"):
        (src / "poc-sources" / p).parent.mkdir(parents=True, exist_ok=True)
        (src / "poc-sources" / p).write_text("x")
    binp = tmp_path / "bin"; binp.mkdir()
    (binp / "curl").write_text(
        '#!/bin/sh\ncase "$*" in\n  *"-X POST"*) echo \'{"result":{"name":"s"}}\' ;;\n  *"-X DELETE"*) ;;\n'
        '  *"-o "*) while [ $# -gt 0 ]; do [ "$1" = -o ] && echo snap > "$2"; shift; done ;;\n'
        '  *) echo \'{"result":{"points_count":7}}\' ;;\nesac\n')
    (binp / "curl").chmod(0o755)
    for p in ("exploitdb/.git/HEAD", "exploitdb/e.txt", "poc/store/.git/HEAD"):
        (src / p).parent.mkdir(parents=True, exist_ok=True); (src / p).write_text("x")
    out = tmp_path / "out"
    r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-export", str(tmp_path / "neo4j-export"), str(src), str(out),
            env={**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "QDRANT_URL": "http://q:6333"})
    assert r.returncode == 0, r.stderr
    return out


def test_pack_data_excludes_git_from_every_tree(tmp_path):
    out = _pack_poc_sources(tmp_path)
    for t in ("exploitdb", "poc"):
        names = subprocess.run(f"zstd -dc {out}/{t}.tar.zst | tar -t", shell=True, text=True, capture_output=True).stdout
        assert ".git" not in names
    assert "exploitdb/e.txt" in subprocess.run(f"zstd -dc {out}/exploitdb.tar.zst | tar -t", shell=True, text=True, capture_output=True).stdout


def test_pack_data_excludes_poc_sources_skip(tmp_path):
    out = _pack_poc_sources(tmp_path)
    names = subprocess.run(f"zstd -dc {out}/poc-sources.tar.zst | tar -t", shell=True, text=True, capture_output=True).stdout
    files = sorted(n for n in names.split() if not n.endswith("/"))
    assert files == ["poc-sources/nuclei-templates/a.yaml", "poc-sources/priority/Nginx-Rift/poc.py"]


def test_pack_manifest_lists_skipped_sources(tmp_path):
    m = (_pack_poc_sources(tmp_path) / "MANIFEST.md").read_text()
    for n in (".git", "trickest-cve", "PoC-in-GitHub", "_manual-untracked-backup-*"):
        assert n in m


# ---- Neo4j: export CSV trong goi (pack) va import + schema + so dem (restore) ----

CURL_OK = ('#!/bin/sh\ncase "$*" in\n  *"-X POST"*) echo \'{"result":{"name":"s"}}\' ;;\n  *"-X DELETE"*) ;;\n'
           '  *"-o "*) while [ $# -gt 0 ]; do [ "$1" = -o ] && echo snap > "$2"; shift; done ;;\n'
           '  *) echo \'{"result":{"points_count":7}}\' ;;\nesac\n')


def pack_env(tmp_path: Path) -> dict[str, str]:
    binp = tmp_path / "bin"; binp.mkdir(exist_ok=True)
    (binp / "curl").write_text(CURL_OK); (binp / "curl").chmod(0o755)
    (binp / "docker").write_text("#!/bin/sh\nexit 1\n"); (binp / "docker").chmod(0o755)   # pack data khong goi docker
    return {**base_env(), "PATH": f"{binp}:{os.environ['PATH']}", "QDRANT_URL": "http://q:6333"}


def test_pack_neo4j_export_dir_copied_and_summed(tmp_path):
    src = _fake_src(tmp_path)
    out = tmp_path / "out"
    r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-export", str(tmp_path / "neo4j-export"), str(src), str(out),
            env=pack_env(tmp_path))
    assert r.returncode == 0, r.stderr
    exp = tmp_path / "neo4j-export"
    assert sorted(p.name for p in (out / "neo4j").iterdir()) == sorted(p.name for p in exp.iterdir())
    for p in exp.iterdir():
        assert (out / "neo4j" / p.name).read_bytes() == p.read_bytes()
    sums = (out / "SHA256SUMS").read_text()
    for f in NEO4J_FILES:
        assert f"  {f}\n" in sums
    assert "neo4j.dump" not in sums
    assert "2 nodes, 1 relationships" in (out / "MANIFEST.md").read_text()
    assert subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=out).returncode == 0


@pytest.mark.parametrize("drop", ["counts.json", "schema.cypher", "nodes-CVE.header.csv"])
def test_pack_neo4j_export_dir_incomplete_refused(tmp_path, drop):
    src = _fake_src(tmp_path)
    (tmp_path / "neo4j-export" / drop).unlink()
    r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-export", str(tmp_path / "neo4j-export"), str(src), str(tmp_path / "out"),
            env=pack_env(tmp_path))
    assert r.returncode != 0 and drop.split("-")[0] in r.stderr
    assert not (tmp_path / "out").exists()


def _pack_with_exporter(tmp_path, graph):
    from test_stack_neo4j_export import fake_neo4j
    src = _fake_src(tmp_path)
    env = pack_env(tmp_path)
    argv_log = tmp_path / "python3.argv"
    real = subprocess.run(["sh", "-c", "command -v python3"], text=True, capture_output=True, env=env).stdout.strip()
    (tmp_path / "bin" / "python3").write_text(f'#!/bin/sh\necho "$@" >> {argv_log}\nexec {real} "$@"\n')
    (tmp_path / "bin" / "python3").chmod(0o755)
    out = tmp_path / "out"
    with fake_neo4j(graph) as (url, st):
        r = run("bash", str(STACK / "pack.sh"), "data", "--neo4j-url", url, str(src), str(out), env={**env, "NEO4J_PASSWORD": PW})
    return r, out, st, argv_log.read_text()


def test_pack_neo4j_url_runs_exporter(tmp_path):
    """Exporter that (stack/neo4j-export.py) chay voi Neo4j gia cua test_stack_neo4j_export; mat khau chi qua env."""
    from test_stack_neo4j_export import sample_graph
    r, out, st, argv = _pack_with_exporter(tmp_path, sample_graph())
    assert r.returncode == 0, r.stderr
    assert "neo4j-export.py" in argv and PW not in argv
    assert st["reqs"] and all(a and a.startswith("Basic ") for a, _ in st["reqs"])     # NEO4J_PASSWORD den exporter qua env
    assert PW not in r.stdout + r.stderr
    names = sorted(p.name for p in (out / "neo4j").iterdir())
    assert "counts.json" in names and "nodes-CVE.csv.gz" in names and "rels-HAS_CWE.header.csv" in names
    sums = (out / "SHA256SUMS").read_text()
    assert all(f"  neo4j/{n}\n" in sums for n in names)
    assert "5 nodes, 4 relationships" in (out / "MANIFEST.md").read_text()


def test_pack_neo4j_export_drift_fails_and_removes_out(tmp_path):
    from test_stack_neo4j_export import sample_graph
    g = sample_graph(); g["drift"] = {"CVE": 1}
    r, out, _, _ = _pack_with_exporter(tmp_path, g)
    assert r.returncode != 0 and "neo4j" in r.stderr
    assert not out.exists()


@pytest.mark.parametrize("flag", ["--neo4j-dump", "--neo4j-dir"])
def test_pack_rejects_old_neo4j_flags(tmp_path, flag):
    src = _fake_src(tmp_path)
    r = run("bash", str(STACK / "pack.sh"), "data", flag, str(tmp_path / "x"), str(src), str(tmp_path / "out"), env=pack_env(tmp_path))
    assert r.returncode == 2 and "usage" in r.stderr.lower() and "--neo4j-export" in r.stderr
    assert not (tmp_path / "out").exists()


def restore(tmp_path, pkg, env, data=None):
    return run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data or tmp_path / "data")})


def test_restore_builds_import_args_from_files(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path)
    r = restore(tmp_path, pkg, env)
    assert r.returncode == 0, r.stderr
    imp = next(l for l in log.read_text().splitlines() if "database import full" in l)
    assert "--nodes=CVE=/import/nodes-CVE.header.csv,/import/nodes-CVE.csv.gz" in imp
    assert "--relationships=AFFECTS=/import/rels-AFFECTS.header.csv,/import/rels-AFFECTS.csv.gz" in imp
    assert "--id-type=integer" in imp and "--multiline-fields=true" in imp and f"{pkg}/neo4j:/import:ro" in imp


def test_restore_counts_match_writes_complete_marker(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path)
    r = restore(tmp_path, pkg, env)
    assert r.returncode == 0, r.stderr
    assert "==> neo4j: counts match (2 nodes, 1 relationships)" in r.stdout
    nd = tmp_path / "data" / "_state" / "neo4j_data"
    assert (nd / ".secai-restore-started").exists() and (nd / ".secai-restore-complete").exists()
    calls = [l for l in log.read_text().splitlines() if l.startswith("ARGV:")]
    order = ["database import full", "run -d", "RETURN 1", "-f /schema.cypher", "db.awaitIndexes(900)", "labels(n)", "type(r)",
             "touch /data/.secai-restore-complete", "rm -f secai-restore-neo4j-"]
    idx = [next(i for i, l in enumerate(calls) if o in l) for o in order]
    assert idx == sorted(idx), calls
    assert f"{pkg}/neo4j/schema.cypher:/schema.cypher:ro" in calls[idx[1]]


def test_restore_counts_mismatch_dies(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path, nodes="CWE")
    r = restore(tmp_path, pkg, env)
    assert r.returncode != 0
    assert "CVE" in r.stderr and "CWE" in r.stderr and "counts match" not in r.stdout
    assert not (tmp_path / "data" / "_state" / "neo4j_data" / ".secai-restore-complete").exists()
    assert "rm -f secai-restore-neo4j-" in log.read_text()                 # trap luon xoa container tam


def test_restore_incomplete_marker_refuses_rerun(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path, nodes="CWE")
    assert restore(tmp_path, pkg, env).returncode != 0                     # chet sau import, truoc khi so dem khop
    env, log = stub_docker(tmp_path)
    log.unlink()
    r = restore(tmp_path, pkg, env)
    nd = tmp_path / "data" / "_state" / "neo4j_data"
    assert r.returncode != 0
    assert f"incomplete previous Neo4j restore: remove {nd} and rerun" in r.stderr
    assert not log.exists()


def test_restore_existing_store_without_markers_skips(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path)
    store = tmp_path / "data" / "_state" / "neo4j_data" / "databases" / "neo4j"
    store.mkdir(parents=True); (store / "neostore").write_text("")
    del env["NEO4J_PASSWORD"]                                              # khong nap Neo4j thi khong can mat khau
    r = restore(tmp_path, pkg, env)
    assert r.returncode == 0, r.stderr
    assert "store already exists" in r.stdout and not log.exists()


def _isolated_restore(tmp_path) -> Path:
    """Ban sao restore-data.sh trong thu muc rieng de kiem soat $(dirname $0)/.env."""
    st = tmp_path / "stack"; st.mkdir()
    (st / "restore-data.sh").write_bytes((STACK / "restore-data.sh").read_bytes())
    return st


def test_restore_without_password_dies_before_touching_data_root(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    st = _isolated_restore(tmp_path)
    (st / ".env").write_text("NEO4J_PASSWORD=old\nNEO4J_PASSWORD=\n")      # dong cuoi rong
    env, log = stub_docker(tmp_path)
    del env["NEO4J_PASSWORD"]
    data = tmp_path / "data"
    r = run("bash", str(st / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(data)})
    assert r.returncode != 0 and "NEO4J_PASSWORD" in r.stderr
    assert not data.exists() and not log.exists()


def test_restore_password_from_dotenv(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    st = _isolated_restore(tmp_path)
    (st / ".env").write_text(f"NEO4J_PASSWORD=old\nNEO4J_PASSWORD={PW}\n")
    env, log = stub_docker(tmp_path)
    del env["NEO4J_PASSWORD"]
    r = run("bash", str(st / "restore-data.sh"), str(pkg), env={**env, "DATA_ROOT": str(tmp_path / "data")})
    assert r.returncode == 0, r.stderr
    run_d = log.read_text().split("ARGV: run -d", 1)[1].splitlines()
    assert "auth_ok=y" in run_d[1]


def test_restore_password_not_in_argv(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path)
    r = restore(tmp_path, pkg, env)
    assert r.returncode == 0, r.stderr
    text = log.read_text()
    assert PW not in text and PW not in r.stdout + r.stderr
    lines = text.splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith("ARGV: run -d"))
    argv = lines[i].split()
    assert "NEO4J_AUTH" in argv and argv[argv.index("NEO4J_AUTH") - 1] == "-e"
    assert not any(a.startswith("NEO4J_AUTH=") for a in argv)
    assert "auth_ok=y" in lines[i + 1]                                     # gia tri neo4j/<pw> chi nam trong env
    for l, nxt in zip(lines, lines[1:]):
        if l.startswith("ARGV: exec") and "cypher-shell" in l:
            assert " -e NEO4J_PASSWORD " in l and "NEO4J_PASSWORD=set" in nxt
            assert " -e LANG=C.UTF-8 " in l                                 # schema.cypher doc dung UTF-8


def test_restore_dead_neo4j_container_fails_fast(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); make_pkg(pkg)
    env, log = stub_docker(tmp_path)
    (tmp_path / "fake" / "running").write_text("false\n")                  # container tam da thoat
    r = restore(tmp_path, pkg, env)
    assert r.returncode != 0 and "not ready (running=false" in r.stderr
    assert "ARGV: logs --tail 30 secai-restore-neo4j-" in log.read_text()
    assert "rm -f secai-restore-neo4j-" in log.read_text()


# ---- pack.sh images ----
IMG_A, IMG_B = "reg/a:1", "reg/b:2"


def images_env(tmp_path: Path, refs=(IMG_A, IMG_B), have=None):
    """docker gia: compose config --images in refs; image inspect doc tmp/fake/ids ("ref id"); save in byte gia."""
    fake = tmp_path / "fake"; fake.mkdir(exist_ok=True)
    (fake / "refs").write_text("\n".join(refs) + "\n")
    (fake / "ids").write_text("".join(f"{r} sha256:id{i}\n" for i, r in enumerate(refs if have is None else have)))
    binp = tmp_path / "bin"; binp.mkdir(exist_ok=True)
    (binp / "docker").write_text(f'''#!/bin/bash
echo "$*" >> {fake}/calls
case "$*" in
  *"config --images"*) cat {fake}/refs ;;
  "image inspect"*) while read -r r i; do [ "$r" = "${{@: -1}}" ] && echo "$i" && exit 0; done < {fake}/ids; exit 1 ;;
  save*) echo fake-image-bytes ;;
  load*) cat > {fake}/loaded; [ -e {fake}/loadok ] && cat {fake}/loadok >> {fake}/ids; exit 0 ;;
esac
''')
    (binp / "docker").chmod(0o755)
    env_file = tmp_path / "x.env"; env_file.write_text("A=1\n")
    return {**base_env(), "PATH": f"{binp}:{os.environ['PATH']}"}, env_file, fake


def test_pack_images_writes_tar_list_sums(tmp_path):
    env, ef, fake = images_env(tmp_path)
    r = run("bash", str(STACK / "pack.sh"), "images", str(ef), str(tmp_path / "out"), env=env)
    assert r.returncode == 0, r.stderr
    d = tmp_path / "out/images"
    assert (d / "images.txt").read_text() == f"{IMG_A} sha256:id0\n{IMG_B} sha256:id1\n"
    assert subprocess.run(["zstd", "-dc", str(d / "images.tar.zst")], capture_output=True).stdout == b"fake-image-bytes\n"
    assert subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=d).returncode == 0
    assert "save reg/a:1 reg/b:2" in (fake / "calls").read_text() and "push" not in (fake / "calls").read_text()


def test_pack_images_missing_or_placeholder_fails_before_writing(tmp_path):
    env, ef, _ = images_env(tmp_path, refs=(IMG_A, "reg/gone:3", "reg/MISSING:x"), have=(IMG_A,))
    r = run("bash", str(STACK / "pack.sh"), "images", str(ef), str(tmp_path / "out"), env=env)
    assert r.returncode != 0 and "reg/gone:3" in r.stderr and "reg/MISSING:x" in r.stderr
    assert not (tmp_path / "out/images").exists()


def test_pack_images_refuses_existing_out(tmp_path):
    env, ef, _ = images_env(tmp_path)
    (tmp_path / "out/images").mkdir(parents=True)
    r = run("bash", str(STACK / "pack.sh"), "images", str(ef), str(tmp_path / "out"), env=env)
    assert r.returncode != 0 and "đã tồn tại" in r.stderr


# ---- bootstrap.sh load-images ----
def images_pkg(tmp_path, ids_before: str, ids_after: str | None = None):
    env, _, fake = images_env(tmp_path, refs=(IMG_A, IMG_B), have=())
    (fake / "ids").write_text(ids_before)
    if ids_after is not None:
        (fake / "loadok").write_text(ids_after)
    d = tmp_path / "pkg"; d.mkdir()
    (d / "images.txt").write_text(f"{IMG_A} sha256:id0\n{IMG_B} sha256:id1\n")
    subprocess.run(f"zstd -q -o {d}/images.tar.zst < /dev/null; sha256sum images.tar.zst images.txt > SHA256SUMS", shell=True, cwd=d, check=True)
    return env, d, fake


def load(env, d):
    return run("bash", str(STACK / "bootstrap.sh"), "load-images", str(d), env=env)


GOOD = f"{IMG_A} sha256:id0\n{IMG_B} sha256:id1\n"


def test_load_images_skips_when_present(tmp_path):
    env, d, fake = images_pkg(tmp_path, GOOD)
    r = load(env, d)
    assert r.returncode == 0 and "all images present" in r.stdout
    assert "load" not in [c.split()[0] for c in (fake / "calls").read_text().splitlines()]


def test_load_images_loads_and_verifies(tmp_path):
    env, d, fake = images_pkg(tmp_path, f"{IMG_A} sha256:id0\n", f"{IMG_B} sha256:id1\n")
    r = load(env, d)
    assert r.returncode == 0, r.stderr
    assert (fake / "loaded").exists()


def test_load_images_id_mismatch_after_load_fails(tmp_path):
    env, d, _ = images_pkg(tmp_path, f"{IMG_A} sha256:id0\n", f"{IMG_B} sha256:WRONG\n")
    r = load(env, d)
    assert r.returncode != 0 and IMG_B in r.stderr and IMG_A not in r.stderr


def test_load_images_bad_checksum_fails_before_load(tmp_path):
    env, d, fake = images_pkg(tmp_path, "")
    (d / "images.txt").write_text(f"{IMG_A} sha256:id0\n")   # lệch SHA256SUMS
    r = load(env, d)
    assert r.returncode != 0
    assert not (fake / "loaded").exists()
