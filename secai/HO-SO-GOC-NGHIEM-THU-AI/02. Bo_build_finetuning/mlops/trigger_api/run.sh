#!/usr/bin/env bash
# Chạy service kích hoạt MLOps (trigger_api) trên HOST. Dùng venv điều phối .venv-eval (đã có
# fastapi+uvicorn). Nạp secret/khoá từ ~/.config/secai-mlops/mlops.env (NGOÀI cây dossier — xem
# sự cố lộ secret 2026-09-18). Bind 127.0.0.1 mặc định; gateway reverse-proxy tới đây same-origin.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MLOPS_ROOT="$(cd "$HERE/.." && pwd)"
VENV="$MLOPS_ROOT/.venv-eval"
ENV_FILE="${MLOPS_ENV_FILE:-$HOME/.config/secai-mlops/mlops.env}"
# Bind vào IP bridge docker0 (mặc định 172.17.0.1) — để gateway trong container tới được qua
# host.docker.internal (reverse-proxy /showcase-api/retraining/*), NHƯNG vẫn KHÔNG lộ ra internet
# (172.17.0.0/16 là IP private của bridge, không route ra ngoài; khác hẳn 0.0.0.0 sẽ nghe cả trên
# IP public của host). Truy cập local dùng chính IP này (không phải 127.0.0.1). Override qua
# TRIGGER_API_HOST nếu docker0 khác IP, hoặc =127.0.0.1 nếu KHÔNG cần proxy same-origin.
HOST="${TRIGGER_API_HOST:-172.17.0.1}"
PORT="${TRIGGER_API_PORT:-8877}"

[ -x "$VENV/bin/uvicorn" ] || { echo "thiếu $VENV/bin/uvicorn — cài: $VENV/bin/pip install fastapi 'uvicorn[standard]'" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "thiếu $ENV_FILE — cp setup/mlops.env.example \"$ENV_FILE\" rồi điền (nhất là MLOPS_TRIGGER_API_KEY)" >&2; exit 1; }

set -a; . "$ENV_FILE"; set +a
[ -n "${MLOPS_TRIGGER_API_KEY:-}" ] || { echo "thiếu MLOPS_TRIGGER_API_KEY trong $ENV_FILE" >&2; exit 1; }

cd "$HERE"
exec "$VENV/bin/uvicorn" app:app --host "$HOST" --port "$PORT"
