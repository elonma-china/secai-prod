"""Kiểm tĩnh nội dung repo theo docs/ops/OPS-CRITERIA.md (offline, không cần docker).

Tiêu chí chưa đạt ở thời điểm viết được đánh xfail(strict=True, raises=OpsGap): lỗi đọc file hay
sai của chính test vẫn là FAIL thật; sửa đúng repo thì test XPASS và strict buộc gỡ marker.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ops_check_repo", ROOT / "scripts" / "ops-check.py")
oc = importlib.util.module_from_spec(_spec)
sys.modules["ops_check_repo"] = oc
_spec.loader.exec_module(oc)

DOC = ROOT / "docs" / "ops" / "OPS-CRITERIA.md"


class OpsGap(AssertionError):
    """Repo hiện tại chưa đạt tiêu chí (dùng cho xfail strict)."""


def gap(ok: bool, msg: str) -> None:
    if not ok:
        raise OpsGap(msg)


def xfail_gap(reason: str):
    return pytest.mark.xfail(strict=True, raises=OpsGap, reason=reason)


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
@xfail_gap("OPS-BLD-1: Dockerfile chỉ COPY pyproject.toml rồi pip install theo [project].dependencies, "
           "bỏ qua uv.lock nên deps trôi mỗi lần build FULL")
def test_dockerfile_installs_from_lock() -> None:
    df = read("Dockerfile")
    assert "FROM " in df
    gap(re.search(r"^\s*COPY\b[^\n]*\buv\.lock\b", df, re.M) is not None, "Dockerfile không COPY uv.lock")
    gap(re.search(r"uv\s+sync\b[^\n]*--(frozen|locked)|uv\s+pip\s+sync\b|--require-hashes", df) is not None,
        "Dockerfile không cài theo lock")


@xfail_gap("OPS-BLD-3: FROM python:3.12-slim chưa ghim digest")
def test_dockerfile_base_pinned_by_digest() -> None:
    bases = re.findall(r"^\s*FROM\s+(\S+)", read("Dockerfile"), re.M | re.I)
    assert bases
    loose = [b for b in bases if "@sha256:" not in b]
    gap(not loose, f"FROM chưa ghim digest: {sorted(set(loose))}")


@xfail_gap("OPS-VER-4: Dockerfile không có LABEL org.opencontainers.image.revision (chỉ build OVERLAY gắn)")
def test_dockerfile_labels_revision() -> None:
    df = read("Dockerfile")
    assert "ARG GIT_SHA" in df
    gap(re.search(r"^\s*LABEL\b[^\n]*org\.opencontainers\.image\.revision", df, re.M) is not None,
        "không có LABEL revision")


def test_dockerignore_excludes_secrets_and_data() -> None:
    lines = {ln.strip() for ln in read(".dockerignore").splitlines()}
    assert {".env", ".env.*", ".venv/", "data/", "*.db"} <= lines


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
    wfs = sorted((ROOT / ".github" / "workflows").glob("*.y*ml")) if (ROOT / ".github").exists() else []
    gap(any("pytest" in p.read_text() and "pull_request" in p.read_text() for p in wfs),
        "không có workflow chạy pytest trên pull_request")


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


@pytest.mark.parametrize("path", shell_scripts(), ids=lambda p: str(p.relative_to(ROOT)))
def test_shell_scripts_strict_mode(path: Path) -> None:
    txt = path.read_text()
    rel = str(path.relative_to(ROOT))
    need = {"u"} | ({"pipefail"} if "bash" in txt.splitlines()[0] else set())
    if rel in oc.MUTATING_SCRIPTS:
        need.add("e")
    assert need <= oc.parse_set_flags(txt), f"{rel} thiếu set -{'/'.join(sorted(need - oc.parse_set_flags(txt)))}"


def test_mutating_script_list_is_current() -> None:
    missing = [p for p in oc.MUTATING_SCRIPTS if not (ROOT / p).is_file()]
    assert not missing, f"MUTATING_SCRIPTS trỏ tới file không còn: {missing}"


# ------------------------------------------------------------------ GIT / SEC
def test_gitignore_blocks_runtime_files() -> None:
    lines = {ln.strip() for ln in read(".gitignore").splitlines()}
    assert {".env", ".env.*", "data/", "logs/", "*.log", ".venv/"} <= lines


def test_no_forbidden_tracked_files() -> None:
    bad = [f for f in tracked_files() if oc.FORBIDDEN_TRACKED.search(f)
           and not (f.endswith(".log") and f.startswith(("docs/archive/", "evidence/")))]
    assert not bad, bad


def test_fake_secret_fixtures_still_exist() -> None:
    """Danh sách miễn trừ OPS-SEC-6 không được trỏ vào đường dẫn đã mất (miễn trừ treo)."""
    files = tracked_files()
    stale = [p for p in oc.FAKE_SECRET_FIXTURES if not any(f == p or f.startswith(p) for f in files)]
    assert not stale, stale


# ------------------------------------------------------------------ DOC
def test_criteria_doc_matches_checker() -> None:
    ids = oc.doc_ids(DOC.read_text())
    assert len(ids) == len(set(ids)), "ID trùng trong tài liệu"
    assert set(ids) == {c.id for c in oc.CHECKS}


def test_criteria_doc_rows_are_measurable() -> None:
    levels = {c.id: c.level for c in oc.CHECKS}
    manual = {c.id for c in oc.CHECKS if c.fn is None}
    for line in DOC.read_text().splitlines():
        m = re.match(r"^\|\s*(OPS-[A-Z]+-\d+)\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"{m.group(1)}: cần 5 cột (ID, tiêu chí, mức, cách đo, ngưỡng): {cells}"
        cid, title, level, how, threshold = cells
        assert level == levels[cid], f"{cid}: mức trong tài liệu '{level}' khác checker '{levels[cid]}'"
        assert title and threshold, cid
        if cid in manual:
            assert "kiểm tay" in how.lower(), f"{cid} là kiểm tay nhưng không ghi bước"
        else:
            assert "ops-check" in how or "pytest" in how, f"{cid}: cách đo không trỏ tới ops-check/pytest"


def test_criteria_doc_covers_every_group() -> None:
    txt = DOC.read_text()
    for g in oc.GROUPS:
        assert f"({g})" in txt, f"thiếu mục nhóm {g}"


def test_service_table_lists_scope_and_links_exist() -> None:
    rows = oc.service_runbooks(DOC.read_text())
    names = " ".join(r[0] for r in rows)
    for n in oc.APP_CONTAINERS + oc.DATASTORES + ("secai-docker-pentestcode",):
        assert n in names, f"bảng service thiếu {n}"
    for name, links, _missing in rows:
        for ln in links:
            assert (DOC.parent / ln).resolve().exists(), f"{name}: link hỏng {ln}"


@xfail_gap("OPS-DOC-3: secai-translation-converter chưa có runbook release/rollback")
def test_every_service_has_runbook() -> None:
    rows = oc.service_runbooks(DOC.read_text())
    assert rows
    missing = [n for n, links, miss in rows if miss or not links]
    gap(not missing, f"chưa có runbook: {missing}")


@xfail_gap("OPS-DOC-4: chưa có docs/ops/RESTORE.md (stack/restore-data.sh chỉ dựng máy mới từ gói)")
def test_restore_runbook_exists() -> None:
    p = ROOT / "docs" / "ops" / "RESTORE.md"
    gap(p.is_file(), "chưa có docs/ops/RESTORE.md")
    txt = p.read_text().lower()
    for word in ("qdrant", "neo4j", "sqlite"):
        assert word in txt, word


def test_release_runbook_is_documented() -> None:
    assert "release-prod.sh rollback --yes" in read("CLAUDE.md")
    assert "release-prod.sh" in read("scripts/README.md")


def test_scripts_readme_lists_ops_scripts() -> None:
    txt = read("scripts/README.md")
    names = sorted(p.name for p in (ROOT / "scripts").glob("*.sh")) + ["ops-check.py"]
    assert not [n for n in names if n not in txt]


def test_docs_index_links_ops_criteria() -> None:
    txt = read("docs/README.md")
    assert "ops/OPS-CRITERIA.md" in txt and "](ops/)" in txt
