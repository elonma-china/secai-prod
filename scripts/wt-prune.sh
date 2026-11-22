#!/usr/bin/env bash
# Prune git worktrees, local branches and dev stacks whose PR is already merged.
#
#   scripts/wt-prune.sh              # dry-run: only lists what it would do
#   scripts/wt-prune.sh --yes        # actually prune (also logged to $WT_PRUNE_LOG)
#   scripts/wt-prune.sh --yes --remote   # also delete the branch on origin
#
# Run inside the repo (any worktree). A worktree/branch is pruned ONLY if ALL hold:
#   1. a PR with this head branch is MERGED (asked from GitHub via `gh`, since the repo allows
#      squash/rebase merges that `git branch --merged` cannot see) AND the local branch tip is
#      exactly that PR's head commit (=> no commit made after the merge is ever lost);
#   2. no uncommitted/untracked changes;
#   3. no process has its cwd inside the worktree (T3 session / agent / shell still open);
#   4. not main, *-deploy-main, the main checkout, or a detached worktree.
# Order per worktree: wt-rm.sh (dev stack + worktree) -> delete local branch [-> origin branch].
# Also removes orphan dev stacks (compose project secai-gw-dev-* whose worktree dir is gone).
# Never touches production (`secai-docker`) or anything not named secai-gw-dev-*.
set -euo pipefail

YES=0; REMOTE=0
for a in "$@"; do
    case $a in
        --yes) YES=1 ;;
        --remote) REMOTE=1 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) echo "wt-prune: unknown option $a (see --help)" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
git rev-parse --git-dir >/dev/null 2>&1 || { echo "wt-prune: run inside the repo" >&2; exit 2; }
MAIN=$(git worktree list --porcelain | awk 'NR==1{print $2}')
LOG=${WT_PRUNE_LOG:-$HOME/.local/state/secai/wt-prune.log}
mkdir -p "$(dirname "$LOG")"

# One run at a time (hourly timer vs. a manual/post-merge run).
exec 9>"${LOG%.log}.lock"
flock -n 9 || { echo "wt-prune: another run is in progress" >&2; exit 0; }

REMOVED=0; SKIPPED=0
say() {
    printf '%s\n' "$*"
    if [ "$YES" = 1 ]; then printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG"; fi
    return 0
}
skip() { SKIPPED=$((SKIPPED + 1)); say "skip   $1 — $2"; }

git fetch -q --prune origin || say "warn   git fetch failed; using the last known origin state"

MERGED=$(mktemp); trap 'rm -f "$MERGED"' EXIT
gh pr list --state merged --limit 300 --json headRefName,headRefOid \
    --jq '.[] | "\(.headRefName) \(.headRefOid)"' > "$MERGED" \
    || { echo "wt-prune: cannot list merged PRs via gh (auth/network); nothing pruned" >&2; exit 1; }

# 0 when a merged PR exists for <branch> whose head commit is exactly <sha>.
merged_at() { grep -qxF "$1 $2" "$MERGED"; }

