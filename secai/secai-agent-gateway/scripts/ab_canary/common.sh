# Shared settings for the isolated A/B canary harness. Source this file; do not execute.
# Everything lives under AB_CANARY_DIR (default /tmp/ab-canary); nothing in the repo is written.
AB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AB_CANARY_DIR="${AB_CANARY_DIR:-/tmp/ab-canary}"
AB_GW_PORT="${AB_GW_PORT:-18261}"
AB_FE_PORT="${AB_FE_PORT:-18262}"
# Deliberately dead port: the training relay reports real connection errors, never fake progress.
AB_DEAD_TRAINING_PORT="${AB_DEAD_TRAINING_PORT:-18269}"
AB_APP="${AB_APP:-secai_gateway.unified_app:app}"   # same ASGI entrypoint family prod uses
AB_PY="$AB_ROOT/.venv/bin/python"
AB_PIDS="$AB_CANARY_DIR/pids"
AB_TOKENS="$AB_CANARY_DIR/tokens.json"            # 0600: admin / user1..3 bearer tokens (never printed)
AB_CREDS="$AB_CANARY_DIR/credentials.json"        # 0600: SHA-256 hashes consumed by the gateway
AB_DB="$AB_CANARY_DIR/review.db"
AB_FE_DIR="$AB_CANARY_DIR/fe"
AB_GW_DIR="$AB_CANARY_DIR/gw"
AB_LOGS="$AB_CANARY_DIR/logs"

# Ports that belong to prod / other agents. The harness refuses to touch them.
AB_FORBIDDEN_PORTS="5173 8131 8133 8134 18183 18185 18241 18197 18083 18868"

ab_forbidden() {
  local p
  for p in $AB_FORBIDDEN_PORTS; do
    [ "$1" = "$p" ] && return 0
  done
  return 1
}

ab_port_busy() { ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN; }

ab_pid_of() { # ab_pid_of gateway|static -> pid recorded by this harness (empty if none)
  [ -f "$AB_PIDS" ] || return 0
  awk -v n="$1" '$1==n {print $2}' "$AB_PIDS" | tail -1
}

ab_alive() { # pid is alive AND cmdline carries our marker (guards against PID reuse)
  local pid="$1" marker="$2"
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -qF -- "$marker"
}

ab_gw_alive() { ab_alive "$(ab_pid_of gateway)" "$AB_APP"; }
ab_fe_alive() { ab_alive "$(ab_pid_of static)" "$AB_FE_DIR/serve_static.py"; }
