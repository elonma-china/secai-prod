#!/bin/sh
# Healthcheck: đầu tunnel 127.0.0.1:18868 có nghe không (chuyển tới đầu xa do check.sh health kiểm).
[ "${TUNNEL_MODE:-ssh}" = off ] && exit 0
exec socat -u OPEN:/dev/null TCP:127.0.0.1:18868,connect-timeout=3
