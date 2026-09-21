#!/usr/bin/env bash
# Cài systemd --user timer chạy run_pipeline.sh THEO ĐÚNG cadence khai báo trong
# configs/pipeline.yml:schedule (mặc định monthly, ngày 15 — lấy nguyên từ chính sách retrain
# đã duyệt trong hồ sơ nghiệm thu, xem mlops/README.md). Không cần sudo (systemd --user).
#
# CẢNH BÁO: timer chạy `run_pipeline.sh --execute --allow-deploy` — TỰ ĐỘNG train/push HF/
# redeploy production nếu drift check phát hiện suy giảm, đúng tinh thần "pipeline MLOps tự
# động". Nếu chỉ muốn drift-check tự động còn train/deploy vẫn làm tay, sửa ExecStart trong
# service file (bỏ --execute --allow-deploy) sau khi cài, hoặc sửa script này trước khi chạy.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

# Sự cố thật 2026-09-18: đặt file này trong $ROOT/setup/ (tức dưới 02. Bo_build_finetuning/)
# từng bị lộ CÔNG KHAI, KHÔNG AUTH — endpoint /showcase-api/finetune/bundle của gateway sống
# (bind 0.0.0.0:8131, cùng máy phục vụ ias.id.vn) zip TOÀN BỘ cây thư mục này để tải về. Từ vụ
# đó: mặc định ĐẶT NGOÀI mọi cây thư mục bị mount/bundle bởi bất kỳ service nào — không đặt lại
# vào $ROOT/setup/ dù chỉ để test.
ENV_FILE="${MLOPS_ENV_FILE:-$HOME/.config/secai-mlops/mlops.env}"

# `systemctl --user` chạy service KHÔNG kế thừa biến môi trường của shell tương tác đã
# `export` — chỉ export tay trước khi chạy install_schedule.sh là chưa đủ, timer tự bắn sau
# này (không có shell nào đang mở) sẽ thiếu HF_TOKEN/JUDGE_BASE_URL/JUDGE_MODEL/JUDGE_API_KEY và chết ngay ở
# `run_gate_eval`/07_push_candidate_to_hf.py. Bắt buộc các biến này nằm trong 1 file riêng nạp
# qua `EnvironmentFile=`, xem mlops/setup/README.md mục 2.
if [ "$DRY" != "1" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    echo "[install_schedule] thiếu $ENV_FILE — tạo trước khi cài timer (không thì timer tự chạy sẽ thiếu HF_TOKEN/JUDGE_BASE_URL/JUDGE_MODEL/JUDGE_API_KEY):" >&2
    echo "  mkdir -p '$(dirname "$ENV_FILE")' && cp '$ROOT/setup/mlops.env.example' '$ENV_FILE' && chmod 600 '$ENV_FILE' && \$EDITOR '$ENV_FILE'" >&2
    exit 1
  fi
  MISSING="$(python3 -c "
import sys
sys.path.insert(0, '$ROOT/scripts')
from _lib import common
env = common.read_env_file(__import__('pathlib').Path('$ENV_FILE'))
required = ['HF_TOKEN', 'JUDGE_BASE_URL', 'JUDGE_MODEL', 'JUDGE_API_KEY']
print(' '.join(k for k in required if not env.get(k)))
")"
  if [ -n "$MISSING" ]; then
    echo "[install_schedule] $ENV_FILE thiếu giá trị cho: $MISSING — điền đủ trước khi cài timer" >&2
    exit 1
  fi
fi

CADENCE_DAY="$(python3 -c "
import sys; sys.path.insert(0, '$ROOT/scripts')
from _lib import common
print(common.load_config()['schedule'].get('day', 15))
")"

UNIT_DIR="$HOME/.config/systemd/user"
SERVICE="$UNIT_DIR/secai-mlops-pipeline.service"
TIMER="$UNIT_DIR/secai-mlops-pipeline.timer"

SERVICE_CONTENT="[Unit]
Description=SecAI MLOps auto-retrain pipeline (CVE moi -> drift -> finetune/DPO -> gate -> deploy)

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
ExecStart=\"$ROOT/scripts/run_pipeline.sh\" --execute --allow-deploy
"

TIMER_CONTENT="[Unit]
Description=Chay secai-mlops-pipeline hang thang ngay $CADENCE_DAY (chinh sach da duyet R-RETRAIN-AUTO)

[Timer]
OnCalendar=*-*-$CADENCE_DAY 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
"

if [ "$DRY" = "1" ]; then
  echo "[install_schedule] DRY-RUN — sẽ ghi 2 file sau rồi enable timer (chưa ghi/chưa cài gì):"
  echo "--- $SERVICE ---"; printf '%s\n' "$SERVICE_CONTENT"
  echo "--- $TIMER ---"; printf '%s\n' "$TIMER_CONTENT"
  echo "sẽ chạy: systemctl --user daemon-reload && systemctl --user enable --now secai-mlops-pipeline.timer"
  exit 0
fi

mkdir -p "$UNIT_DIR"
printf '%s\n' "$SERVICE_CONTENT" > "$SERVICE"
printf '%s\n' "$TIMER_CONTENT" > "$TIMER"
systemctl --user daemon-reload
systemctl --user enable --now secai-mlops-pipeline.timer
echo "[install_schedule] đã cài — kiểm tra: systemctl --user list-timers | grep secai-mlops"
echo "[install_schedule] gỡ: systemctl --user disable --now secai-mlops-pipeline.timer && rm '$SERVICE' '$TIMER'"
