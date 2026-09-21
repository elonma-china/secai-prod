#!/usr/bin/env bash
# Thuê 1 GPU TRẦN trên vast.ai để chạy train/DPO/quantize thủ công (SSH vào tự chạy scripts) —
# KHÁC serving-up-cyber-f1.sh (dựng stack vLLM 3 dịch vụ). Tái dùng ensure_tools/ensure_vastai/
# ensure_apikey/$VASTAI của secai-serving-vast/lib.sh (không viết lại phần thuê/xác thực).
#
# Dùng:
#   ./rent_training_gpu.sh --dry-run                      # in offer sẽ chọn, KHÔNG thuê
#   ./rent_training_gpu.sh --yes                           # 1×A100 80GB rẻ nhất (mặc định)
#   ./rent_training_gpu.sh --yes --gpus 1 --gpu-ram-min 80 --max-price 2.5 --disk 150
set -uo pipefail
SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LIB="$(python3 -c "
import sys
sys.path.insert(0, '$SETUP_DIR/../scripts')
from _lib import common
import os
print(os.path.dirname(str(common.resolve_path(common.load_config()['vast_serving']['up_script']))))
")/lib.sh"
[ -f "$LIB" ] || { echo "thiếu $LIB — kiểm tra secai-serving-vast" >&2; exit 1; }
# shellcheck source=/dev/null
. "$LIB"

LABEL="secai-mlops-training"
IMAGE="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel"
GPUS=1; GPU_RAM_MIN=80; MAX_PRICE="3.0"; DISK=150; DRY=0; YES=0
while [ $# -gt 0 ]; do case "$1" in
  --gpus)         GPUS="$2"; shift 2;;
  --gpu-ram-min)  GPU_RAM_MIN="$2"; shift 2;;
  --max-price)    MAX_PRICE="$2"; shift 2;;
  --disk)         DISK="$2"; shift 2;;
  --image)        IMAGE="$2"; shift 2;;
  --yes)          YES=1; shift;;
  --dry-run)      DRY=1; shift;;
  -h|--help) sed -n '2,10p' "$0"; exit 0;;
  *) die "tham số lạ: $1";;
esac; done

say "══ Preflight ══"
ensure_tools; ensure_vastai; ensure_apikey

QUERY="num_gpus=$GPUS gpu_ram>=$GPU_RAM_MIN disk_space>=$DISK dph_total<=$MAX_PRICE inet_down>=500 rentable=true verified=true"
say "══ Search offer ${GPUS}×GPU ≥${GPU_RAM_MIN}GB ≤\$$MAX_PRICE/h ══"
TABLE="$($VASTAI search offers "$QUERY" -o dph_total --raw 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
d=d if isinstance(d,list) else d.get("offers",[])
for i,o in enumerate(d[:10],1):
    print("%2d) id=%-9s %-14s x%s  ram=%3.0fG  disk=%4.0fG  $%.3f/h  rel=%.3f  %s"%(
      i,o["id"],o.get("gpu_name",""),o.get("num_gpus"),o.get("gpu_ram") or 0,
      o.get("disk_space") or 0,o.get("dph_total") or 0,o.get("reliability2") or 0,
      str(o.get("geolocation"))[:20]))')"
[ -n "$TABLE" ] || die "không có offer ${GPUS}×GPU ≥${GPU_RAM_MIN}GB ≤\$$MAX_PRICE/h" "nới --max-price hoặc --gpu-ram-min"
echo "$TABLE"
OFFER="$(echo "$TABLE" | head -1 | sed 's/.*id=\([0-9]*\).*/\1/')"
say "offer chọn: $OFFER"

if [ "$DRY" = "1" ]; then
  say "DRY-RUN — sẽ chạy:"
  echo "  $VASTAI create instance $OFFER --image $IMAGE --disk $DISK --ssh --direct --cancel-unavail --label $LABEL"
  exit 0
fi
if [ "$YES" != "1" ]; then
  { [ -r /dev/tty ] && [ -w /dev/tty ]; } || die "không có TTY để xác nhận" "chạy lại với --yes"
  printf '\033[0;36m[rent]\033[0m Xác nhận thuê offer %s (~\$%s/h trần)? [Enter=có / Ctrl-C=huỷ] ' "$OFFER" "$MAX_PRICE" >/dev/tty
  read -r _ </dev/tty
fi

say "══ Thuê (offer $OFFER) ══"
CREATED="$($VASTAI create instance "$OFFER" --image "$IMAGE" --disk "$DISK" --ssh --direct \
  --cancel-unavail --label "$LABEL" --raw 2>&1)"
IID="$(printf '%s' "$CREATED" | python3 -c 'import re,sys;m=re.search(r"\"new_contract\"\s*:\s*(\d+)",sys.stdin.read());print(m.group(1) if m else "")')"
[ -n "$IID" ] || die "không đọc được instance id: $CREATED" "kiểm tra: $VASTAI show instances"
ok "instance id=$IID (label=$LABEL) — bắt đầu TÍNH TIỀN"

say "══ Chờ SSH sẵn sàng (≤10') ══"
deadline=$(( $(date +%s) + 600 ))
SSH_HOST=""; SSH_PORT=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  read -r host port <<<"$($VASTAI show instance "$IID" --raw 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("- -"); raise SystemExit
print(d.get("ssh_host") or "-", d.get("ssh_port") or "-")')"
  if [ "$host" != "-" ] && [ "$port" != "-" ]; then SSH_HOST="$host"; SSH_PORT="$port"; break; fi
  say "  chờ SSH..."; sleep 15
done
[ -n "$SSH_HOST" ] || die "hết hạn chờ SSH ($IID)" "kiểm tra tay: $VASTAI show instance $IID; huỷ nếu cần: $VASTAI destroy instance $IID -y"

echo
ok "GPU SẴN SÀNG — instance $IID"
echo "──────────────────────────────────────────────────────────────"
echo "  SSH: ssh -p $SSH_PORT root@$SSH_HOST"
echo
echo "  Copy code + cài đặt (chạy từ máy điều phối này):"
echo "    rsync -avz -e 'ssh -p $SSH_PORT' \\"
echo "      \"$SETUP_DIR/../../\" root@$SSH_HOST:/root/Bo_build_finetuning/"
echo "    ssh -p $SSH_PORT root@$SSH_HOST '\\"
echo "      pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu126 && \\"
echo "      pip install -r /root/Bo_build_finetuning/finetune/requirements-train.txt'"
echo
echo "  Huỷ khi xong (TÍNH TIỀN tới lúc huỷ): $VASTAI destroy instance $IID -y"