# 0 when some process has its cwd inside directory $1 (this script itself excluded).
in_use() {
    local d=$1 p c
    for p in /proc/[0-9]*; do
        [ "${p#/proc/}" = "$$" ] && continue
        c=$(readlink "$p/cwd" 2>/dev/null) || continue
        case $c in "$d"|"$d"/*) return 0 ;; esac
    done
    return 1
}

delete_remote() {  # <branch> <sha>
    [ "$REMOTE" = 1 ] || return 0
    local rsha
    rsha=$(git ls-remote --heads origin "$1" | awk '{print $1}')
    if [ "$rsha" = "$2" ]; then
        if [ "$YES" = 1 ]; then
            git push -q origin --delete "$1" && say "remote $1 deleted from origin"
        else
            say "would  delete origin/$1"
        fi
    elif [ -n "$rsha" ]; then
        skip "origin/$1" "remote tip differs from the merged head"
    fi
}

CHECKED_OUT=" "   # branches attached to some worktree (handled in the worktree loop)

while IFS=$'\t' read -r path ref; do
    br=${ref#refs/heads/}
    [ -n "$br" ] && CHECKED_OUT="$CHECKED_OUT$br "
    [ "$path" = "$MAIN" ] && continue
    case $path in *-deploy-main) continue ;; esac
    [ -n "$br" ] || { skip "$path" "detached worktree"; continue; }

    sha=$(git -C "$path" rev-parse HEAD 2>/dev/null) || { skip "$path" "cannot read HEAD"; continue; }
    merged_at "$br" "$sha" || { skip "$path ($br)" "no merged PR at this exact commit (open, never PR'd, or commits after merge)"; continue; }
    [ -z "$(git -C "$path" status --porcelain)" ] || { skip "$path ($br)" "uncommitted or untracked changes"; continue; }
    if in_use "$path"; then skip "$path ($br)" "a process is still running inside it"; continue; fi

    if [ "$YES" = 1 ]; then
        if "$SCRIPT_DIR/wt-rm.sh" "$path" >/dev/null 2>&1; then
            git branch -D "$br" >/dev/null 2>&1 || true
            REMOVED=$((REMOVED + 1)); say "pruned $path ($br @ ${sha:0:8})"
            delete_remote "$br" "$sha"
        else
            skip "$path ($br)" "wt-rm.sh failed; left as is"
        fi
    else
        REMOVED=$((REMOVED + 1)); say "would  prune $path ($br @ ${sha:0:8})"
        delete_remote "$br" "$sha"
    fi
done < <(git worktree list --porcelain | awk -v RS= -F'\n' '{
    path=$1; sub(/^worktree /, "", path); br="";
    for (i = 2; i <= NF; i++) if ($i ~ /^branch /) br=substr($i, 8);
    print path "\t" br }')

# Local branches with no worktree (e.g. worktree already removed by hand).
while read -r br sha; do
    [ "$br" = main ] && continue
    case $CHECKED_OUT in *" $br "*) continue ;; esac
    merged_at "$br" "$sha" || continue
    if [ "$YES" = 1 ]; then
        git branch -D "$br" >/dev/null && { REMOVED=$((REMOVED + 1)); say "pruned branch $br (@ ${sha:0:8})"; }
    else
        REMOVED=$((REMOVED + 1)); say "would  prune branch $br (@ ${sha:0:8})"
    fi
    delete_remote "$br" "$sha"
done < <(git for-each-ref --format='%(refname:short) %(objectname)' refs/heads)

# Orphan dev stacks: compose project secai-gw-dev-* whose worktree directory no longer exists.
if command -v docker >/dev/null 2>&1; then
    while IFS=$'\t' read -r proj wd; do
        case $proj in secai-gw-dev-?*) ;; *) continue ;; esac
        [ -n "$wd" ] || continue
        [ -d "$wd" ] && continue
        if [ "$YES" = 1 ]; then
            docker compose -p "$proj" down -v --remove-orphans >/dev/null 2>&1 || true
            docker image rm "secai-agent-gateway:dev-${proj#secai-gw-dev-}" >/dev/null 2>&1 || true
            REMOVED=$((REMOVED + 1)); say "pruned orphan dev stack $proj (worktree $wd is gone)"
        else
            REMOVED=$((REMOVED + 1)); say "would  prune orphan dev stack $proj (worktree $wd is gone)"
        fi
    done < <(docker ps -a --filter label=com.docker.compose.project \
        --format '{{.Label "com.docker.compose.project"}}	{{.Label "com.docker.compose.project.working_dir"}}' | sort -u)
fi

if [ "$YES" = 1 ]; then say "done: $REMOVED pruned, $SKIPPED skipped"; else say "dry-run: $REMOVED would be pruned, $SKIPPED skipped (add --yes to apply)"; fi
