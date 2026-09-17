#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  fe-tunnel.sh — SSH local-forward BỀN từ máy FE tới gateway :8131 trên box GPU.
#
#  VM-mode KVM instance của vast KHÔNG map cổng tuỳ ý ra public IP (chỉ cổng SSH đã
#  khai lúc tạo). Nên FE (ở máy khác) không gọi thẳng http://<IP>:8131 được. Script
#  này mở SSH local-forward: localhost:8131 (trên máy FE) → 127.0.0.1:8131 (gateway
#  trong box), rồi FE trỏ GATEWAY_URL=http://127.0.0.1:8131. Tự khởi động lại khi
#  kết nối rớt (autossh nếu có, không thì vòng lặp ssh + keepalive).
#
#  KHÔNG phụ thuộc dịch vụ tunnel bên thứ 3 (Cloudflare…) — chỉ dùng đúng cổng SSH
#  vốn đã có, giữ đúng tinh thần air-gap (không mở thêm đường ra internet ngoài).
#
#  Chạy TRÊN MÁY FE:
#     ./fe-tunnel.sh                          # dùng mặc định dưới đây
#     REMOTE_HOST=1.2.3.4 REMOTE_PORT=20700 SSH_KEY=~/.ssh/id_ed25519 ./fe-tunnel.sh
#     LOCAL_PORT=8131 ./fe-tunnel.sh          # đổi cổng nghe cục bộ nếu 8131 bận
#
#  Chạy nền bền (không chết khi logout):  nohup ./fe-tunnel.sh >/tmp/fe-tunnel.log 2>&1 &
#  Hoặc dựng systemd/pm2 gọi script này để bật lại sau reboot máy FE.
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

# ── Cấu hình (override qua biến môi trường) ──────────────────────────────────
REMOTE_HOST="${REMOTE_HOST:-38.65.239.54}"     # public IP của box GPU (vast 48436187)
REMOTE_PORT="${REMOTE_PORT:-20700}"            # cổng SSH đã map (vastai ssh-url)
REMOTE_USER="${REMOTE_USER:-root}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
LOCAL_PORT="${LOCAL_PORT:-8131}"               # cổng nghe trên máy FE → set GATEWAY_URL=http://127.0.0.1:$LOCAL_PORT
GATEWAY_PORT="${GATEWAY_PORT:-8131}"           # cổng gateway trong box (không đổi)
LOCAL_BIND="${LOCAL_BIND:-127.0.0.1}"          # 0.0.0.0 nếu muốn máy khác trong LAN FE dùng chung tunnel

log(){ printf '\033[0;36m[fe-tunnel]\033[0m %s %s\n' "$(date -Is)" "$*"; }
die(){ printf '\033[0;31m[fe-tunnel][FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

[ -f "$SSH_KEY" ] || die "không thấy SSH key: $SSH_KEY (đặt SSH_KEY=... hoặc copy key sang máy FE)"

# ── Tuỳ chọn SSH: keepalive để phát hiện rớt, tự thoát khi forward lỗi ────────
SSH_OPTS=(
  -p "$REMOTE_PORT"
  -i "$SSH_KEY"
  -o StrictHostKeyChecking=accept-new
  -o ExitOnForwardFailure=yes         # nếu :$LOCAL_PORT bận → thoát ngay để loop báo lỗi rõ
  -o ServerAliveInterval=15           # ping mỗi 15s
  -o ServerAliveCountMax=3            # 3 lần không phản hồi → coi như chết, đóng để reconnect
  -o TCPKeepAlive=yes
  -N                                  # không mở shell, chỉ forward
  -L "${LOCAL_BIND}:${LOCAL_PORT}:127.0.0.1:${GATEWAY_PORT}"
  "${REMOTE_USER}@${REMOTE_HOST}"
)

log "forward ${LOCAL_BIND}:${LOCAL_PORT} → ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT} → 127.0.0.1:${GATEWAY_PORT}"
log "FE trỏ: GATEWAY_URL=http://127.0.0.1:${LOCAL_PORT}   (kiểm tra: curl http://127.0.0.1:${LOCAL_PORT}/health)"

# ── autossh nếu có (quản lý reconnect tốt nhất) ──────────────────────────────
if command -v autossh >/dev/null 2>&1; then
  log "dùng autossh (auto-reconnect)."
  export AUTOSSH_GATETIME=0           # reconnect ngay cả khi lần đầu fail nhanh
  exec autossh -M 0 "${SSH_OPTS[@]}"
fi

# ── Fallback: vòng lặp ssh, tự bật lại sau mỗi lần rớt ───────────────────────
log "không có autossh → dùng vòng lặp ssh + keepalive (cài autossh để mượt hơn)."
backoff=2
while true; do
  ssh "${SSH_OPTS[@]}"
  code=$?
  log "tunnel đóng (exit=$code) — bật lại sau ${backoff}s"
  sleep "$backoff"
  backoff=$(( backoff < 30 ? backoff * 2 : 30 ))   # backoff luỹ tiến, trần 30s
done
