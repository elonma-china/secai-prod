#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  status.sh — toàn cảnh dung lượng project SecAI trong MỘT lần chạy:
#    • 4 container local (secai-docker): trạng thái + cỡ image + DATA thật (bind-mount)
#    • 3 model trên vast (LLM / embedding / rerank): đang serve? + size trên đĩa vast
#    • tổng kết
#
#  Read-only, không đổi gì. Dùng:  ./status.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")"

VAST_IID="${VAST_IID:-48080054}"          # instance vast (đổi khi thuê máy khác)
DATA="/home/ict/AIEngines/secai/secai-data"
C='\033[1;36m'; G='\033[1;32m'; Y='\033[1;33m'; D='\033[0;90m'; N='\033[0m'
hr(){ printf "${D}%s${N}\n" "────────────────────────────────────────────────────────────────────────"; }

# đọc endpoint model từ .env (fallback nếu thiếu)
API_KEY="$(grep -E '^QWEN_API_KEY=' .env 2>/dev/null | cut -d= -f2)"; API_KEY="${API_KEY:-sk-secai2026}"
LLM_URL="$(grep -E '^QWEN_BASE_URL=' .env 2>/dev/null | cut -d= -f2 | sed 's#/v1$##')"
EMB_URL="$(grep -E '^EMBEDDING_API_URL=' .env 2>/dev/null | cut -d= -f2)"
RRK_URL="$(grep -E '^RERANKER_API_URL=' .env 2>/dev/null | cut -d= -f2)"

sz(){ du -sh "$1" 2>/dev/null | cut -f1 || echo '-'; }          # size 1 dir
img_sz(){ docker ps -s --filter "name=$1" --format '{{.Size}}' 2>/dev/null | sed -E 's/.*virtual ([^)]+)\)/\1/'; }
state(){ docker inspect "$1" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' 2>/dev/null || echo 'down'; }
serve(){ curl -s --max-time 8 -H "Authorization: Bearer $API_KEY" "$1/v1/models" \
         | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print('OK' if d else 'empty')" 2>/dev/null || echo 'DOWN'; }

printf "${C}════════════════════════════════════════════════════════════════════════${N}\n"
printf "${C}  SECAI — CONTAINER LOCAL + MODEL VAST — DUNG LƯỢNG${N}\n"
printf "${C}════════════════════════════════════════════════════════════════════════${N}\n"

# ── 1) container local ──────────────────────────────────────────────────────────
printf "\n${C}▸ CONTAINER LOCAL (secai-docker)${N}\n"
printf "%-22s %-14s %-9s %s\n" "NAME" "STATE" "IMAGE" "DATA THẬT (bind-mount)"
hr
printf "%-22s %-14s %-9s %s\n" "secai-docker-gateway" "$(state secai-docker-gateway)" "$(img_sz secai-docker-gateway)" \
       "KB: poc $(sz $DATA/poc) · exploitdb $(sz $DATA/exploitdb) · poc-sources $(sz $DATA/poc-sources)"
printf "%-22s %-14s %-9s storage %s ${D}(+ snapshots %s backup, không tính)${N}\n" "secai-docker-qdrant" \
       "$(state secai-docker-qdrant)" "$(img_sz secai-docker-qdrant)" \
       "$(sz $DATA/_state/qdrant_storage)" "$(sz $DATA/qdrant)"
printf "%-22s %-14s %-9s %s\n" "secai-docker-neo4j"   "$(state secai-docker-neo4j)"   "$(img_sz secai-docker-neo4j)" \
       "data $(sz $DATA/_state/neo4j_data)"
printf "%-22s %-14s %-9s %s\n" "secai-docker-redis"   "$(state secai-docker-redis)"   "$(img_sz secai-docker-redis)" \
       "vol ~0 (chat ephemeral)"

# ── 2) model trên vast ──────────────────────────────────────────────────────────
printf "\n${C}▸ MODEL TRÊN VAST (instance $VAST_IID)${N}\n"
SSH_URL="$(vastai --url https://cloud.vast.ai ssh-url "$VAST_IID" 2>/dev/null)"
declare -A MSZ; VDF=""
if [ -n "$SSH_URL" ]; then
  H="$(sed -E 's#ssh://[^@]+@([^:]+):.*#\1#' <<<"$SSH_URL")"; P="$(sed -E 's#.*:([0-9]+).*#\1#' <<<"$SSH_URL")"
  OUT="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -p "$P" "root@$H" '
    cd /workspace/hf-cache/hub 2>/dev/null || exit 0
    for m in Cyber-F1-AWQ sec-embedding sec-rerank; do echo "$m $(du -sh models--DuyTa--$m 2>/dev/null|cut -f1)"; done
    echo "DF $(df -h / | awk "NR==2{print \$3\"/\"\$2\" (trống \"\$4\")\"}")"' 2>/dev/null)"
  while read -r k v; do [ "$k" = DF ] && VDF="$v" || MSZ[$k]="$v"; done <<<"$OUT"
fi
printf "%-11s %-26s %-6s %s\n" "MODEL" "ENDPOINT" "SERVE" "SIZE (đĩa vast)"
hr
printf "%-11s %-26s %-6s %s\n" "LLM"       "${LLM_URL:-?}" "$(serve "$LLM_URL")" "${MSZ[Cyber-F1-AWQ]:-?}  (DuyTa/Cyber-F1-AWQ)"
printf "%-11s %-26s %-6s %s\n" "EMBEDDING" "${EMB_URL:-?}" "$([ -n "$EMB_URL" ] && serve "$EMB_URL")" "${MSZ[sec-embedding]:-?}  (DuyTa/sec-embedding)"
printf "%-11s %-26s %-6s %s\n" "RERANK"    "${RRK_URL:-?}" "$([ -n "$RRK_URL" ] && serve "$RRK_URL")" "${MSZ[sec-rerank]:-?}  (DuyTa/sec-rerank)"
[ -n "$VDF" ] && printf "${D}   đĩa instance vast: %s${N}\n" "$VDF"

# ── 3) tổng ─────────────────────────────────────────────────────────────────────
printf "\n${C}▸ TỔNG${N}\n"
printf "   %-24s %s\n" "Image/container local:" "~1.3 GB (data để ngoài, không nhân đôi)"
printf "   %-24s %s ${D}(không gồm snapshots)${N}\n" "Data KB local:" "$(du -shc $DATA/_state/qdrant_storage $DATA/_state/neo4j_data $DATA/poc $DATA/poc-sources $DATA/exploitdb 2>/dev/null | tail -1 | cut -f1)"
printf "   %-24s %s\n" "Model trên vast:" "${MSZ[Cyber-F1-AWQ]:-?} + ${MSZ[sec-embedding]:-?} + ${MSZ[sec-rerank]:-?} = ~27 GB"
printf "${G}✓ xong${N}\n"
