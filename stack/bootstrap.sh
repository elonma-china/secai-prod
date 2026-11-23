#!/usr/bin/env bash
# bootstrap.sh init|preflight - chuẩn bị và kiểm tra stack trước `docker compose up`. Chỉ gọi docker để đọc
# (`docker image inspect` của TUNNEL_IMAGE), không bao giờ đổi gì.
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
    # specialist-tunnel chạy bằng uid của người vận hành (chủ tệp khoá 0600); image dựng với cùng --build-arg TUNNEL_UID.
    fill TUNNEL_UID "$(id -u)"
    local tdir; tdir="$(val TUNNEL_SECRETS_DIR)"
    if [ -z "$tdir" ] || [ "$tdir" = "./secrets/tunnel" ]; then mkdir -p "$STACK_DIR/secrets/tunnel"; chmod 700 "$STACK_DIR/secrets/tunnel"; fi

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

# Quyền ghi của gateway (uid 10001, gid 999 + nhóm $4) theo POSIX: chỉ MỘT lớp được áp dụng.
# In ok (ghi được), warn (chỉ nhờ others-writable) hoặc no. Args: uid gid mode(bát phân) shared_gid.
gateway_write_class() {
    local uid="$1" gid="$2" mode="$3" shared="$4"
    if [ "$uid" = "$GATEWAY_UID" ]; then (( 8#$mode & 8#200 )) && echo ok || echo no
    elif [ "$gid" = "$GATEWAY_GID" ] || [ "$gid" = "$shared" ]; then (( 8#$mode & 8#020 )) && echo ok || echo no
    else (( 8#$mode & 8#002 )) && echo warn || echo no
    fi
}

# stat -L: Docker mount đích của symlink nên phải xét đích, không xét chính liên kết.
# STAT_FAKE="uid gid mode" thay đầu ra stat (CHỈ để test, không dùng khi chạy thật).
check_gateway_writable() {
    local dir="$1" label="$2" uid gid mode shared
    shared="$(val VM_SHARED_GID)"; shared="${shared:-10100}"
    read -r uid gid mode <<<"${STAT_FAKE:-$(stat -L -c '%u %g %a' "$dir")}"
    case "$(gateway_write_class "$uid" "$gid" "$mode" "$shared")" in
        ok) return 0 ;;
        warn) echo "WARNING: $label: $dir (uid $uid gid $gid mode $mode) ai cũng ghi được; nên siết: sudo chown -R $GATEWAY_UID:$shared $dir && sudo chmod -R g+w $dir" >&2; return 0 ;;
    esac
    err "$label: $dir (uid $uid gid $gid mode $mode) gateway (uid $GATEWAY_UID, gid $GATEWAY_GID, nhóm $shared) không ghi được; chạy: sudo chown -R $GATEWAY_UID:$shared $dir && sudo chmod -R g+w $dir  (hoặc sudo chmod -R o+w $dir)"
}

# URL dịch vụ dịch chuyên dụng: gateway (translation/specialist.py, multilang/provider.py) chỉ nhận http tới loopback hoặc
# https tới IP private, có cổng, không path/user/query; sai thì translation-gateway/worker chết lúc khởi động.
check_specialist_url() {
    local v; v="$(val "$1")"; [ -n "$v" ] || return 0
    python3 -I - "$v" <<'PY' && return 0
import ipaddress, sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1].rstrip("/"))
try:
    a = ipaddress.ip_address(u.hostname or ""); loop = a.is_loopback; priv = loop or a.is_private
except ValueError:
    loop = priv = u.hostname == "localhost"
try:
    port = u.port
except ValueError:
    port = None
ok = priv and (u.scheme == "https" or (u.scheme == "http" and loop)) and port is not None \
    and not (u.username or u.password or u.query or u.fragment) and u.path in ("", "/")
sys.exit(0 if ok else 1)
PY
    err "$1: gateway chỉ nhận http://127.0.0.1:<cổng> hoặc https://<IP private>:<cổng> (không path/user/query); trên mạng bridge 127.0.0.1 là chính container, nên dùng sidecar specialist-tunnel (TUNNEL_MODE=ssh) hoặc https"
}

is_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }

# URL có phải http(s)://127.0.0.1|localhost|[::1] không, và cổng của nó (rỗng nếu không có).
url_loopback() { [[ "$1" =~ ^https?://(127\.[0-9.]+|localhost|\[::1\])(:|/|$) ]]; }
SPEC_URL=http://127.0.0.1:18868   # đầu tunnel cố định trong netns translation-gateway (đúng chuỗi này, không localhost/[::1]/https)
OCR_URL=http://127.0.0.1:18083
STUB_KEY=stub-test-key-not-secret  # khoá giả cố định của acceptance/stubs/compose.stubs.yml

# direct (chỉ test) chỉ được tới stub-* trong secai-net hoặc IPv4 private/loopback: không bao giờ ra Internet.
direct_target_ok() {
    [[ "$1" =~ ^([A-Za-z0-9._-]+):([0-9]+)$ ]] || return 1
    local h="${BASH_REMATCH[1]}"
    is_port "${BASH_REMATCH[2]}" || return 1
    [[ "$h" =~ ^stub-[a-z0-9-]+$ ]] && return 0
    python3 -I -c 'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); sys.exit(0 if a.version == 4 and (a.is_private or a.is_loopback) and not a.is_link_local else 1)' "$h" 2>/dev/null
}
url_port() { [[ "$1" =~ ^[a-z]+://[^/]*:([0-9]+)/?$ ]] && printf '%s' "${BASH_REMATCH[1]}"; }

# Sidecar specialist-tunnel (compose.yml): mở 127.0.0.1:18868 (Hy-MT2) và 127.0.0.1:18083 (OCR) trong netns của
# translation-gateway/worker. TUNNEL_MODE ssh (mặc định) | direct (chỉ để test, socat tới stub) | off (URL https IP private).
check_tunnel() {
    local mode k v
    mode="$(val TUNNEL_MODE)"; mode="${mode:-ssh}"
    check_image TUNNEL_IMAGE
    check_tunnel_uid
    case "$mode" in ssh|direct|off) ;; *) err "TUNNEL_MODE: '$mode' không hợp lệ (ssh | off | direct (chỉ để test))"; return ;; esac
    if [ "$mode" = off ]; then
        for k in TRANSLATE_V2_SPECIALIST_BASE_URL MULTILANG_SPECIALIST_BASE_URL TRANSLATE_V2_OCR_BASE_URL; do
            if url_loopback "$(val "$k")"; then
                err "$k: trỏ loopback nhưng TUNNEL_MODE=off; trên mạng bridge không có gì nghe ở 127.0.0.1 của container. Bật TUNNEL_MODE=ssh hoặc dùng https://<IP private>:<cổng>"
            fi
        done
        return
    fi
    # Tunnel bật: dịch vụ chuyên dụng phải đi qua đầu tunnel cố định; OCR nếu loopback cũng vậy.
    for k in TRANSLATE_V2_SPECIALIST_BASE_URL MULTILANG_SPECIALIST_BASE_URL; do
        v="$(val "$k")"
        [ -z "$v" ] || [ "$v" = "$SPEC_URL" ] \
            || err "$k: TUNNEL_MODE=$mode thì phải đúng $SPEC_URL (đầu tunnel trong netns translation-gateway)"
    done
    v="$(val TRANSLATE_V2_OCR_BASE_URL)"
    if url_loopback "$v" && [ "$v" != "$OCR_URL" ]; then
        err "TRANSLATE_V2_OCR_BASE_URL: loopback thì phải đúng $OCR_URL (đầu tunnel OCR)"
    fi
    if [ "$mode" = direct ]; then
        echo "WARNING: TUNNEL_MODE=direct CHỈ ĐỂ TEST (socat tới stub trong secai-net, không mã hoá, không xác thực máy); máy thật dùng ssh" >&2
        # direct vượt chặn SSRF loopback-only của gateway và gửi khoá Bearer qua TCP thô: phải bật tường minh,
        # chỉ tới stub/IP private, và chỉ với khoá stub giả.
        [ "$(val TUNNEL_DIRECT_ALLOW_TEST)" = 1 ] \
            || err "TUNNEL_DIRECT_ALLOW_TEST: TUNNEL_MODE=direct chỉ để test, phải đặt TUNNEL_DIRECT_ALLOW_TEST=1 tường minh (máy thật dùng ssh)"
        for k in TUNNEL_DIRECT_SPECIALIST TUNNEL_DIRECT_OCR; do
            v="$(val "$k")"
            if [ -z "$v" ]; then
                [ "$k" = TUNNEL_DIRECT_OCR ] && [ "$(val TRANSLATE_V2_OCR_BASE_URL)" != "$OCR_URL" ] && continue
                err "$k: TUNNEL_MODE=direct cần host:cổng (ví dụ ${k#TUNNEL_DIRECT_}: stub-specialist:18868 / stub-ocr:18083)"
                continue
            fi
            direct_target_ok "$v" || err "$k: '$v' không được: direct chỉ tới stub-*:<cổng> hoặc IPv4 private/loopback:<cổng>"
        done
        for k in TRANSLATE_V2_SPECIALIST_API_KEY MULTILANG_SPECIALIST_API_KEY TRANSLATE_V2_OCR_API_KEY; do
            v="$(val "$k")"
            [ -z "$v" ] || [ "$v" = "$STUB_KEY" ] || err "$k: TUNNEL_MODE=direct chỉ dùng khoá stub giả '$STUB_KEY' (không gửi khoá thật qua TCP thô); dùng .env riêng cho stub"
        done
        return
    fi
    need TUNNEL_SSH_HOST "máy model (IP/tên) mà sidecar ssh -L tới loopback của nó"
    need TUNNEL_SSH_USER "user ssh trên máy model (chỉ cần quyền port-forward)"
    v="$(val TUNNEL_SSH_HOST)"
    [ -z "$v" ] || [[ "$v" =~ ^[A-Za-z0-9._:-]+$ ]] || err "TUNNEL_SSH_HOST: '$v' không hợp lệ (chỉ IP hoặc tên máy, không user@/cổng)"
    for k in TUNNEL_SSH_PORT TUNNEL_REMOTE_SPECIALIST_PORT TUNNEL_REMOTE_OCR_PORT; do
        v="$(val "$k")"; [ -z "$v" ] || is_port "$v" || err "$k: '$v' không phải cổng 1..65535"
    done
    local dir uid key kh host port mode_bits owner
    dir="$(val TUNNEL_SECRETS_DIR)"; dir="$(abs_path "${dir:-./secrets/tunnel}")"
    uid="$(val TUNNEL_UID)"; uid="${uid:-10002}"
    key="$dir/key"; kh="$dir/known_hosts"
    if [ ! -f "$key" ]; then
        err "TUNNEL_SECRETS_DIR: thiếu khoá $key (ssh-keygen -t ed25519 -N '' -f $key; đưa $key.pub vào authorized_keys của máy model, nên kèm restrict,port-forwarding,permitopen=\"127.0.0.1:18868\",permitopen=\"127.0.0.1:18083\")"
    else
        read -r owner mode_bits <<<"$(stat -L -c '%u %a' "$key")"
        (( (8#$mode_bits & 8#077) == 0 )) || err "TUNNEL_SECRETS_DIR: $key mode $mode_bits; phải 0600 (chmod 600 $key)"
        [ "$owner" = "$uid" ] || err "TUNNEL_UID: $key thuộc uid $owner nhưng sidecar chạy uid $uid; đặt TUNNEL_UID=$owner và dựng lại image tunnel với --build-arg TUNNEL_UID=$owner"
    fi
    host="$(val TUNNEL_SSH_HOST)"; port="$(val TUNNEL_SSH_PORT)"; port="${port:-22}"
    [ "$port" = 22 ] || host="[$host]:$port"
    if [ ! -s "$kh" ]; then
        err "TUNNEL_SECRETS_DIR: thiếu hoặc rỗng $kh (ghim khoá máy model: ssh-keyscan -p $port <host> > $kh, rồi đối chiếu fingerprint)"
    elif ! awk -v h="$host" '$1 ~ /^\|1\|/ { found=1 } { n=split($1, a, ","); for (i=1;i<=n;i++) if (a[i]==h) found=1 } END { exit !found }' "$kh"; then
        err "TUNNEL_SECRETS_DIR: known_hosts không có dòng cho $host (StrictHostKeyChecking=yes sẽ từ chối); ssh-keyscan -p $port <host> >> $kh"
    fi
}

# TUNNEL_UID: số, khác 0, và bằng USER của image tunnel nếu image đã có (chỉ đọc `docker image inspect`).
check_tunnel_uid() {
    local uid img iu
    uid="$(val TUNNEL_UID)"
    if ! [[ "$uid" =~ ^[0-9]+$ ]] || (( 10#$uid == 0 )); then
        err "TUNNEL_UID: '$uid' phải là số uid khác 0 (init đặt \$(id -u)); sidecar không chạy root"; return
    fi
    img="$(val TUNNEL_IMAGE)"
    [ -n "$img" ] && [[ "$img" != *MISSING* ]] || return 0
    if command -v docker >/dev/null 2>&1 && iu="$(docker image inspect -f '{{.Config.User}}' "$img" 2>/dev/null)"; then
        [ "${iu%%:*}" = "$uid" ] || err "TUNNEL_UID: $uid nhưng image $img chạy user '$iu'; dựng lại với --build-arg TUNNEL_UID=$uid"
    else
        echo "NOTICE: TUNNEL_IMAGE: $img chưa có trên máy này; bỏ qua so khớp TUNNEL_UID với USER của image" >&2
    fi
}

# llm_var X: tên biến LLM đang dùng: LLM_X (chuẩn, config.py) nếu có, không thì QWEN_X (tên cũ) nếu có, không thì LLM_X.
llm_var() { if [ -n "$(val "LLM_$1")" ] || [ -z "$(val "QWEN_$1")" ]; then printf 'LLM_%s' "$1"; else printf 'QWEN_%s' "$1"; fi; }

preflight() {
    [ -f "$ENV_FILE" ] || die "chưa có $ENV_FILE: chạy '$0 init' trước"
    local k
    for k in BASE_URL API_KEY MODEL CONTEXT_WINDOW; do   # không in giá trị (có khoá API)
        if [ -n "$(val "LLM_$k")" ] && [ -n "$(val "QWEN_$k")" ] && [ "$(val "LLM_$k")" != "$(val "QWEN_$k")" ]; then
            echo "WARNING: LLM_$k và QWEN_$k (tên cũ) khác nhau; gateway và VM dùng LLM_$k. Xoá QWEN_$k cho khỏi nhầm" >&2
        fi
    done
    for k in BASE_URL API_KEY MODEL; do need "$(llm_var "$k")" "nhập tay (tên cũ QWEN_$k vẫn nhận)"; done
    for k in EMBEDDING_API_URL EMBEDDING_API_KEY EMBEDDING_MODEL \
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
        [ "$(val VERIFY_ALLOW_ALL)" = true ] || need VERIFY_ALLOWED_TARGETS "host/CIDR lab được phép khai thác; trống thì mọi yêu cầu exploit-v2 bị 403"
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
        check_specialist_url TRANSLATE_V2_SPECIALIST_BASE_URL
        check_specialist_url MULTILANG_SPECIALIST_BASE_URL
        check_tunnel
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
