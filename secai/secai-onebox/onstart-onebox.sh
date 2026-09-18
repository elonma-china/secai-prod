#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  onstart-onebox.sh — CHẠY BÊN TRONG máy vast.ai VM (Ubuntu 22.04, 2×A100).
#
#  Idempotent: cài Docker + Compose plugin + NVIDIA Container Toolkit, rồi `up` cả
#  khối secai-onebox. KHÔNG kéo data (dùng seed-data.sh cho việc đó). An toàn chạy lại.
#
#  Điều kiện: đã có sẵn ở /opt/secai/secai-onebox (compose+.env+nginx+templates) và
#  /opt/secai/secai-agent-gateway (source để build gateway image). up-onebox.sh lo
#  việc upload 2 thư mục này trước khi gọi script.
#
#  Dùng:  sudo bash /opt/secai/secai-onebox/onstart-onebox.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

OB_DIR="${OB_DIR:-/opt/secai/secai-onebox}"
DATA_ROOT="${DATA_ROOT:-/data/secai}"
log(){ printf '\033[0;36m[onstart]\033[0m %s\n' "$*"; }
die(){ printf '\033[0;31m[onstart][FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "cần chạy bằng root (sudo)."
[ -f "$OB_DIR/docker-compose.yml" ] || die "không thấy $OB_DIR/docker-compose.yml — upload thư mục secai-onebox trước."
[ -f "$OB_DIR/../secai-agent-gateway/Dockerfile" ] || die "không thấy source gateway ở $OB_DIR/../secai-agent-gateway — cần build context."

export DEBIAN_FRONTEND=noninteractive

# ── 1) Docker Engine + Compose plugin ────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  log "cài Docker Engine..."
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  log "Docker đã có: $(docker --version)"
fi

# ── 2) NVIDIA Container Toolkit (để container thấy GPU) ───────────────────────
if ! docker info 2>/dev/null | grep -qi nvidia && ! command -v nvidia-ctk >/dev/null 2>&1; then
  log "cài NVIDIA Container Toolkit..."
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq
  apt-get install -y -qq nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
else
  log "NVIDIA Container Toolkit đã cấu hình."
fi

# Kiểm tra GPU nhìn thấy trong container.
log "kiểm tra GPU trong container..."
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L \
  || die "container không thấy GPU — kiểm tra NVIDIA driver/toolkit."

# ── 3) Thư mục data trên Local Volume ────────────────────────────────────────
log "tạo cây data dưới $DATA_ROOT (Local Volume)..."
mkdir -p "$DATA_ROOT"/{qdrant_storage,qdrant_snapshots,neo4j_data,neo4j_logs,redis,gw-data,exploitdb,poc/store,poc-sources,hf-cache,bm25_local_model}
# neo4j chạy uid 7474; qdrant/redis chạy root trong image nên không cần chown thêm.
chown -R 7474:7474 "$DATA_ROOT/neo4j_data" "$DATA_ROOT/neo4j_logs" 2>/dev/null || true

# ── 4) Build + up ────────────────────────────────────────────────────────────
cd "$OB_DIR"
log "docker compose build gateway + secai-serving; pull datastore images..."
docker compose build gateway secai-serving
docker compose pull qdrant neo4j redis 2>/dev/null || true   # secai-serving là build:, không pull
log "docker compose up -d (lần đầu: secai-serving tự dò GPU + cold-load weights, có thể mất nhiều phút)..."
docker compose up -d

log "trạng thái:"
docker compose ps
cat <<EOF

──────────────────────────────────────────────────────────────────────────────
 Khối đã 'up'. secai-serving tự phát hiện GPU + cold-load model (theo dõi:
 docker compose logs -f secai-serving). Sau khi healthy, gateway phục vụ ở :8131
 (đọc port ngoài qua vast IP Port Info, hoặc SSH local-forward nếu không map trực tiếp).
 Nếu chưa seed data: chạy seed-data.sh từ máy local để đẩy qdrant/neo4j/poc/hf-cache lên $DATA_ROOT.
──────────────────────────────────────────────────────────────────────────────
EOF
