#!/usr/bin/env bash
# Remove a task worktree AND its dev stack (containers, data volume, dev image).
#
#   scripts/wt-rm.sh <task|path>
#
# The branch is kept (push/merge it first, or delete it yourself). Git refuses to remove a
# worktree with uncommitted changes; this script does not force it.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: $0 <task|path>" >&2; exit 2; }

# Repo of the current directory (so a copy of this script run from elsewhere still works),
# falling back to the repo this script lives in.
MAIN_CHECKOUT=$( { git worktree list --porcelain 2>/dev/null || git -C "$(dirname "$0")/.." worktree list --porcelain; } | sed -n '1s/^worktree //p')
case "$1" in */*) dest=$(realpath "$1") ;; *) dest=$(dirname "$MAIN_CHECKOUT")/wt-$1 ;; esac

git -C "$MAIN_CHECKOUT" worktree list --porcelain | grep -qx "worktree $dest" \
    || { echo "wt-rm: $dest is not a worktree of this repo" >&2; exit 1; }
case "$dest" in
    "$MAIN_CHECKOUT"|*-deploy-main) echo "wt-rm: refusing to remove $dest (main / production source)" >&2; exit 1 ;;
esac

# Check for uncommitted work before tearing the stack down, so a refusal leaves it intact.
[ -z "$(git -C "$dest" status --porcelain --untracked-files=no)" ] \
    || { echo "wt-rm: $dest has uncommitted changes; commit or stash first" >&2; exit 1; }

if [ -f "$dest/.env.worktree" ] && [ -x "$dest/scripts/dev.sh" ]; then
    "$dest/scripts/dev.sh" clean
fi
rm -f "$dest/.env" "$dest/.env.worktree"
git -C "$MAIN_CHECKOUT" worktree remove "$dest"
echo "wt-rm: removed $dest (branch kept)"
