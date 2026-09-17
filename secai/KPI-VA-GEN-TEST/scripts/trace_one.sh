#!/usr/bin/env bash
# trace_one.sh — wrapper nạp basic-auth creds rồi gọi trace_one.py.
#
# Creds (OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD) chỉ sống trong env của
# container backend, KHÔNG có trên đĩa. Wrapper này nạp chúng vào biến môi trường và
# chuyển thẳng cho tiến trình Python — KHÔNG in ra stdout/stderr, KHÔNG ghi ra file.
# Python chỉ dùng để dựng header Authorization Basic.
#
# Thứ tự nguồn creds:
#   1) đã export sẵn trong env  -> dùng luôn
#   2) file ./.env.deploy (nếu có, chứa SECAI_SERVER_PASSWORD)
#   3) đọc từ container đang chạy (docker exec printenv) — mặc định
#
# Dùng:
#   bash kpi/scripts/trace_one.sh --id acc_dd_0001
#   bash kpi/scripts/trace_one.sh --question "CVE-2021-44228 là gì?"
#   CONTAINER=secai-backend-1 bash kpi/scripts/trace_one.sh --id acc_dd_0002
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CONTAINER="${CONTAINER:-secai-backend-1}"

# (2) nạp từ .env.deploy nếu tồn tại (không in giá trị)
if [[ -f "$REPO/.env.deploy" ]]; then
  set -a; . "$REPO/.env.deploy"; set +a
  export OPENCODE_SERVER_PASSWORD="${OPENCODE_SERVER_PASSWORD:-${SECAI_SERVER_PASSWORD:-}}"
fi

export OPENCODE_SERVER_USERNAME="${OPENCODE_SERVER_USERNAME:-Fable5}"

# (3) fallback: đọc password từ container (command substitution — không in ra)
if [[ -z "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
  if command -v docker >/dev/null 2>&1; then
    OPENCODE_SERVER_PASSWORD="$(docker exec "$CONTAINER" printenv OPENCODE_SERVER_PASSWORD 2>/dev/null || true)"
    export OPENCODE_SERVER_PASSWORD
    U="$(docker exec "$CONTAINER" printenv OPENCODE_SERVER_USERNAME 2>/dev/null || true)"
    [[ -n "$U" ]] && export OPENCODE_SERVER_USERNAME="$U"
  fi
fi

if [[ -z "${OPENCODE_SERVER_PASSWORD:-}" ]]; then
  echo "[trace_one.sh] CẢNH BÁO: không lấy được OPENCODE_SERVER_PASSWORD" >&2
  echo "  -> export thủ công, hoặc kiểm tra container '$CONTAINER' đang chạy." >&2
fi

exec python3 "$HERE/trace_one.py" "$@"
