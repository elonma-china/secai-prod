#!/usr/bin/env bash
# bootstrap.sh init|preflight - chuẩn bị và kiểm tra stack trước `docker compose up`. Không gọi docker.
#   init       tạo .env từ .env.example (0600), sinh secret còn trống, pool, secrets/review-credentials.json.
#              Chạy lại an toàn: chỉ điền giá trị còn trống, không bao giờ ghi đè.
#   preflight  kiểm .env/đường dẫn/máy chủ; mỗi vấn đề một dòng trên stderr (tên biến + cách sửa), thoát != 0 nếu có.
# STACK_DIR đổi thư mục stack (dùng cho test); MAX_MAP_COUNT_FILE đổi nguồn vm.max_map_count (test).
set -euo pipefail

STACK_DIR="$(cd "${STACK_DIR:-$(dirname "${BASH_SOURCE[0]}")}" && pwd)"
ENV_FILE="$STACK_DIR/.env"
CRED_FILE="$STACK_DIR/secrets/review-credentials.json"
GATEWAY_UID=10001   # uid app trong image gateway
GATEWAY_GID=999     # gid chính thật của user app trong image gateway (compose thêm nhóm VM_SHARED_GID qua group_add)

die() { echo "bootstrap.sh: $*" >&2; exit 1; }

# Giá trị cuối cùng của KEY trong .env (docker compose cũng lấy dòng sau cùng); bỏ cặp nháy bao ngoài.
val() {
    local v
    v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r')" || true
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    printf '%s' "$v"
}

# Gán KEY=VALUE chỉ khi hiện trống/thiếu. Giá trị đi qua ENVIRON của awk nên không bị diễn giải escape.
fill() {
    [ -z "$(val "$1")" ] || return 0
    local tmp; tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    if grep -qE "^$1=" "$ENV_FILE"; then
        K="$1" V="$2" awk -F= '$1 == ENVIRON["K"] { print ENVIRON["K"] "=" ENVIRON["V"]; next } { print }' "$ENV_FILE" >"$tmp"
    else
        { cat "$ENV_FILE"; printf '%s=%s\n' "$1" "$2"; } >"$tmp"
    fi
    chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}

rand_hex() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# Tên profile đã bỏ khoảng trắng quanh dấu phẩy ("exploit, translation" vẫn đúng như compose hiểu).
profiles() { val COMPOSE_PROFILES | tr ',' '\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | grep -v '^$' || true; }
has_profile() { profiles | grep -qxF "$1"; }
warn_unknown_profiles() {
    local p
    while IFS= read -r p; do
        case "$p" in exploit|translation|report|review) ;; *) echo "WARNING: COMPOSE_PROFILES: profile lạ '$p' (hợp lệ: exploit, translation, report, review)" >&2 ;; esac
    done < <(profiles)
}

