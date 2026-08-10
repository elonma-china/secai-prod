#!/usr/bin/env bash
# Chạy Kali executor ở chế độ EPHEMERAL + HARDENING (docs/poc-verify-architecture.md §5).
# Đây là wrapper để test thủ công; khi tích hợp, gateway sẽ tự dựng `docker run` tương đương.
#
#   run.sh <selftest|nuclei|msf-check|pocsuite|raw> [args...]
#
# Biến môi trường:
#   IMAGE        ảnh (mặc định secai-kali-executor:latest)
#   NET          bridge | none        (mặc định bridge — xem cảnh báo egress bên dưới)
#   EXEC_TIMEOUT giây, timeout trong container (mặc định 120)
#   MEM CPUS PIDS giới hạn tài nguyên (mặc định 1g / 2 / 256)
#   MOUNTS       chuỗi "-v host:container:ro ..." để nạp template/PoC vào (read-only)
set -euo pipefail

IMAGE="${IMAGE:-secai-kali-executor:latest}"
NET="${NET:-bridge}"
EXEC_TIMEOUT="${EXEC_TIMEOUT:-120}"
MEM="${MEM:-1g}"; CPUS="${CPUS:-2}"; PIDS="${PIDS:-256}"

[ $# -ge 1 ] || { echo "usage: run.sh <selftest|nuclei|msf-check|pocsuite|raw> [args...]" >&2; exit 2; }

if [ "$NET" = "bridge" ]; then
  echo "[!] NET=bridge: container tới được TARGET *và* internet." >&2
  echo "    Egress 'chỉ-tới-target' (doc §5) cần firewall host — xem README mục Mạng." >&2
fi

exec docker run --rm \
  --name "secai-exec-$$-${RANDOM}" \
  --network "$NET" \
  --read-only \
  --tmpfs /tmp:rw,size=256m,mode=1777 \
  --tmpfs /work:rw,size=256m,mode=1777 \
  --tmpfs /home/runner:rw,size=128m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit "$PIDS" \
  --memory "$MEM" --cpus "$CPUS" \
  -e EXEC_TIMEOUT="$EXEC_TIMEOUT" \
  ${MOUNTS:-} \
  "$IMAGE" "$@"
