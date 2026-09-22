#!/usr/bin/env bash
# Cài trigger_api thành systemd --user service (không cần sudo) — service kích hoạt MLOps chạy
# nền, tự bật lại khi reboot/crash. Đọc khoá/secret từ ~/.config/secai-mlops/mlops.env qua
# EnvironmentFile (systemd --user KHÔNG kế thừa export của shell — xem install_schedule.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

ENV_FILE="${MLOPS_ENV_FILE:-$HOME/.config/secai-mlops/mlops.env}"
RUN_SH="$ROOT/trigger_api/run.sh"
UNIT_DIR="$HOME/.config/systemd/user"
SERVICE="$UNIT_DIR/secai-mlops-trigger.service"

if [ "$DRY" != "1" ]; then
  [ -f "$ENV_FILE" ] || { echo "[install_trigger_service] thiếu $ENV_FILE — tạo trước (cp setup/mlops.env.example \"$ENV_FILE\"; điền MLOPS_TRIGGER_API_KEY)" >&2; exit 1; }
  MISSING="$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/scripts')
from _lib import common
env = common.read_env_file(__import__('pathlib').Path('$ENV_FILE'))
print('' if env.get('MLOPS_TRIGGER_API_KEY') else 'MLOPS_TRIGGER_API_KEY')
")"
  [ -z "$MISSING" ] || { echo "[install_trigger_service] $ENV_FILE thiếu: $MISSING" >&2; exit 1; }
fi

SERVICE_CONTENT="[Unit]
Description=SecAI MLOps trigger API (kich hoat + theo doi pipeline retraining)
After=network.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
ExecStart=\"$RUN_SH\"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"

if [ "$DRY" = "1" ]; then
  echo "[install_trigger_service] DRY-RUN — sẽ ghi $SERVICE:"
  printf '%s\n' "$SERVICE_CONTENT"
  echo "sẽ chạy: systemctl --user daemon-reload && systemctl --user enable --now secai-mlops-trigger.service"
  exit 0
fi

chmod +x "$RUN_SH"
mkdir -p "$UNIT_DIR"
printf '%s\n' "$SERVICE_CONTENT" > "$SERVICE"
systemctl --user daemon-reload
systemctl --user enable --now secai-mlops-trigger.service
echo "[install_trigger_service] đã cài + khởi động. Kiểm tra: systemctl --user status secai-mlops-trigger.service"
echo "[install_trigger_service] log: journalctl --user -u secai-mlops-trigger.service -f"
echo "[install_trigger_service] gỡ: systemctl --user disable --now secai-mlops-trigger.service && rm '$SERVICE'"