# Đường dẫn tương đối tính từ thư mục stack (compose cũng tính từ thư mục project).
abs_path() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$STACK_DIR" "${1#./}" ;; esac; }

init() {
    [ -f "$STACK_DIR/.env.example" ] || die "thiếu $STACK_DIR/.env.example"
    if [ ! -e "$ENV_FILE" ]; then
        (umask 077; cp "$STACK_DIR/.env.example" "$ENV_FILE")
    fi
    chmod 600 "$ENV_FILE"
    if grep -q $'\r' "$ENV_FILE"; then   # CRLF (sửa trên Windows): chuẩn hoá về LF
        local crlf; crlf="$(mktemp "$ENV_FILE.XXXXXX")"; tr -d '\r' <"$ENV_FILE" >"$crlf"; chmod 600 "$crlf"; mv "$crlf" "$ENV_FILE"
    fi
    warn_unknown_profiles
    local k
    for k in NEO4J_PASSWORD ADMIN_API_KEY LOGS_UI_PASSWORD LOGS_UI_SESSION_SECRET REPORT_API_KEY \
             IAS_SERVER_PASSWORD TRANSLATE_V2_CONVERTER_TOKEN; do
        fill "$k" "$(rand_hex)"
    done
    if has_profile exploit; then
        fill IAS_SERVER_POOL "$(bash "$STACK_DIR/lib/pool.sh" "$(val VM_SLOTS)")"
    fi
    if has_profile review; then fill REVIEW_ENABLED true; fi
    if has_profile translation; then fill TRANSLATION_UPSTREAM_URL http://translation-gateway:18185; fi

    # Tokenizer mặc định: tạo thư mục rỗng để bind mount không bị Docker tạo (root); preflight đòi có tệp bên trong.
    local tok; tok="$(val TOKENIZER_DIR)"
    if [ -z "$tok" ] || [ "$tok" = "./tokenizer" ]; then mkdir -p "$STACK_DIR/tokenizer"; chmod 755 "$STACK_DIR/tokenizer"; fi

    # Bind mount tệp thiếu làm Docker tạo THƯ MỤC; nên luôn tạo tệp này trước `up`. Token thô chỉ in một lần, không lưu.
    if [ -d "$CRED_FILE" ]; then
        die "$CRED_FILE là thư mục (Docker tạo khi tệp thiếu): rmdir nó rồi chạy lại init"
    elif [ ! -e "$CRED_FILE" ]; then
        mkdir -p "$(dirname "$CRED_FILE")"; chmod 755 "$(dirname "$CRED_FILE")"
        python3 -I - "$CRED_FILE" <<'PY'
import hashlib, json, os, secrets, sys
token = secrets.token_hex(32)
doc = {"credentials": [{"token_sha256": hashlib.sha256(token.encode()).hexdigest(), "actor_id": "admin", "role": "admin"}]}
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)  # gateway (uid 10001) trong container phải đọc được
with os.fdopen(fd, "w") as f:
    json.dump(doc, f, indent=2)
os.chmod(sys.argv[1], 0o644)
print("REVIEW_ADMIN_TOKEN=" + token)
print("(token review admin chỉ hiện một lần ở trên; lưu lại ngay, chỉ băm SHA-256 được ghi vào " + sys.argv[1] + ")")
PY
    fi
    echo "init xong: $ENV_FILE (0600). Điền tay các biến còn trống rồi chạy: $0 preflight" >&2
}

ERRORS=()
err() { ERRORS+=("$1"); }

need() { [ -n "$(val "$1")" ] || err "$1: chưa đặt trong .env ($2)"; }

# Image phải có tag cố định: không :latest, không để placeholder MISSING của compose.
check_image() {
    local v; v="$(val "$1")"
    if [ -z "$v" ] || [[ "$v" == *MISSING* ]]; then err "$1: chưa đặt (đang trống/placeholder MISSING); dựng hoặc nạp image rồi ghi tag vào .env"; return; fi
    if [[ "$v" == *:latest ]]; then err "$1: '$v' dùng :latest; dùng tag cố định (sha/ngày)"; return; fi
    [[ "${v##*/}" == *:* || "$v" == *@sha256:* ]] || err "$1: '$v' không có tag; thêm tag cố định"
}

