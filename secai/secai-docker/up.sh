#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  up.sh — build + khởi động bộ Docker thống nhất `secai-docker`
#          (gateway + qdrant + neo4j + redis), rồi in danh sách container của dự án.
#
#  LƯU Ý: script này KHÔNG dừng dịch vụ nào. Nếu port 6333/7687/6379 đang bị các
#  container cũ (compose `secai-data`, `redis-ai`) chiếm, hãy giải phóng TRƯỚC bằng
#  ./down-legacy.sh (hoặc dừng thủ công) — data ở bind-mount nên không mất.
#
#  Gateway container mặc định ra host port 8140 (coexist với native ias.id.vn :8131).
#  Muốn container chiếm 8131: tự `sudo systemctl stop secai-gateway` rồi
#  `GATEWAY_HOST_PORT=8131 ./up.sh`.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

say(){ printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok(){  printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m! %s\033[0m\n' "$*"; }

[ -f .env ] || { echo "thiếu .env"; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a
DATA="${SECAI_DATA_ROOT:?SECAI_DATA_ROOT chưa set trong .env}"

say "kiểm tra data dir bind-mount"
for d in "_state/qdrant_storage" "_state/neo4j_data" "qdrant" "exploitdb" "poc" "poc-sources"; do
  [ -e "$DATA/$d" ] && ok "$d" || warn "thiếu $DATA/$d (mount có thể rỗng)"
done

say "build image gateway + up stack"
docker compose --env-file .env up -d --build

say "chờ healthy (tối đa ~120s)"
deadline=$(( $(date +%s) + 120 ))
while :; do
  bad="$(docker compose ps --format '{{.Name}} {{.Health}} {{.State}}' 2>/dev/null \
        | awk '$2!="healthy" && !($2=="" && $3=="running"){c++} END{print c+0}')"
  [ "${bad:-1}" = "0" ] && { ok "tất cả service đã healthy/running"; break; }
  [ "$(date +%s)" -ge "$deadline" ] && { warn "hết thời gian chờ — in trạng thái hiện tại"; break; }
  sleep 4
done

echo
say "DANH SÁCH CONTAINER CỦA DỰ ÁN (secai-docker)"
docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.State}}\t{{.Status}}\t{{.Ports}}'
echo
GW_PORT="${GATEWAY_HOST_PORT:-8140}"
ok "gateway: http://localhost:${GW_PORT}/health   (native ias.id.vn vẫn ở :8131)"
