#!/bin/bash
# Daily AutoScan -> gateway data sync: CVE knowledge-base ingest (sync_autoscan_to_kb.py).
# The PoC step (sync_autoscan_poc.py) is DISABLED by default (see Step 2): AutoScan's only
# PoC source is exploitdb, a subset the gateway's git-collector catalog already covers.
# Modeled on /home/ict/Downloads/CVE-DB/run_sync.sh.
#
# Guard: the KB step preflights BOTH (a) the AutoScan source API (a real authenticated GET)
# and (b) the embed endpoint (a real POST /v1/embeddings — /health can be green while the
# model name 404s, per CLAUDE.md). Either down -> SKIP the ingest with a loud WARNING and a
# NON-ZERO exit (so cron/monitoring sees the failure) rather than write CVEs without vectors
# (which would degrade RAG to BM25-only) or half-ingest from a dead source. The Python script
# also fetches all candidates before any write, so a mid-run API failure aborts clean too.
# (The PoC step is off by default anyway — see Step 2.)
#
# Both underlying python scripts are independently safe to re-run (incremental watermark +
# idempotent upserts) — this wrapper adds no extra state of its own beyond the log file.
#
# NOT wired into crontab by this script — cutover to a scheduled run is a separate, manual
# step (see task notes / CLAUDE.md).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GW_ROOT="/home/ict/AIEngines/secai/secai-agent-gateway"
GW_ENV="$GW_ROOT/.env"
STATE_DIR="/home/ict/AIEngines/secai/secai-data/_state"
LOG_FILE="$STATE_DIR/autoscan_daily.log"
MAX_LOG_SIZE=$((10 * 1024 * 1024))  # 10MB

mkdir -p "$STATE_DIR"

# Rotate the log if it has grown too large (same pattern as run_sync.sh).
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$MAX_LOG_SIZE" ]; then
    mv "$LOG_FILE" "${LOG_FILE}.old"
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Prefer the gateway venv's python (present + has every dependency the wider gateway code
# uses); fall back to plain python3 if this host doesn't have that venv. Neither sync
# script needs anything beyond the stdlib itself, but this matches the convention of
# run_sync.sh's own KB-push step.
PY="$GW_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="python3"
fi

# Extract KEY=value from the gateway .env (mirrors sync_autoscan_to_kb.py's own load_env,
# just in bash) — a real exported env var of the same name still takes precedence below,
# same precedence Settings/pydantic itself uses.
env_val() {
    [ -f "$GW_ENV" ] || return 0
    grep -m1 "^$1=" "$GW_ENV" 2>/dev/null \
        | cut -d'=' -f2- \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
              -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

EMBEDDING_API_URL="${EMBEDDING_API_URL:-$(env_val EMBEDDING_API_URL)}"
EMBEDDING_API_KEY="${EMBEDDING_API_KEY:-$(env_val EMBEDDING_API_KEY)}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-$(env_val EMBEDDING_MODEL)}"

# Reachability probe: a real (tiny) POST to /v1/embeddings, not just a TCP/health check —
# CLAUDE.md's own incident note is that /health can report "ok" while the embed endpoint
# itself 404s on a model-name mismatch, so we verify the actual endpoint we're about to
# depend on, not just that something is listening.
embed_reachable() {
    if [ -z "$EMBEDDING_API_URL" ]; then
        return 1
    fi
    local url="${EMBEDDING_API_URL%/}/v1/embeddings"
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -X POST "$url" \
        -H "Authorization: Bearer ${EMBEDDING_API_KEY}" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${EMBEDDING_MODEL}\",\"input\":\"healthcheck\"}" 2>>"$LOG_FILE")
    [ "$code" = "200" ]
}

