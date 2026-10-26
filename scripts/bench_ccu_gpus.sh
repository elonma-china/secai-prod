#!/usr/bin/env bash
# bench_ccu_gpus.sh — chạy locust 30 CCU nhưng GHÌM serving vào N GPU CHỈ trong lúc bench,
# rồi TỰ ĐỘNG trả nginx về đủ GPU khi xong. Luồng sử dụng bình thường không đổi (vẫn N GPU).
#
# Cách hoạt động: serving chạy 1 vLLM replica/GPU, nginx `least_conn` fan ra tất cả replica
# (vast/on_start_cyber_f1.sh sinh upstream N-thành-viên). Script này tạm comment các replica
# GPU >= N trong /etc/nginx/conf.d/vllm.conf trên HOST serving, reload nginx, chạy locust,
# rồi khôi phục conf (trap EXIT) → hệ thống về nguyên trạng.
#
# Dùng:
#   scripts/bench_ccu_gpus.sh                 # 3 GPU, 30 CCU, 90s, host 127.0.0.1:8131
#   GPUS=4 scripts/bench_ccu_gpus.sh          # đo full 4 GPU để so
#   GPUS=3 CCU=30 TIME=90s GW=http://127.0.0.1:8131 scripts/bench_ccu_gpus.sh
#
# Biến (đều có mặc định):
#   GPUS         số GPU dùng cho bench (mặc định 3)         [1..số GPU thật]
#   CCU/RATE/TIME locust -u/-r/-t                           [30/6/90s]
#   GW           gateway base URL đo                       [http://127.0.0.1:8131]
#   SERVING_SSH  user@host host serving (qua tunnel)        [root@217.91.60.211]
#   SERVING_PORT cổng SSH tunnel tới host serving           [17037]
#   CONF         đường dẫn nginx conf trên host serving     [/etc/nginx/conf.d/vllm.conf]
set -euo pipefail

GPUS="${GPUS:-3}"; CCU="${CCU:-30}"; RATE="${RATE:-6}"; TIME="${TIME:-90s}"
GW="${GW:-http://127.0.0.1:8131}"
SERVING_SSH="${SERVING_SSH:-root@217.91.60.211}"; SERVING_PORT="${SERVING_PORT:-17037}"
CONF="${CONF:-/etc/nginx/conf.d/vllm.conf}"

ROOT="/home/ict/AIEngines/secai/secai-agent-gateway"
DIR="$ROOT/docs/nghiem-thu/benchmark-3xA100-2026-09-20"
TS="$(date +%Y%m%d-%H%M%S)"
PREFIX="$DIR/load_30ccu_${GPUS}gpu_$TS"

SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p $SERVING_PORT $SERVING_SSH"

restore() {
  echo "[bench] khôi phục nginx (đủ GPU)…"
  $SSH "cp $CONF.bak-bench $CONF 2>/dev/null || true; nginx -t >/dev/null 2>&1 && nginx -s reload 2>/dev/null || true"
  echo "[bench] xong. Hệ thống về nguyên trạng."
}
trap restore EXIT

echo "[bench] ghìm serving về $GPUS GPU (tạm)…"
# backup (giữ bản gốc, không ghi đè backup cũ) rồi comment các replica GPU>=GPUS.
$SSH "set -e
  cp -n $CONF $CONF.bak-bench
  # server lines look like: '    server 127.0.0.1:1810K ...' (llm 181, embed 180, rerank 182)
  if [ $GPUS -lt 4 ]; then
    sed -i -E 's/^([[:space:]]*server 127\.0\.0\.1:1(80|81|82)0[4-9] )/# \1/' $CONF
  fi
  nginx -t
  nginx -s reload"
echo "[bench] upstream đang dùng:"; $SSH "grep -E '^\s*(#\s*)?server 127' $CONF" | sed 's/^/    /'

echo "[bench] chạy locust: CCU=$CCU rate=$RATE time=$TIME host=$GW"
"$ROOT/.venv/bin/locust" -f "$DIR/locustfile_30ccu.py" --headless \
  -u "$CCU" -r "$RATE" -t "$TIME" --host "$GW" \
  --csv="$PREFIX" --html="${PREFIX}_report.html"

echo "[bench] chỉ số trung bình (${GPUS} GPU):"
tail -1 "${PREFIX}_stats.csv" | awk -F, '{printf "    reqs=%s fail=%s median=%sms avg=%.0fms p95=%sms max=%sms rps=%.2f\n",$3,$4,$5,$6,$17,$8,$10}'
