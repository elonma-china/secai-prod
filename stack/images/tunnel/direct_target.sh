# direct_target_ok HOST:PORT - luật DUY NHẤT cho đích TUNNEL_MODE=direct (chỉ để test). POSIX sh, source bởi
# images/tunnel/entrypoint.sh (busybox sh trong sidecar) và bootstrap.sh (bash, preflight).
# Nhận: stub-<chữ thường/số/-> (service stub trong secai-net) hoặc IPv4 đủ 4 phần thập phân (không số 0 đứng đầu,
# không dạng rút gọn 127.1) thuộc 10/8, 172.16/12, 192.168/16, 127/8. Mọi dải khác bị từ chối (0/8, 100.64/10,
# 169.254/16, 198.18/15, TEST-NET, 240/4, broadcast, IP công khai, tên máy). Cổng 1..65535, không số 0 đứng đầu.
direct_target_ok() {
    _dt_h=${1%:*}; _dt_p=${1##*:}
    [ "$_dt_h" != "$1" ] || return 1
    case $_dt_p in ''|0*|*[!0-9]*) return 1 ;; esac
    [ ${#_dt_p} -le 5 ] && [ "$_dt_p" -le 65535 ] || return 1
    case $_dt_h in
        stub-*) case ${_dt_h#stub-} in ''|-*|*[!a-z0-9-]*) return 1 ;; esac; return 0 ;;
        *[!0-9.]*|.*|*.|*..*) return 1 ;;
    esac
    _dt_ifs=$IFS; IFS=.; set -- $_dt_h; IFS=$_dt_ifs
    [ $# -eq 4 ] || return 1
    for _dt_o in "$@"; do
        case $_dt_o in 0?*) return 1 ;; esac
        [ ${#_dt_o} -le 3 ] && [ "$_dt_o" -le 255 ] || return 1
    done
    case $1 in
        10|127) return 0 ;;
        192) [ "$2" = 168 ] ;;
        172) [ "$2" -ge 16 ] && [ "$2" -le 31 ] ;;
        *) return 1 ;;
    esac
}
