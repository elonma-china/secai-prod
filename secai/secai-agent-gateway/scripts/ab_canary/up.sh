#!/usr/bin/env bash
# Start the isolated A/B canary: gateway (127.0.0.1:18261) + Devspace static proxy (127.0.0.1:18262).
# Idempotent: if both are already healthy it does nothing. A first start (or --reset) wipes the temp
# DB, regenerates throw-away tokens and restarts only processes recorded in $AB_CANARY_DIR/pids.
# Usage: up.sh [--reset]      Build the FE first (build-fe.sh), seed afterwards (seed.py).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
RESET=0; [ "${1:-}" = "--reset" ] && RESET=1

for p in "$AB_GW_PORT" "$AB_FE_PORT" "$AB_DEAD_TRAINING_PORT"; do
  if ab_forbidden "$p"; then echo "port $p is reserved for prod/other agents" >&2; exit 2; fi
done
if ab_port_busy "$AB_DEAD_TRAINING_PORT"; then
  echo "training stand-in port $AB_DEAD_TRAINING_PORT must stay dead but is listening; set AB_DEAD_TRAINING_PORT" >&2; exit 2
fi

http_code() { curl -s -o /dev/null -m 3 -w '%{http_code}' "$1" 2>/dev/null || true; }

if [ "$RESET" = 0 ] && ab_gw_alive && ab_fe_alive \
   && [ "$(http_code "http://127.0.0.1:$AB_GW_PORT/health")" = 200 ] \
   && [ "$(http_code "http://127.0.0.1:$AB_FE_PORT/health")" = 200 ]; then
  echo "canary already running: gateway :$AB_GW_PORT, static :$AB_FE_PORT (use --reset for a fresh DB)"
  exit 0
fi

"$(dirname "${BASH_SOURCE[0]}")/down.sh" >/dev/null
for p in "$AB_GW_PORT" "$AB_FE_PORT"; do
  if ab_port_busy "$p"; then echo "port $p is held by a process this harness did not start; aborting" >&2; exit 2; fi
done

[ -x "$AB_PY" ] || { echo "missing $AB_PY" >&2; exit 2; }
[ -f "$AB_FE_DIR/dist/index.html" ] || echo "warning: no canary dist yet; run build-fe.sh (static server needs it)" >&2

umask 077
mkdir -p "$AB_CANARY_DIR" "$AB_LOGS" "$AB_GW_DIR/home" "$AB_FE_DIR"
chmod 700 "$AB_CANARY_DIR"
# Fresh ledger every (re)start; the review DB is created by the gateway on boot.
rm -f "$AB_DB" "$AB_DB"-wal "$AB_DB"-shm "$AB_GW_DIR"/*.db "$AB_GW_DIR"/*.db-* 2>/dev/null || true

# Throw-away identities. Plain tokens go only to 0600 files; the gateway receives SHA-256 hashes.
AB_CREDS="$AB_CREDS" AB_TOKENS="$AB_TOKENS" AB_FE_DIR="$AB_FE_DIR" "$AB_PY" -I - <<'PY'
import hashlib, json, os, secrets
from pathlib import Path
actors = [("canary-admin", "admin", "admin"), ("canary-user-1", "reviewer", "user1"),
          ("canary-user-2", "reviewer", "user2"), ("canary-user-3", "reviewer", "user3")]
tokens = {key: "ab-" + secrets.token_urlsafe(32) for _, _, key in actors}
tokens["training"] = "ab-" + secrets.token_urlsafe(32)   # relay->training token (upstream is dead)
def write(path, data):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(data, f)
    os.chmod(p, 0o600)
write(os.environ["AB_CREDS"], {"credentials": [
    {"token_sha256": hashlib.sha256(tokens[key].encode()).hexdigest(), "actor_id": aid, "role": role}
    for aid, role, key in actors]})
write(os.environ["AB_TOKENS"], tokens)
# Canary-only copy of the static proxy's identity map: demo "Admin" -> canary admin, "Người dùng" -> canary-user-1.
write(Path(os.environ["AB_FE_DIR"]) / ".review-devspace-tokens.local",
      {"admin": tokens["admin"], "reviewer": tokens["user1"]})
PY

TRAINING_TOKEN="$("$AB_PY" -I -c "import json,sys;print(json.load(open(sys.argv[1]))['training'])" "$AB_TOKENS")"

# Gateway: empty environment + empty cwd, so neither repo .env nor shell variables leak in
# (no Qdrant/Neo4j/Redis/LLM/Vast endpoints, AIRGAP on).
cd "$AB_GW_DIR"
( setsid env -i PATH="/usr/local/bin:/usr/bin:/bin" HOME="$AB_GW_DIR/home" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    AIRGAP=true REVIEW_ENABLED=true REVIEW_DB_PATH="$AB_DB" REVIEW_CREDENTIALS_FILE="$AB_CREDS" \
    REWARD_TRAINING_ENABLED=true REWARD_TRAINING_URL="http://127.0.0.1:$AB_DEAD_TRAINING_PORT" \
    REWARD_TRAINING_TOKEN="$TRAINING_TOKEN" \
    GATEWAY_DB_PATH="$AB_GW_DIR/gateway.db" SESSION_DB_PATH="$AB_GW_DIR/sessions.db" \
    REPORT_DB_PATH="$AB_GW_DIR/reports.db" POC_CATALOG_DB="$AB_GW_DIR/poc_catalog.db" \
    "$AB_PY" -m uvicorn "$AB_APP" --host 127.0.0.1 --port "$AB_GW_PORT" --log-level info \
    >"$AB_LOGS/gateway.log" 2>&1 < /dev/null & echo "gateway $!" >> "$AB_PIDS" )

ready=0
for _ in $(seq 1 120); do
  [ "$(http_code "http://127.0.0.1:$AB_GW_PORT/health")" = 200 ] && { ready=1; break; }
  ab_gw_alive || break
  sleep 0.5
done
if [ "$ready" != 1 ]; then echo "gateway failed to become healthy; see $AB_LOGS/gateway.log" >&2; "$(dirname "${BASH_SOURCE[0]}")/down.sh" >/dev/null; exit 1; fi

# Static server: private copy of serve_static.py so ROOT=<copy>/dist and the *.local identity files
# resolve inside $AB_FE_DIR. The original file and the prod .local files are never read or written.
cp "$AB_ROOT/secai-fe-devspace/serve_static.py" "$AB_FE_DIR/serve_static.py"
mkdir -p "$AB_FE_DIR/dist"
cd "$AB_FE_DIR"
GW="http://127.0.0.1:$AB_GW_PORT"
( setsid env -i PATH="/usr/local/bin:/usr/bin:/bin" HOME="$AB_FE_DIR" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    FE_HOST=127.0.0.1 FE_PORT="$AB_FE_PORT" VITE_GATEWAY_PROXY="$GW" REPORT_GATEWAY_PROXY="$GW" \
    TRANSLATION_GATEWAY_PROXY="$GW" REWARD_TRAINING_GATEWAY_PROXY="$GW" \
    REPORT_KEY_FILE="$AB_FE_DIR/.no-report-key" DEVSPACE_REVIEW_ALLOW_LOOPBACK=true \
    /usr/bin/python3 "$AB_FE_DIR/serve_static.py" \
    >"$AB_LOGS/static.log" 2>&1 < /dev/null & echo "static $!" >> "$AB_PIDS" )

for _ in $(seq 1 40); do
  [ "$(http_code "http://127.0.0.1:$AB_FE_PORT/health")" = 200 ] && { echo "canary up: gateway :$AB_GW_PORT, static :$AB_FE_PORT (DB $AB_DB, tokens $AB_TOKENS)"; exit 0; }
  sleep 0.25
done
echo "static server failed; see $AB_LOGS/static.log" >&2; exit 1
