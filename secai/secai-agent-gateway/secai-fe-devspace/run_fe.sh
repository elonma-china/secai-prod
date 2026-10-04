#!/usr/bin/env bash
# Keep-alive supervisor for the static FE server on :5173.
# Origin for the Cloudflare quick tunnel (graphics-accent-...trycloudflare.com).
# Uses serve_static.py (stdlib only, no inotify/Vite watch) so it cannot die
# from watcher exhaustion; auto-restarts on any crash so 502 does not recur.
cd "$(dirname "$0")" || exit 1
export FE_HOST="${FE_HOST:-0.0.0.0}"
export FE_PORT="${FE_PORT:-5173}"
export VITE_GATEWAY_PROXY="${VITE_GATEWAY_PROXY:-http://127.0.0.1:8131}"

while true; do
  echo "[supervisor] $(date -Is) starting serve_static.py on :${FE_PORT} -> ${VITE_GATEWAY_PROXY}"
  python3 serve_static.py
  code=$?
  echo "[supervisor] $(date -Is) serve_static.py exited (code=${code}); restarting in 2s"
  sleep 2
done