# AutoScan source API (env var wins over .env). NOTE (D1): prefer a STABLE DNS NAME for
# AUTOSCAN_API over a raw GCP IP — the IP is volatile (already moved 34.50.74.39->34.50.65.146),
# and a move should be a DNS change, not a code/.env edit (same lesson as CLAUDE.md's vast
# serving-IP note). The NVD pipeline in run_sync.sh stays a HOT STANDBY (not deleted at cutover).
AUTOSCAN_API="${AUTOSCAN_API:-$(env_val AUTOSCAN_API)}"
AUTOSCAN_USER="${AUTOSCAN_USER:-$(env_val AUTOSCAN_USER)}"
AUTOSCAN_PASS="${AUTOSCAN_PASS:-$(env_val AUTOSCAN_PASS)}"

# Preflight the source API (abort-before-any-write): a real authenticated GET of one CVE.
# Down/moved host -> skip the ingest loudly rather than let the KB silently go stale.
autoscan_api_reachable() {
    [ -n "$AUTOSCAN_API" ] || return 1
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
        -u "${AUTOSCAN_USER}:${AUTOSCAN_PASS}" \
        "${AUTOSCAN_API%/}/api/cve?page_size=1" 2>>"$LOG_FILE")
    [ "$code" = "200" ]
}

log "========================================"
log "Starting autoscan daily sync (python=$PY)"
log "========================================"

# ── Step 1: CVE -> KB — only when BOTH the AutoScan API and the embed endpoint are up ──
# Preflight both before any write. A skip sets a NON-ZERO exit (D1) so the failure is visible
# to cron/monitoring — the real risk is the KB silently going stale, not a loud failure.
if ! autoscan_api_reachable; then
    kb_exit=1
    log "ERROR: AutoScan API unreachable ('$AUTOSCAN_API') -- SKIPPING KB ingest. Source host may be down or its IP moved; check AUTOSCAN_API in .env. KB left unchanged (no partial write). NVD pipeline (run_sync.sh) remains available as a standby."
elif ! embed_reachable; then
    kb_exit=1
    log "ERROR: embed endpoint unreachable ('$EMBEDDING_API_URL') -- SKIPPING KB ingest. Ingesting CVEs without dense vectors would degrade RAG to BM25-only. Fix the serving endpoint / .env, then re-run."
else
    log "AutoScan API + embed endpoint OK -- running sync_autoscan_to_kb.py"
    "$PY" "$SCRIPT_DIR/sync_autoscan_to_kb.py" >> "$LOG_FILE" 2>&1
    kb_exit=$?
    log "sync_autoscan_to_kb.py exit=$kb_exit"
fi

# ── Step 2: PoC catalog — DISABLED by default ──────────────────────────────────
# AutoScan's only PoC source is exploitdb (~46.7k), the SAME single source the gateway's
# git-collector catalog already covers (exploitdb ~46.7k), and AutoScan lacks the other 6
# sources (poc_github/ghsa/nuclei/metasploit/vulhub/vendor, ~77k more PoCs). So pulling PoC
# from AutoScan is redundant — a strict subset. Left OFF; set AUTOSCAN_SYNC_POC=1 to run it.
poc_exit=0
if [ "${AUTOSCAN_SYNC_POC:-0}" = "1" ]; then
    log "AUTOSCAN_SYNC_POC=1 -- running sync_autoscan_poc.py"
    "$PY" "$SCRIPT_DIR/sync_autoscan_poc.py" >> "$LOG_FILE" 2>&1
    poc_exit=$?
    log "sync_autoscan_poc.py exit=$poc_exit"
else
    log "PoC step SKIPPED (AutoScan PoC = exploitdb only, a subset of the gateway's 7-source catalog; set AUTOSCAN_SYNC_POC=1 to enable)."
fi

if [ "$kb_exit" -eq 0 ] && [ "$poc_exit" -eq 0 ]; then
    log "autoscan daily sync completed successfully"
    exit 0
else
    log "autoscan daily sync FAILED (kb_exit=$kb_exit poc_exit=$poc_exit)"
    exit 1
fi
