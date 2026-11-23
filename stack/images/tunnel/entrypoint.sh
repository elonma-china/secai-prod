#!/bin/sh
# specialist-tunnel: mở 127.0.0.1:18868 (Hy-MT2) và 127.0.0.1:18083 (OCR) trong netns của translation-gateway
# (compose.yml: network_mode service:translation-gateway), để gateway giữ nguyên chặn SSRF "http chỉ loopback".
#   TUNNEL_MODE=ssh     ssh -L tới loopback máy model; khoá/known_hosts ở $TUNNEL_DIR; ssh chết thì nối lại.
#   TUNNEL_MODE=off     không làm gì (URL dịch là https://<IP private>); giữ container sống.
#   TUNNEL_MODE=direct  CHỈ ĐỂ TEST: socat tới host:cổng stub trong secai-net (không mã hoá, không xác thực máy).
set -eu
D="${TUNNEL_DIR:-/run/tunnel}"
RETRY="${TUNNEL_RETRY_SECONDS:-5}"
child=""
trap '[ -z "$child" ] || kill $child 2>/dev/null; exit 0' TERM INT
die() { echo "specialist-tunnel: $*" >&2; exit 64; }
nap() { sleep "$1" & child=$!; wait "$child" || true; child=""; }
# translation-gateway khởi động lại thì netns cũ chỉ còn lo: thoát để Docker khởi động lại sidecar và nhập netns mới.
netns_ok() { for i in "${TUNNEL_NET_DIR:-/sys/class/net}"/*; do [ "${i##*/}" = lo ] || return 0; done; return 1; }
# watch PID...: trả về khi một PID thoát; netns mất thì giết các PID rồi thoát lỗi.
watch() {
    while :; do
        for p in "$@"; do kill -0 "$p" 2>/dev/null || return 0; done
        netns_ok || { kill "$@" 2>/dev/null || true; die "netns của translation-gateway đã mất (gateway dịch khởi động lại); thoát để Docker nối lại"; }
        sleep 5 & child="$* $!"; wait $! || true; child="$*"
    done
}

run_ssh() {
    [ -n "${TUNNEL_SSH_HOST:-}" ] || die "TUNNEL_SSH_HOST chưa đặt"
    [ -n "${TUNNEL_SSH_USER:-}" ] || die "TUNNEL_SSH_USER chưa đặt"
    [ -r "$D/key" ] || die "không đọc được $D/key (chủ phải là uid $(id -u), mode 0600)"
    [ -s "$D/known_hosts" ] || die "$D/known_hosts thiếu hoặc rỗng: phải ghim khoá máy model"
    while :; do
        netns_ok || die "netns của translation-gateway đã mất; thoát để Docker nối lại"
        ssh -F /dev/null -N -T -i "$D/key" -p "${TUNNEL_SSH_PORT:-22}" -l "$TUNNEL_SSH_USER" \
            -o BatchMode=yes -o IdentitiesOnly=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
            -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$D/known_hosts" -o GlobalKnownHostsFile=/dev/null \
            -o UpdateHostKeys=no -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
            -o ConnectTimeout=10 \
            -L "127.0.0.1:18868:127.0.0.1:${TUNNEL_REMOTE_SPECIALIST_PORT:-18868}" \
            -L "127.0.0.1:18083:127.0.0.1:${TUNNEL_REMOTE_OCR_PORT:-18083}" \
            -- "$TUNNEL_SSH_HOST" &
        pid=$!; child=$pid; watch "$pid"; rc=0; wait "$pid" || rc=$?; child=""
        echo "specialist-tunnel: ssh thoát (rc=$rc); nối lại sau ${RETRY}s" >&2
        nap "$RETRY"
    done
}

# Luật đích direct dùng chung với bootstrap.sh (preflight): một định nghĩa.
. "$(dirname "$0")/direct_target.sh"

run_direct() {
    echo "specialist-tunnel: TUNNEL_MODE=direct CHỈ ĐỂ TEST (stub), không dùng cho máy thật" >&2
    # Phòng thủ lớp hai (preflight cũng chặn): direct vượt chặn SSRF loopback-only của gateway.
    [ "${TUNNEL_DIRECT_ALLOW_TEST:-}" = 1 ] || die "TUNNEL_MODE=direct cần TUNNEL_DIRECT_ALLOW_TEST=1 (chỉ để test)"
    [ -n "${TUNNEL_DIRECT_SPECIALIST:-}" ] || die "TUNNEL_DIRECT_SPECIALIST chưa đặt (host:cổng)"
    direct_target_ok "$TUNNEL_DIRECT_SPECIALIST" || die "TUNNEL_DIRECT_SPECIALIST '$TUNNEL_DIRECT_SPECIALIST': chỉ stub-<tên>:cổng hoặc IPv4 private/loopback đủ 4 phần"
    [ -z "${TUNNEL_DIRECT_OCR:-}" ] || direct_target_ok "$TUNNEL_DIRECT_OCR" || die "TUNNEL_DIRECT_OCR '$TUNNEL_DIRECT_OCR': chỉ stub-<tên>:cổng hoặc IPv4 private/loopback đủ 4 phần"
    netns_ok || die "netns của translation-gateway đã mất; thoát để Docker nối lại"
    socat TCP-LISTEN:18868,bind=127.0.0.1,reuseaddr,fork "TCP:$TUNNEL_DIRECT_SPECIALIST" & a=$!
    b=""
    if [ -n "${TUNNEL_DIRECT_OCR:-}" ]; then
        socat TCP-LISTEN:18083,bind=127.0.0.1,reuseaddr,fork "TCP:$TUNNEL_DIRECT_OCR" & b=$!
    fi
    child="$a $b"; watch $a $b
    kill $a $b 2>/dev/null || true
    die "socat thoát"
}

case "${TUNNEL_MODE:-ssh}" in
    ssh) run_ssh ;;
    direct) run_direct ;;
    off) echo "specialist-tunnel: TUNNEL_MODE=off, không mở tunnel" >&2; while :; do nap 3600; done ;;
    *) die "TUNNEL_MODE='${TUNNEL_MODE}' không hợp lệ (ssh | off | direct)" ;;
esac
