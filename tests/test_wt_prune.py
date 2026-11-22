"""scripts/wt-prune.sh against a throw-away git repo (bare origin + main checkout + worktrees).

`gh` and `docker` are replaced by stubs on PATH: gh prints the "merged PRs" we choose,
docker reports the dev compose projects we choose and records every call.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def sh(*args: str, cwd: Path, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, check=check, text=True, capture_output=True)


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.main = root / "main"
        self.origin = root / "origin.git"
        self.bin = root / "bin"
        self.merged_file = root / "merged.txt"
        self.docker_ps_file = root / "docker_ps.txt"
        self.docker_calls = root / "docker_calls.txt"
        self.log = root / "wt-prune.log"
        self.bin.mkdir()
        self.merged_file.write_text("")
        self.docker_ps_file.write_text("")
        self.docker_calls.write_text("")
        (self.bin / "gh").write_text(f"#!/bin/sh\ncat '{self.merged_file}'\n")
        (self.bin / "docker").write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> '{self.docker_calls}'\n"
            f"[ \"$1\" = ps ] && cat '{self.docker_ps_file}'\n"
            "exit 0\n")
        for f in ("gh", "docker"):
            (self.bin / f).chmod(0o755)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin), cwd=root)
        sh("git", "init", "-q", "-b", "main", str(self.main), cwd=root)
        (self.main / "scripts").mkdir()
        for name in ("wt-prune.sh", "wt-rm.sh"):
            shutil.copy(SCRIPTS / name, self.main / "scripts" / name)
        (self.main / "README").write_text("x\n")
        sh("git", "add", "-A", cwd=self.main, env=env)
        sh("git", "commit", "-q", "-m", "init", cwd=self.main, env=env)
        sh("git", "remote", "add", "origin", str(self.origin), cwd=self.main)
        sh("git", "push", "-q", "origin", "main", cwd=self.main)
        self.env = {**env, "PATH": f"{self.bin}:{os.environ['PATH']}", "WT_PRUNE_LOG": str(self.log)}

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return sh("git", *args, cwd=cwd or self.main, env=self.env).stdout.strip()

    def worktree(self, name: str, *, commit: bool = True, merged: bool = True) -> Path:
        """Worktree wt-<name> on branch feat/<name>; optionally with a commit + merged-PR record."""
        path = self.root / f"wt-{name}"
        self.git("worktree", "add", "-q", "-b", f"feat/{name}", str(path), "main")
        if commit:
            (path / f"{name}.txt").write_text(name)
            self.git("add", "-A", cwd=path)
            self.git("commit", "-q", "-m", name, cwd=path)
        if merged:
            self.mark_merged(f"feat/{name}", self.git("rev-parse", "HEAD", cwd=path))
        return path

    def mark_merged(self, branch: str, sha: str) -> None:
        with self.merged_file.open("a") as fh:
            fh.write(f"{branch} {sha}\n")

    def prune(self, *flags: str) -> str:
        res = sh(str(self.main / "scripts" / "wt-prune.sh"), *flags, cwd=self.main, env=self.env, check=False)
        assert res.returncode == 0, res.stderr + res.stdout
        return res.stdout

    def branches(self) -> set[str]:
        return set(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads").split())


@pytest.fixture()
def sb(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def test_dry_run_changes_nothing(sb: Sandbox) -> None:
    wt = sb.worktree("done")
    out = sb.prune()
    assert "would  prune" in out and "wt-done" in out and "dry-run: 1 would be pruned" in out
    assert wt.exists() and "feat/done" in sb.branches()
    assert not sb.log.exists()


def test_merged_clean_worktree_and_branch_are_removed(sb: Sandbox) -> None:
    wt = sb.worktree("done")
    out = sb.prune("--yes")
    assert "pruned" in out and not wt.exists()
    assert "feat/done" not in sb.branches()
    assert "wt-done" in sb.log.read_text()


def test_squash_merged_branch_is_detected_without_being_an_ancestor(sb: Sandbox) -> None:
    """Squash merge: main never contains the branch commits, only the PR record says merged."""
    wt = sb.worktree("squashed")
    assert subprocess.run(["git", "merge-base", "--is-ancestor", "feat/squashed", "main"], cwd=sb.main).returncode != 0
    sb.prune("--yes")
    assert not wt.exists() and "feat/squashed" not in sb.branches()


def test_fresh_empty_worktree_is_never_pruned(sb: Sandbox) -> None:
    """A brand-new branch equals main (an ancestor of it) but has no merged PR: must survive."""
    wt = sb.worktree("fresh", commit=False, merged=False)
    out = sb.prune("--yes")
    assert wt.exists() and "feat/fresh" in sb.branches()
    assert "no merged PR" in out


def test_commit_after_merge_is_kept(sb: Sandbox) -> None:
    wt = sb.worktree("more")
    (wt / "later.txt").write_text("after the merge")
    sb.git("add", "-A", cwd=wt)
    sb.git("commit", "-q", "-m", "later", cwd=wt)
    out = sb.prune("--yes")
    assert wt.exists() and "feat/more" in sb.branches()
    assert "no merged PR at this exact commit" in out


@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
def test_uncommitted_changes_are_kept(sb: Sandbox, dirty: str) -> None:
    wt = sb.worktree("dirty")
    if dirty == "tracked":
        (wt / "dirty.txt").write_text("edited")
    else:
        (wt / "new-file.txt").write_text("not added")
    out = sb.prune("--yes")
    assert wt.exists() and "uncommitted or untracked" in out


def test_worktree_in_use_by_a_process_is_kept(sb: Sandbox) -> None:
    wt = sb.worktree("busy")
    proc = subprocess.Popen(["sleep", "30"], cwd=wt)
    try:
        time.sleep(0.2)
        out = sb.prune("--yes")
        assert wt.exists() and "still running inside" in out
    finally:
        proc.kill(); proc.wait()
    sb.prune("--yes")
    assert not wt.exists()


def test_main_deploy_main_and_detached_are_never_touched(sb: Sandbox) -> None:
    deploy = sb.root / "repo-deploy-main"
    sb.git("worktree", "add", "-q", "--detach", str(deploy), "main")
    detached = sb.root / "wt-detached"
    sb.git("worktree", "add", "-q", "--detach", str(detached), "main")
    sb.mark_merged("main", sb.git("rev-parse", "main"))
    out = sb.prune("--yes")
    assert deploy.exists() and detached.exists() and sb.main.exists() and "main" in sb.branches()
    assert "detached worktree" in out


def test_branch_without_worktree_is_pruned(sb: Sandbox) -> None:
    sb.git("branch", "feat/orphan", "main")
    sb.git("commit", "-q", "--allow-empty", "-m", "x")  # move main on so feat/orphan != main tip
    sha = sb.git("rev-parse", "feat/orphan")
    sb.mark_merged("feat/orphan", sha)
    sb.prune("--yes")
    assert "feat/orphan" not in sb.branches()


def test_remote_branch_only_deleted_with_flag(sb: Sandbox) -> None:
    wt = sb.worktree("pushed")
    sb.git("push", "-q", "origin", "feat/pushed", cwd=wt)
    sb.prune("--yes")
    assert "refs/heads/feat/pushed" in sb.git("ls-remote", "--heads", "origin")
    wt2 = sb.worktree("pushed2")
    sb.git("push", "-q", "origin", "feat/pushed2", cwd=wt2)
    sb.prune("--yes", "--remote")
    remote_heads = {line.split("\t")[1] for line in sb.git("ls-remote", "--heads", "origin").splitlines()}
    assert "refs/heads/feat/pushed2" not in remote_heads  # deleted with --remote
    assert "refs/heads/feat/pushed" in remote_heads       # first run had no --remote: kept


def test_orphan_dev_stack_is_cleaned_but_prod_and_live_ones_are_not(sb: Sandbox) -> None:
    live = sb.worktree("live", merged=False)  # worktree dir exists -> its stack must stay
    sb.docker_ps_file.write_text(
        f"secai-gw-dev-gone\t{sb.root}/wt-gone\n"
        f"secai-gw-dev-live\t{live}\n"
        "secai-docker\t/home/ict/AIEngines/secai/secai-docker\n")
    sb.prune("--yes")
    calls = sb.docker_calls.read_text()
    assert "compose -p secai-gw-dev-gone down -v" in calls
    assert "image rm secai-agent-gateway:dev-gone" in calls
    assert "-p secai-gw-dev-live" not in calls   # worktree still exists
    assert "-p secai-docker" not in calls        # production is never matched