# Gateway (uid 10001, gid 999 + nhóm VM_SHARED_GID) ghi được $1 khi: chủ là uid 10001 có quyền ghi chủ,
# hoặc nhóm là 999/VM_SHARED_GID có quyền ghi nhóm, hoặc others-writable (chấp nhận nhưng cảnh báo).
check_gateway_writable() {
    local dir="$1" label="$2" uid gid mode shared
    shared="$(val VM_SHARED_GID)"; shared="${shared:-10100}"
    read -r uid gid mode < <(stat -c '%u %g %a' "$dir")
    if [ "$uid" = "$GATEWAY_UID" ] && (( 8#$mode & 8#200 )); then return 0; fi
    if (( 8#$mode & 8#020 )) && { [ "$gid" = "$GATEWAY_GID" ] || [ "$gid" = "$shared" ]; }; then return 0; fi
    if (( 8#$mode & 8#002 )); then
        echo "WARNING: $label: $dir (uid $uid gid $gid mode $mode) ai cũng ghi được; nên siết: sudo chown -R $GATEWAY_UID:$shared $dir && sudo chmod -R g+w $dir" >&2
        return 0
    fi
    err "$label: $dir (uid $uid gid $gid mode $mode) gateway (uid $GATEWAY_UID, gid $GATEWAY_GID, nhóm $shared) không ghi được; chạy: sudo chown -R $GATEWAY_UID:$shared $dir && sudo chmod -R g+w $dir  (hoặc sudo chmod -R o+w $dir)"
}

preflight() {
    [ -f "$ENV_FILE" ] || die "chưa có $ENV_FILE: chạy '$0 init' trước"
    local k
    for k in QWEN_BASE_URL QWEN_API_KEY QWEN_MODEL EMBEDDING_API_URL EMBEDDING_API_KEY EMBEDDING_MODEL \
             RERANKER_API_URL RERANKER_API_KEY RERANKER_MODEL NEO4J_PASSWORD; do
        need "$k" "nhập tay"
    done
    warn_unknown_profiles
    check_image GATEWAY_IMAGE

    local root; root="$(val DATA_ROOT)"
    if [ -z "$root" ]; then err "DATA_ROOT: chưa đặt trong .env (đường dẫn tuyệt đối tới gói dữ liệu)"
    elif [ ! -d "$root" ]; then err "DATA_ROOT: '$root' không tồn tại hoặc không phải thư mục"
    else
        for k in exploitdb poc poc-sources; do
            [ -d "$root/$k" ] || err "DATA_ROOT: thiếu thư mục $root/$k (giải nén gói dữ liệu)"
        done
        if [ -d "$root/poc" ]; then check_gateway_writable "$root/poc" DATA_ROOT; fi
        if [ ! -d "$root/poc/store" ]; then err "DATA_ROOT: thiếu thư mục $root/poc/store (gói dữ liệu cung cấp, hoặc mkdir; không có thì Docker tạo root-owned và gateway không ghi được PoC)"
        else check_gateway_writable "$root/poc/store" DATA_ROOT; fi
    fi

    if has_profile exploit; then
        check_image VM_IMAGE
        [ -n "$root" ] && [ -d "$root/exploitdb-mcp-server" ] || err "DATA_ROOT: thiếu ${root:-<DATA_ROOT>}/exploitdb-mcp-server (nằm NGOÀI secai-data trong cây nguồn; gói dữ liệu (data package) phải cung cấp)"
        need IAS_SERVER_PASSWORD "để trống thì engine không xác thực; init sinh"
        need IAS_SERVER_POOL "chạy '$0 init' sau khi đặt VM_SLOTS"
        local want; want="$(bash "$STACK_DIR/lib/pool.sh" "$(val VM_SLOTS)" 2>/dev/null)" || err "VM_SLOTS: '$(val VM_SLOTS)' không hợp lệ (số nguyên 1..99)"
        if [ -n "${want:-}" ] && [ -n "$(val IAS_SERVER_POOL)" ] && [ "$(val IAS_SERVER_POOL)" != "$want" ]; then
            err "IAS_SERVER_POOL: không khớp VM_SLOTS=$(val VM_SLOTS); xoá dòng IAS_SERVER_POOL trong .env rồi chạy init"
        fi
    fi
    if has_profile translation; then
        need TRANSLATE_V2_CONVERTER_TOKEN "để trống thì converter từ chối mọi yêu cầu; init sinh"
        check_image CONVERTER_IMAGE
        for k in TRANSLATE_V2_OCR_BASE_URL TRANSLATE_V2_OCR_MODEL TRANSLATE_V2_SPECIALIST_BASE_URL \
                 TRANSLATE_V2_SPECIALIST_MODEL MULTILANG_SPECIALIST_BASE_URL MULTILANG_SPECIALIST_MODEL; do
            need "$k" "nhập tay"
        done
        local tok; tok="$(abs_path "$(val TOKENIZER_DIR)")"
        [ -n "$(val TOKENIZER_DIR)" ] || tok="$STACK_DIR/tokenizer"
        for k in tokenizer.json tokenizer_config.json; do
            [ -r "$tok/$k" ] || err "TOKENIZER_DIR: không đọc được $tok/$k (đặt tokenizer HY-MT2 vào thư mục này)"
        done
    fi
    if has_profile report; then
        need REPORT_API_KEY "để trống thì API báo cáo trả 404; init sinh"
    fi
    if has_profile review; then
        check_image REVIEW_UI_IMAGE
        [ -f "$CRED_FILE" ] || err "secrets/review-credentials.json: $CRED_FILE phải là TỆP (thiếu hoặc là thư mục); chạy '$0 init'"
    fi

    local mm; mm="$(cat "${MAX_MAP_COUNT_FILE:-/proc/sys/vm/max_map_count}" 2>/dev/null || echo 0)"
    (( mm >= 262144 )) || err "vm.max_map_count=$mm < 262144 (Elasticsearch không khởi động); chạy: sudo sysctl -w vm.max_map_count=262144"

    if [ "${#ERRORS[@]}" -gt 0 ]; then
        printf '%s\n' "${ERRORS[@]}" >&2
        echo "preflight: ${#ERRORS[@]} vấn đề" >&2
        exit 1
    fi
    echo "preflight: OK" >&2
}

case "${1:-}" in
    init) init ;;
    preflight) preflight ;;
    *) echo "usage: bootstrap.sh init|preflight" >&2; exit 2 ;;
esac
