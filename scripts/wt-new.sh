#!/usr/bin/env bash
# Create a worktree + feature branch for one agent/task, ready for scripts/dev.sh.
#
#   scripts/wt-new.sh <task> [branch] [base]
#     task    short name -> worktree ../wt-<task>
#     branch  default feat/<task>
#     base    default origin/main (fetched first)
#
# Then runs `scripts/dev.sh init`: copies the main checkout's .env (mode 600) and writes
# .env.worktree (per-worktree compose project / port / LAN bind IP). Worktrees created by
# T3 Code get the same through the project's "Dev init" script (runOnWorktreeCreate).
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <task> [branch] [base]" >&2; exit 2; }
task=$1; branch=${2:-feat/$1}; base=${3:-origin/main}

MAIN_CHECKOUT=$(git -C "$(dirname "$0")/.." worktree list --porcelain | awk 'NR==1{print $2}')
dest=$(dirname "$MAIN_CHECKOUT")/wt-$task
[ -e "$dest" ] && { echo "wt-new: $dest already exists" >&2; exit 1; }

git -C "$MAIN_CHECKOUT" fetch -q origin
git -C "$MAIN_CHECKOUT" worktree add "$dest" -b "$branch" "$base"
"$dest/scripts/dev.sh" init
echo "wt-new: ready -> cd $dest && scripts/dev.sh up && scripts/dev.sh health"
