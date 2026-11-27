"""Kiểm tĩnh nội dung repo theo docs/criteria/ops.md (offline, không cần docker).

Các tiêu chí chỉ phụ thuộc file trong repo gọi thẳng hàm kiểm của scripts/ops-check.py (một quy tắc,
một chỗ), với runner cấm mọi lệnh ngoài. Phần còn lại (bash -n, compose, release-prod.sh) kiểm độc lập.

Tiêu chí chưa đạt ở thời điểm viết được đánh xfail(strict=True, raises=OpsGap): lỗi đọc file hay
sai của chính test vẫn là FAIL thật; sửa đúng repo thì test XPASS và strict buộc gỡ marker.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker, default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ops_check_repo", ROOT / "scripts" / "ops-check.py")
oc = importlib.util.module_from_spec(_spec)
sys.modules["ops_check_repo"] = oc
_spec.loader.exec_module(oc)

DOC = ROOT / "docs" / "criteria" / "ops.md"


class OpsGap(AssertionError):
    """Repo hiện tại chưa đạt tiêu chí (dùng cho xfail strict)."""


def gap(ok: bool, msg: str) -> None:
    if not ok:
        raise OpsGap(msg)


def xfail_gap(reason: str):
    return pytest.mark.xfail(strict=True, raises=OpsGap, reason=reason)


class NoCommands(oc.Runner):
    def run(self, argv, timeout=60):
        raise AssertionError(f"kiểm tĩnh không được gọi lệnh ngoài: {argv[:3]}")


def repo_ctx(runner: oc.Runner | None = None) -> oc.Ctx:
    cfg = oc.Config(repo=ROOT, docker_dir=ROOT / "không-có", releases_dir=ROOT / "không-có", data_root=ROOT / "không-có",
                    backup_dir=None, gh_repo=None, use_gh=False, cron_globs=[])
    return oc.Ctx(cfg, runner or NoCommands(), dt.datetime.now().astimezone())


def check(fn) -> oc.R:
    return fn(repo_ctx())


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def shell_scripts() -> list[Path]:
    out = []
    for pat in oc.SHELL_SCRIPT_GLOBS:
        out += [p for p in sorted(ROOT.glob(pat)) if p.read_text(errors="replace").startswith("#!")]
    return out


def tracked_files() -> list[str]:
    if not (ROOT / ".git").exists():
        pytest.skip("không có checkout git (gate release chạy trên git archive)")
    return [f for f in subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                                      text=True, check=True).stdout.split("\0") if f]


# ------------------------------------------------------------------ BLD
def test_dockerfile_installs_from_lock() -> None:
    r = check(oc.bld_lock)
    assert r.status == oc.PASS, r
    # --frozen cũng qua OPS-BLD-1 nhưng cài lock cũ mà không báo; repo dựa vào --locked để build từ chối lock lệch.
    assert re.search(r"\buv\s+sync\b[^\n]*--locked\b", oc.dockerfile(repo_ctx())), \
        "deps stage phải `uv sync --locked`, không `--frozen`"


_PYS = ["3.9.18", "3.10.0", "3.10.14", "3.11.0", "3.11.9", "3.12.0", "3.12.3", "3.13.1", "3.14.0", "3.15.0"]
_PLATS = [("linux", "Linux", "posix", "x86_64"), ("linux", "Linux", "posix", "aarch64"),
          ("darwin", "Darwin", "posix", "arm64"), ("win32", "Windows", "nt", "AMD64")]


def _marker_envs(extras: list[str]) -> list[dict]:
    """Lưới môi trường để so marker theo nghĩa: uv viết lại marker khi lock (`python_version >= '3.10'`
    thành `python_full_version >= '3.10'`), nên không so chuỗi. Lưới gồm cả Python ngoài requires-python
    vì uv vẫn ghi vế luôn đúng trong khoảng đó, thêm/bớt nó là lock lệch."""
    base = default_environment()
    return [{**base, "python_full_version": v, "python_version": ".".join(v.split(".")[:2]), "implementation_version": v,
             "sys_platform": sp, "platform_system": ps, "os_name": osn, "platform_machine": m, "extra": e}
            for v in _PYS for sp, ps, osn, m in _PLATS for e in [""] + extras]


def _req_key(name: str, extras, specifier: str, marker: str, envs: list[dict]) -> tuple:
    mk = Marker(marker) if marker else None
    return (canonicalize_name(name), tuple(sorted(canonicalize_name(e) for e in extras)), str(SpecifierSet(specifier)),
            tuple(mk.evaluate(env) if mk else True for env in envs))


def lock_drift(pyproject: dict, lock: dict) -> list[str]:
    """Chỗ uv.lock lệch pyproject.toml, theo các trường `uv sync --locked` so: requires-dist (tên, extras,
    specifier, marker kể cả `extra == '…'`), provides-extras, requires-python. Rỗng là khớp.

    Thuần Python (không gọi uv, không mạng). Không xét [tool.uv] (sources/constraints) hay
    [dependency-groups] — repo chưa dùng."""
    proj = pyproject["project"]
    optional = proj.get("optional-dependencies", {})
    envs = _marker_envs(sorted(canonicalize_name(e) for e in optional))
    want: dict[tuple, str] = {}
    for extra, reqs in [(None, proj.get("dependencies", []))] + list(optional.items()):
        for s in reqs:
            r = Requirement(s)
            marker = str(r.marker) if r.marker else ""
            if extra:
                em = f"extra == '{canonicalize_name(extra)}'"
                marker = f"({marker}) and {em}" if marker else em
            want[_req_key(r.name, r.extras, str(r.specifier), marker, envs)] = f"{s}" + (f" [extra {extra}]" if extra else "")
    pkgs = [p for p in lock.get("package", []) if canonicalize_name(p["name"]) == canonicalize_name(proj["name"])]
    if not pkgs:
        return [f"uv.lock không có package {proj['name']}"]
    meta = pkgs[0].get("metadata", {})
    have: dict[tuple, str] = {}
    for d in meta.get("requires-dist", []):
        shown = d["name"] + (f"[{','.join(d['extras'])}]" if d.get("extras") else "") + d.get("specifier", "")
        have[_req_key(d["name"], d.get("extras", []), d.get("specifier", ""), d.get("marker", ""), envs)] = \
            shown + (f"; {d['marker']}" if d.get("marker") else "")
    out = [f"thiếu trong uv.lock: {want[k]}" for k in want.keys() - have.keys()]
    out += [f"thừa trong uv.lock: {have[k]}" for k in have.keys() - want.keys()]
    out.sort()
    extras_want = sorted(canonicalize_name(e) for e in optional)
    extras_have = sorted(canonicalize_name(e) for e in meta.get("provides-extras", []))
    if extras_want != extras_have:
        out.append(f"provides-extras: pyproject {extras_want} != uv.lock {extras_have}")
    rp_want, rp_have = str(SpecifierSet(proj.get("requires-python", ""))), str(SpecifierSet(lock.get("requires-python", "")))
    if rp_want != rp_have:
        out.append(f"requires-python: pyproject {rp_want!r} != uv.lock {rp_have!r}")
    return out


_PP = {"project": {"name": "Demo_Pkg", "requires-python": ">=3.10",
                   "dependencies": ["a>=1,<2", "B[x]>=2", "tomli>=2; python_version < '3.11'"],
                   "optional-dependencies": {"doc_agent": ["c<3; sys_platform == 'linux'"]}}}
_DIST = [{"name": "a", "specifier": "<2,>=1"}, {"name": "b", "extras": ["x"], "specifier": ">=2"},
         {"name": "tomli", "marker": "python_full_version < '3.11'", "specifier": ">=2"},  # uv viết lại python_version
         {"name": "c", "marker": "sys_platform == 'linux' and extra == 'doc-agent'", "specifier": "<3"}]
_LOCK_OK = {"requires-python": ">=3.10",
            "package": [{"name": "demo-pkg", "metadata": {"requires-dist": _DIST, "provides-extras": ["doc-agent"]}}]}


def _lock_with(**meta) -> dict:
    return {**_LOCK_OK, "package": [{"name": "demo-pkg", "metadata": {**_LOCK_OK["package"][0]["metadata"], **meta}}]}


@pytest.mark.parametrize("lock,needle", [
    (_LOCK_OK, None),
    (_lock_with(**{"requires-dist": _DIST[1:]}), "thiếu trong uv.lock: a>=1,<2"),
    (_lock_with(**{"requires-dist": [{"name": "a", "specifier": ">=1"}] + _DIST[1:]}), "thừa trong uv.lock: a>=1"),
    (_lock_with(**{"requires-dist": _DIST[:3] + [{"name": "c", "specifier": "<3", "marker": "sys_platform == 'linux'"}]}),
     "thiếu trong uv.lock: c<3; sys_platform == 'linux' [extra doc_agent]"),
    (_lock_with(**{"requires-dist": _DIST[:2] + [{"name": "tomli", "specifier": ">=2"}] + _DIST[3:]}),
     "thừa trong uv.lock: tomli>=2"),
    (_lock_with(**{"provides-extras": []}), "provides-extras"),
    (_lock_with(**{"requires-dist": [{"name": "a", "specifier": "<2,>=1", "marker": "python_full_version >= '3.10'"}]
                   + _DIST[1:]}), "thừa trong uv.lock: a<2,>=1; python_full_version >= '3.10'"),
    ({**_LOCK_OK, "requires-python": ">=3.11"}, "requires-python"),
    ({"package": []}, "không có package Demo_Pkg"),
], ids=["match", "missing-dep", "specifier-changed", "extra-marker-lost", "env-marker-lost", "extras",
        "marker-true-in-requires-python", "requires-python", "no-package"])
def test_lock_drift_detects_each_kind(lock: dict, needle: str | None) -> None:
    drift = lock_drift(_PP, lock)
    if needle is None:
        assert drift == []
    else:
        assert any(needle in d for d in drift), drift


def test_uv_lock_matches_pyproject() -> None:
    """Lock lệch thì build FULL (`uv sync --locked`) fail ở gate 2 của release-prod.sh, sau gate pytest ~17 phút."""
    drift = lock_drift(tomllib.loads(read("pyproject.toml")), tomllib.loads(read("uv.lock")))
    assert not drift, "uv.lock lệch pyproject.toml: chạy `uv lock` rồi commit uv.lock\n" + "\n".join(drift)


@xfail_gap("OPS-BLD-3: FROM python:3.12-slim chưa ghim digest")
def test_dockerfile_base_pinned_by_digest() -> None:
    r = check(oc.bld_digest)
    assert r.status in (oc.PASS, oc.FAIL), r
    gap(r.status == oc.PASS, r.evidence)


def final_stage_revision_label(df: str) -> bool | None:
    """LABEL revision=$GIT_SHA có vào image cuối không. None: stage cuối không khai `ARG GIT_SHA`.

    Docker chỉ giữ LABEL của stage cuối (stage khác không vào image), và $GIT_SHA dùng trước dòng
    `ARG GIT_SHA` của chính stage đó thành chuỗi rỗng; nên chỉ xét đoạn sau FROM cuối, sau dòng ARG."""
    last = re.split(r"^\s*FROM\b[^\n]*$", df, flags=re.M | re.I)[-1]
    arg = re.search(r"^\s*ARG\s+GIT_SHA\b", last, re.M)
    if not arg:
        return None
    return re.search(r"^\s*LABEL\b[^\n]*org\.opencontainers\.image\.revision=\S*\$\{?GIT_SHA",
                     last[arg.end():], re.M) is not None


@pytest.mark.parametrize("snippet,want", [
    ("FROM py AS deps\nLABEL org.opencontainers.image.revision=$GIT_SHA\nFROM py AS runtime\nARG GIT_SHA\n", False),
    ("FROM py AS runtime\nLABEL org.opencontainers.image.revision=${GIT_SHA}\nARG GIT_SHA\n", False),
    ("FROM py AS deps\nFROM py AS runtime\nARG GIT_SHA=\"\"\nLABEL org.opencontainers.image.revision=${GIT_SHA}\n", True),
    ("FROM py AS runtime\nENV X=1\n", None),
], ids=["label-in-other-stage", "label-before-arg", "label-after-arg", "no-arg"])
def test_final_stage_revision_label_follows_docker_scoping(snippet: str, want: bool | None) -> None:
    assert final_stage_revision_label(snippet) is want


def test_dockerfile_labels_revision() -> None:
    ok = final_stage_revision_label(oc.dockerfile(repo_ctx()))
    assert ok is not None, "stage cuối của Dockerfile không còn `ARG GIT_SHA`"
    assert ok, "stage cuối không có LABEL revision lấy từ GIT_SHA sau dòng ARG GIT_SHA"


def test_dockerignore_excludes_secrets_and_data() -> None:
    r = check(oc.bld_dockerignore)
    assert r.status == oc.PASS, r


def test_release_script_builds_from_git_archive() -> None:
    rp = read("scripts/release-prod.sh")
    assert 'git -C "$REPO" archive "$TARGET"' in rp
    assert 'diff -rq -x __pycache__ "$REL/imgsrc" "$TREE/src"' in rp
    assert not re.search(r"-v\s+\S*/src:/app/src", rp), "release-prod.sh mount src sống"


# ------------------------------------------------------------------ VER: compose trong repo
COMPOSE_FILES = ["docker-compose.yml", "compose.dev.yml", "deploy/docker-compose.yml", "deploy/report/compose.yaml",
                 "deploy/pentestcode/compose.fragment.yml", "stack/compose.yml", "stack/acceptance/stubs/compose.stubs.yml"]


def compose_images(rel: str) -> list[str]:
    """Giá trị image: (lấy mặc định của ${VAR:-x}; ${VAR:?..} là bắt buộc truyền, bỏ qua)."""
    out = []
    for m in re.finditer(r"^\s*image:\s*(\S+)", read(rel), re.M):
        v = m.group(1).strip("'\"")
        d = re.fullmatch(r"\$\{[A-Z0-9_]+:-([^}]+)\}", v)
        if d:
            v = d.group(1)
        elif v.startswith("${"):
            continue
        out.append(v)
    return out


@pytest.mark.parametrize("rel", COMPOSE_FILES)
def test_repo_compose_has_no_latest_or_untagged_images(rel: str) -> None:
    for ref in compose_images(rel):
        repo, tag, digest = oc.image_repo_tag(ref)
        assert digest or (tag and tag != "latest"), f"{rel}: {ref} dùng latest/không tag"


@xfail_gap("OPS-VER-3: stack/compose.yml dùng redis:7-alpine (chỉ major) và neo4j:5.20-community (chỉ x.y)")
def test_stack_compose_pins_third_party_versions() -> None:
    refs = [r for r in compose_images("stack/compose.yml") if not oc.first_party(r)]
    assert refs
    loose = [r for r in refs if oc.classify_third_party_tag(*oc.image_repo_tag(r)[1:])[0] != oc.PASS]
    gap(not loose, f"chưa ghim x.y.z/digest: {loose}")


# ------------------------------------------------------------------ CI
@xfail_gap("OPS-CI-1: repo chưa có workflow GitHub Actions nào")
def test_ci_workflow_runs_pytest() -> None:
    r = check(oc.ci_workflow)  # use_gh=False: chỉ xét file trong .github/workflows
    assert r.status in (oc.PASS, oc.FAIL), r
    gap(r.status == oc.PASS, r.evidence)


def test_release_script_has_full_pytest_gate() -> None:
    rp = read("scripts/release-prod.sh")
    assert re.search(r'"\$PY" -m pytest -q', rp)
    assert "pytest không xanh" in rp and "KHÔNG release" in rp


def test_release_script_never_pushes_or_prunes() -> None:
    rp = read("scripts/release-prod.sh")
    for bad in ("docker push", "system prune", "volume rm", "down -v", "image prune"):
        assert bad not in rp, bad
    assert not re.search(r"rm -rf /(\s|$)", rp)


@pytest.mark.skipif(not shutil.which("bash"), reason="cần bash")
@pytest.mark.parametrize("path", shell_scripts(), ids=lambda p: str(p.relative_to(ROOT)))
def test_shell_scripts_parse(path: Path) -> None:
    shell = "bash" if "bash" in path.read_text().splitlines()[0] else "sh"
    res = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_shell_scripts_strict_mode() -> None:
    r = check(oc.ci_shell)
    assert r.status == oc.PASS, r


def test_mutating_script_list_is_current() -> None:
    missing = [p for p in oc.MUTATING_SCRIPTS if not (ROOT / p).is_file()]
    assert not missing, f"MUTATING_SCRIPTS trỏ tới file không còn: {missing}"


# ------------------------------------------------------------------ GIT / SEC
def test_gitignore_blocks_runtime_files() -> None:
    assert check(oc.git_ignore).status == oc.PASS


def test_no_forbidden_tracked_files() -> None:
    tracked_files()
    r = oc.git_forbidden(repo_ctx(oc.Runner()))
    assert r.status == oc.PASS, r


def test_fake_secret_fixtures_still_exist() -> None:
    """Danh sách miễn trừ OPS-SEC-6 không được trỏ vào đường dẫn đã mất (miễn trừ treo)."""
    files = tracked_files()
    stale = [p for p in oc.FAKE_SECRET_FIXTURES if not any(f == p or (p.endswith("/") and f.startswith(p)) for f in files)]
    assert not stale, stale


# ------------------------------------------------------------------ DOC
def test_criteria_doc_matches_checker() -> None:
    r = check(oc.doc_criteria)
    assert r.status == oc.PASS, r


def test_criteria_doc_rows_are_measurable() -> None:
    levels = {c.id: c.level for c in oc.CHECKS}
    manual = {c.id for c in oc.CHECKS if c.fn is None}
    seen = 0
    for line in DOC.read_text().splitlines():
        m = re.match(r"^\|\s*(OPS-[A-Z]+-\d+)\s*\|", line)
        if not m:
            continue
        seen += 1
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"{m.group(1)}: cần 5 cột (ID, tiêu chí, mức, cách đo, ngưỡng): {cells}"
        cid, title, level, how, threshold = cells
        assert level == levels[cid], f"{cid}: mức trong tài liệu '{level}' khác checker '{levels[cid]}'"
        assert title and threshold, cid
        if cid in manual:
            assert "kiểm tay" in how.lower(), f"{cid} là kiểm tay nhưng không ghi bước"
        else:
            assert "ops-check" in how or "pytest" in how, f"{cid}: cách đo không trỏ tới ops-check/pytest"
    assert seen == len(oc.CHECKS)


def test_criteria_doc_covers_every_group() -> None:
    txt = DOC.read_text()
    for g in oc.GROUPS:
        assert f"({g})" in txt, f"thiếu mục nhóm {g}"


def _relative_links(md: Path) -> list[tuple[str, Path]]:
    out = []
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
        if not re.match(r"^[a-z]+:", target):
            out.append((target, (md.parent / target).resolve()))
    return out


@pytest.mark.parametrize("md", ["docs/criteria/ops.md", "docs/README.md", "scripts/README.md",
                                "docs/archive/test-results/ops-baseline-20261127.md"])
def test_ops_docs_links_resolve(md: str) -> None:
    broken = [t for t, p in _relative_links(ROOT / md) if not p.exists()]
    assert not broken, f"{md}: link hỏng {broken}"


def test_service_table_lists_scope_and_links_exist() -> None:
    rows = oc.service_runbooks(DOC.read_text())
    names = " ".join(r[0] for r in rows)
    for n in oc.APP_CONTAINERS + oc.DATASTORES + ("secai-docker-pentestcode",):
        assert n in names, f"bảng service thiếu {n}"
    for name, links, _missing in rows:
        for ln in links:
            assert (DOC.parent / ln).resolve().exists(), f"{name}: link hỏng {ln}"


@xfail_gap("OPS-DOC-3: review, converter, pool pentestcode, 4 kho dữ liệu và 2 unit devspace chưa có runbook release/rollback")
def test_every_service_has_runbook() -> None:
    r = check(oc.doc_service_runbooks)
    assert r.status in (oc.PASS, oc.FAIL), r
    gap(r.status == oc.PASS, r.evidence)


@xfail_gap(f"OPS-DOC-4: chưa có {oc.RESTORE_DOC} (stack/restore-data.sh chỉ dựng máy mới từ gói)")
def test_restore_runbook_exists() -> None:
    r = check(oc.doc_restore)
    gap(r.status == oc.PASS, r.evidence)


@xfail_gap(f"OPS-DOC-7: chưa có {oc.INCIDENT_DOC} (người nhận cảnh báo, xử lý tunnel/đĩa/health/KB)")
def test_incident_runbook_exists() -> None:
    r = check(oc.doc_incident)
    gap(r.status == oc.PASS, r.evidence)


def test_release_runbook_is_documented() -> None:
    assert check(oc.doc_release_runbook).status == oc.PASS


def test_scripts_readme_lists_ops_scripts() -> None:
    r = check(oc.doc_scripts_readme)
    assert r.status == oc.PASS, r.evidence


def test_docs_index_links_ops_criteria() -> None:
    txt = read("docs/README.md")
    assert f"]({oc.CRITERIA_DOC.removeprefix('docs/')})" in txt and "](operations/container-versions.md)" in txt
    assert "](ops/" not in txt, "docs/ops/ đã bỏ: tiêu chí ở docs/criteria/, runbook ở docs/operations/"


def test_ops_docs_follow_docs_layout() -> None:
    """docs/README.md: tiêu chí ở docs/criteria/, tài liệu vận hành ở docs/operations/, tên kebab-case."""
    for rel in (oc.CRITERIA_DOC, oc.RESTORE_DOC, oc.INCIDENT_DOC):
        folder, name = rel.rsplit("/", 1)
        assert folder in ("docs/criteria", "docs/operations"), rel
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*\.md", name), rel
    assert not (ROOT / "docs" / "ops").exists()
