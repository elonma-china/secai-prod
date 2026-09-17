#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  seed-data.sh — đẩy ~23GB data (qdrant/neo4j/exploitdb/poc/poc-sources) từ máy
#  LOCAL lên Local Volume của máy vast (rsync qua SSH). Chạy TỪ máy local.
#
#  Nguồn mặc định: /home/ict/AIEngines/secai/secai-data
#  Đích: <DATA_ROOT trên VM, mặc định /data/secai> — khớp DATA_ROOT trong .env.
#
#  LƯU Ý NHẤT QUÁN: nên DỪNG stack DB local trước khi rsync để bản sao qdrant/neo4j
#  không dính trạng thái đang ghi (WAL/segment). Dùng --stop-local để tự dừng
#  secai-docker (qdrant+neo4j) trước, rồi bật lại sau.
#
#  Dùng:
#    ./seed-data.sh --host <IP> --port <SSH_PORT> --key ~/.ssh/id_ed25519 [--data-root /data/secai]
#    ./seed-data.sh --host ... --port ... --key ... --skip-poc-sources   # re-clone poc-sources trên VM thay vì upload 13GB
#
#  ⚠ AIR-GAP: KHÔNG dùng --skip-poc-sources cho deploy air-gap — flag đó re-clone
#  poc-sources từ GitHub trên VM (cần internet lúc chạy). Luôn upload đủ 13GB.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SRC="${SECAI_DATA_SRC:-/home/ict/AIEngines/secai/secai-data}"
DATA_ROOT="/data/secai"
HOST=""; PORT="22"; KEY=""; SKIP_POC_SOURCES=0; STOP_LOCAL=0
DOCKER_DIR="/home/ict/AIEngines/secai/secai-docker"
log(){ printf '\033[0;36m[seed]\033[0m %s\n' "$*"; }
die(){ printf '\033[0;31m[seed][FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do case "$1" in
  --host) HOST="$2"; shift 2;;
  --port) PORT="$2"; shift 2;;
  --key)  KEY="$2"; shift 2;;
  --data-root) DATA_ROOT="$2"; shift 2;;
  --src)  SRC="$2"; shift 2;;
  --skip-poc-sources) SKIP_POC_SOURCES=1; shift;;
  --stop-local) STOP_LOCAL=1; shift;;
  -h|--help) sed -n '2,22p' "$0"; exit 0;;
  *) die "tham số lạ: $1";;
esac; done

[ -n "$HOST" ] || die "thiếu --host <IP máy vast>"
[ -n "$KEY" ]  || die "thiếu --key <đường dẫn SSH private key>"
[ -d "$SRC" ]  || die "không thấy thư mục nguồn data: $SRC"
command -v rsync >/dev/null || die "cần rsync (apt-get install -y rsync)"

SSH="ssh -p $PORT -i $KEY -o StrictHostKeyChecking=accept-new"
RSH="ssh -p $PORT -i $KEY -o StrictHostKeyChecking=accept-new"
RSYNC(){ rsync -ah --info=progress2 --partial -e "$RSH" "$@"; }

log "đích: root@$HOST:$DATA_ROOT (port $PORT)"
$SSH "root@$HOST" "mkdir -p $DATA_ROOT/{qdrant_storage,neo4j_data,neo4j_logs,redis,gw-data,exploitdb,poc/store,poc-sources,bm25_local_model}" \
  || die "không SSH/mkdir được trên máy vast — kiểm tra host/port/key."

if [ "$STOP_LOCAL" = "1" ]; then
  log "dừng DB local (secai-docker qdrant+neo4j) để bản sao nhất quán..."
  ( cd "$DOCKER_DIR" && docker compose stop qdrant neo4j ) || log "  (bỏ qua: secai-docker không chạy)"
fi

# ── qdrant LIVE storage (7.7G) — KHÔNG dùng snapshot cũ cve_kb; đây là cve_kb_hybrid ──
log "[1/6] qdrant_storage (collection cve_kb_hybrid, ~7.7G)..."
RSYNC "$SRC/_state/qdrant_storage/" "root@$HOST:$DATA_ROOT/qdrant_storage/"

# ── neo4j LIVE (1.1G) ────────────────────────────────────────────────────────
log "[2/6] neo4j_data (~1.1G)..."
RSYNC "$SRC/_state/neo4j_data/" "root@$HOST:$DATA_ROOT/neo4j_data/"
$SSH "root@$HOST" "chown -R 7474:7474 $DATA_ROOT/neo4j_data $DATA_ROOT/neo4j_logs" || true

# ── exploitdb (0.37G) + poc (1G) ─────────────────────────────────────────────
log "[3/6] exploitdb (~0.37G)..."
RSYNC "$SRC/exploitdb/" "root@$HOST:$DATA_ROOT/exploitdb/"
log "[4/6] poc catalog + store (~1G)..."
RSYNC "$SRC/poc/" "root@$HOST:$DATA_ROOT/poc/"

# ── poc-sources (13G) — upload hoặc re-clone trên VM ─────────────────────────
if [ "$SKIP_POC_SOURCES" = "1" ]; then
  log "[5/6] BỎ QUA upload poc-sources (dùng --skip-poc-sources). Re-clone trên VM nếu cần recon PoC nguồn."
  log "  ⚠ air-gap: re-clone cần internet lúc chạy trên VM — KHÔNG dùng flag này nếu muốn air-gap thật."
else
  log "[5/6] poc-sources (~13G — phần lâu nhất)..."
  RSYNC "$SRC/poc-sources/" "root@$HOST:$DATA_ROOT/poc-sources/"
fi

# ── bm25_local_model (8K) — fallback offline cho fastembed Qdrant/bm25 (bm25.py
# tự dùng specific_model_path khi thư mục này tồn tại → không gọi HF Hub) ─────
log "[6/6] bm25_local_model (fallback offline fastembed)..."
RSYNC "$SRC/_state/bm25_local_model/" "root@$HOST:$DATA_ROOT/bm25_local_model/"

if [ "$STOP_LOCAL" = "1" ]; then
  log "bật lại DB local..."
  ( cd "$DOCKER_DIR" && docker compose start qdrant neo4j ) || true
fi

log "DONE. Trên VM: cd /opt/secai/secai-onebox && docker compose restart qdrant neo4j gateway"
log "Kiểm chứng: curl -s http://localhost:6333/collections/cve_kb_hybrid | python3 -m json.tool"
