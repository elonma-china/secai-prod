#!/usr/bin/env bash
# eval-vast.sh up|run|collect|down|status - chạy thử gói stack trên MỘT máy Vast VM thuê rẻ, rồi huỷ. Luồng:
#   up [--dry-run] [--i-accept-higher]   tìm offer rẻ nhất hợp lệ, thuê, GHI ID NGAY, đặt deadline, dựng image + nạp dữ liệu
#   run       bootstrap init/preflight + restore Qdrant + compose up + check.sh all + acceptance/run-api.sh (trên instance)
#   collect   kéo log/JSON kết quả về $STACK_EVAL_DIR/results-<ts>/ (KHÔNG .env, KHÔNG dữ liệu) và kiểm sha256
#   (review độc lập bên ngoài script)
#   down      destroy-eval.sh --yes  (huỷ đúng ID trong state.json)
#   status    id/label/giờ còn lại/giá, đối chiếu `vastai show instances-v1` (không parse được thì fail an toàn)
# Ngân sách chủ dự án: giá <= $0.30/h (chặn cứng ở search), tối đa 3 giờ. Cờ --i-accept-higher mới cho EVAL_MAX_DPH > 0.30 / EVAL_MAX_HOURS > 3.
# Biến: EVAL_ENV_FILE (BẮT BUỘC cho up; .env do operator chỉ định, script không đọc .env prod), EVAL_DATA_PKG (thư mục gói dữ liệu từ
#   pack.sh data, BẮT BUỘC), EVAL_SSH_KEY (~/.ssh/id_ed25519), EVAL_MAX_DPH (0.30), EVAL_MAX_HOURS (3), EVAL_MIN_RAM_GB (32; 48 nếu
#   EVAL_EXPLOIT=1), EVAL_EXPLOIT=1 (bật profile exploit + build image VM), EVAL_CONTAINER_API_KEY (khoá GIỚI HẠN THEO INSTANCE cho lớp A;
#   không có thì bỏ lớp A), EVAL_TEMPLATE (98fff0d0a96048e83c19da789fe7a979, VM Ubuntu), STACK_EVAL_DIR (local/stack-eval),
#   EVAL_REPO_ROOT (nguồn git archive), EVAL_POLL_SLEEP (15).
# Deadline: lớp B = timer systemd-run --user +max_hours; lớp D = timer dự phòng +10 phút; lớp EXIT = trap huỷ khi `up` lỗi sau khi đã thuê;
#   lớp A = tự huỷ trên instance (chỉ khi có khoá instance). Mọi lớp huỷ đều đi qua destroy-eval.sh. Không bao giờ đưa khoá API TÀI KHOẢN
#   Vast lên máy thuê; secret chỉ đi qua stdin/tệp 0600, không qua argv.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${EVAL_REPO_ROOT:-$(cd "$HERE/.." && pwd)}"
SD="${STACK_EVAL_DIR:-$REPO/local/stack-eval}"
KEY="${EVAL_SSH_KEY:-$HOME/.ssh/id_ed25519}"
POLL="${EVAL_POLL_SLEEP:-15}"
TEMPLATE="${EVAL_TEMPLATE:-98fff0d0a96048e83c19da789fe7a979}"
REMOTE=/opt/secai-stack
HARD_DENY=62088958

die() { echo "eval-vast: $*" >&2; exit 1; }
say() { echo "eval-vast: $*"; }
nap() { sleep "$POLL"; }
need_cmds() { local c; for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "thiếu lệnh '$c' trong PATH"; done; }

# sget KEY -> giá trị trong state.json (rỗng nếu thiếu)
sget() { python3 -I -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2],""); print(v if not isinstance(v,(list,dict)) else json.dumps(v))' "$SD/state.json" "$1" 2>/dev/null; }
# sset KEY=JSONVALUE... -> ghi vào state.json (nguyên tử)
sset() { python3 -I - "$SD/state.json" "$@" <<'PY'
import json, os, sys
p = sys.argv[1]
d = json.load(open(p)) if os.path.exists(p) else {}
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1); d[k] = json.loads(v)
t = p + ".tmp"; open(t, "w").write(json.dumps(d)); os.chmod(t, 0o600); os.replace(t, p)
PY
}
# vlist -> JSON danh sách instance; rc != 0 nếu vastai lỗi/DEPRECATED/không phải JSON danh sách (không đoán).
vlist() { vastai show instances-v1 --raw 2>/dev/null | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
if isinstance(d, dict): d = d.get("instances")
assert isinstance(d, list)
print(json.dumps(d))' 2>/dev/null; }

ssh_opts() { SSHO=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=30); }
load_conn() {
    [ -f "$SD/state.json" ] || die "chưa có state ($SD/state.json); chạy 'up' trước"
    HOST="$(sget ssh_host)"; PORT="$(sget ssh_port)"
    [ -n "$HOST" ] && [[ "$PORT" =~ ^[0-9]+$ ]] || die "state thiếu ssh_host/ssh_port (up chưa xong?)"
    ssh_opts
}
rssh() { ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" "$@"; }

# ---------------------------------------------------------------------------------------------------- up
cmd_up() {
    local dry=0 higher=0 a
    for a in "$@"; do case "$a" in --dry-run) dry=1 ;; --i-accept-higher) higher=1 ;; *) die "tham số lạ: $a" ;; esac; done
    local cap="${EVAL_MAX_DPH:-0.30}" hours="${EVAL_MAX_HOURS:-3}" ram="${EVAL_MIN_RAM_GB:-}"
    [[ "$cap" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "EVAL_MAX_DPH không hợp lệ: $cap"
    [[ "$hours" =~ ^[1-9][0-9]*$ ]] || die "EVAL_MAX_HOURS phải là số nguyên >= 1: $hours"
    if [ "$higher" != 1 ]; then
        awk -v c="$cap" 'BEGIN { exit !(c <= 0.30) }' || die "EVAL_MAX_DPH=$cap vượt ngân sách chốt \$0.30/h; muốn vượt phải thêm --i-accept-higher"
        [ "$hours" -le 3 ] || die "EVAL_MAX_HOURS=$hours vượt ngân sách chốt 3 giờ; muốn vượt phải thêm --i-accept-higher"
    fi
    [ -n "$ram" ] || { ram=32; [ "${EVAL_EXPLOIT:-0}" != 1 ] || ram=48; }
    need_cmds vastai python3

    # Offer rẻ nhất thoả điều kiện; lọc LẠI ở phía client nên giá > cap bị chặn dù vastai trả gì.
    local query="dph_total<=$cap cpu_ram>=$((ram * 1024)) disk_space>=100 inet_down>=200 reliability>=0.98 rentable=true verified=true"
    local offers pick OFFER PRICE
    offers="$(vastai search offers "$query" -o dph_total --raw 2>/dev/null)" || die "vastai search lỗi"
    pick="$(printf '%s' "$offers" | python3 -I -c '
import json, sys
cap, ram = float(sys.argv[1]), float(sys.argv[2])
try:
    d = json.load(sys.stdin)
    d = d if isinstance(d, list) else d.get("offers", [])
except Exception:
    sys.exit(2)
ok = [o for o in d if isinstance(o, dict) and isinstance(o.get("dph_total"), (int, float)) and o["dph_total"] <= cap
      and (o.get("cpu_ram") or 0) >= ram * 1024 and (o.get("disk_space") or 0) >= 100 and (o.get("inet_down") or 0) >= 200
      and (o.get("reliability2") or o.get("reliability") or 0) >= 0.98]
if not ok: sys.exit(1)
b = min(ok, key=lambda o: o["dph_total"]); print(b["id"], b["dph_total"])' "$cap" "$ram")" \
        || die "không có offer nào <= \$$cap/h, RAM >= ${ram}GB, disk >= 100GB, mạng >= 200Mbps, reliability >= 0.98 (hoặc vastai trả dữ liệu lạ); không thuê"
    read -r OFFER PRICE <<<"$pick"
    [[ "$OFFER" =~ ^[0-9]+$ ]] || die "offer id lạ: '$OFFER'"
    local label="secai-stack-eval-$(date +%Y%m%d)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    local create=(vastai create instance "$OFFER" --template_hash "$TEMPLATE" --disk 100 --label "$label" --ssh --direct --raw)
    say "offer $OFFER \$$PRICE/h (cap \$$cap/h, tối đa ${hours}h => tối đa ~\$$(awk -v p="$PRICE" -v h="$hours" 'BEGIN{printf "%.2f", p*h}'))"
    if [ "$dry" = 1 ]; then say "DRY-RUN, không thuê. Lệnh sẽ chạy: ${create[*]}"; return 0; fi

    [ -f "${EVAL_ENV_FILE:-}" ] || die "đặt EVAL_ENV_FILE = tệp .env cho instance (script không tự đọc .env prod)"
    [ -d "${EVAL_DATA_PKG:-}" ] && [ -f "$EVAL_DATA_PKG/SHA256SUMS" ] || die "đặt EVAL_DATA_PKG = thư mục gói dữ liệu (pack.sh data) có SHA256SUMS"
    [ -f "$KEY" ] || die "không thấy khoá ssh $KEY (EVAL_SSH_KEY)"
    need_cmds ssh scp rsync systemd-run git
    mkdir -p "$SD"; chmod 700 "$SD"
    if [ -f "$SD/state.json" ]; then
        [ -n "$(sget destroyed_at)" ] || die "state.json cũ chưa huỷ (id $(sget id)); chạy 'down' hoặc 'status' trước"
        mv "$SD/state.json" "$SD/state-$(date +%s).json"; rm -f "$SD/instance.id"
    fi
    [ -f "$SD/protected-instances" ] || (umask 077; echo "$HARD_DENY" >"$SD/protected-instances")
    echo "CẢNH BÁO: $EVAL_ENV_FILE (chứa API key) sẽ được CHUYỂN sang máy thuê ngoài của Vast. Xoay các khoá đó sau khi eval xong."

    # --- thuê. Từ đây mọi lỗi phải huỷ instance (trap EXIT).
    local out ID="" t
    out="$("${create[@]}" 2>&1)"; local rc=$?
    ID="$(printf '%s' "$out" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("new_contract",""))
except Exception: pass' 2>/dev/null)"
    if [ "$rc" != 0 ] || ! [[ "$ID" =~ ^[1-9][0-9]*$ ]]; then
        ID=""
        say "create trả lỗi/không rõ (rc=$rc); tìm instance mồ côi theo label $label"
        for t in 1 2 3; do
            ID="$(vlist | python3 -I -c 'import json,sys
for i in json.load(sys.stdin):
    if i.get("label") == sys.argv[1]: print(i["id"]); break' "$label")" || die "create lỗi và danh sách vastai không đọc được: KIỂM TAY console Vast, tìm label $label"
            [ -z "$ID" ] || break
            nap
        done
        [ -n "$ID" ] || die "create lỗi và không có instance nào mang label $label (không thuê gì)"
    fi
    [[ "$ID" =~ ^[1-9][0-9]*$ ]] && [ "$ID" != "$HARD_DENY" ] || die "ID lạ sau create: '$ID'; kiểm tay console, label $label"
    # GHI ID NGAY, trước mọi bước khác.
    (umask 077; printf '%s\n' "$ID" >"$SD/instance.id")
    local now; now="$(date +%s)"
    sset "id=\"$ID\"" "label=\"$label\"" "created_at=$now" "deadline=$((now + hours * 3600))" "price=$PRICE" \
         "max_dph=$cap" "max_hours=$hours" "timers=[]"
    chmod 600 "$SD/state.json"
    DONE=0; CREATED=1; trap 'on_exit_up' EXIT
    say "đã thuê instance $ID ($label); ID ghi vào $SD/instance.id"

    # --- deadline: lớp B và D (bắt buộc), rồi lớp A nếu có khoá instance.
    local tag unit timers=() secs
    for tag in B D; do
        secs=$((hours * 3600)); [ "$tag" = D ] && secs=$((secs + 600))
        unit="secai-eval-destroy-$tag-$ID-$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        local envs=(--setenv=STACK_EVAL_DIR="$SD" --setenv=PATH="$PATH")
        [ -z "${STACK_EVAL_PROTECTED:-}" ] || envs+=(--setenv=STACK_EVAL_PROTECTED="$STACK_EVAL_PROTECTED")
        systemd-run --user --unit="$unit" --on-active="$secs" "${envs[@]}" "$HERE/destroy-eval.sh" --force-deadline >/dev/null 2>&1 \
            || die "không đặt được timer deadline $tag (không có deadline thì không để máy chạy)"
        timers+=("\"$unit\""); sset "timers=[$(IFS=,; echo "${timers[*]}")]"
    done
    say "deadline cứng $(date -d "@$((now + hours * 3600))" '+%F %T') (lớp B) và +10 phút (lớp D); timers: ${timers[*]//\"/}"

    # --- chờ máy chạy + ssh
    local host="" port="" n info
    for n in $(seq 1 60); do
        info="$(vlist | python3 -I -c 'import json,sys
for i in json.load(sys.stdin):
    if str(i.get("id")) == sys.argv[1] and i.get("actual_status") == "running":
        h = i.get("ssh_host") or i.get("public_ipaddr"); p = i.get("ssh_port") or 22
        if h: print(h, p)' "$ID")" || info=""
        [ -z "$info" ] || break
        nap
    done
    read -r host port <<<"$info"
    [ -n "$host" ] || die "instance $ID chưa running/chưa có ssh sau khi chờ"
    sset "ssh_host=\"$host\"" "ssh_port=$port"
    HOST="$host"; PORT="$port"; ssh_opts
    for n in $(seq 1 40); do rssh true 2>/dev/null && break; nap; [ "$n" != 40 ] || die "ssh vào $host:$port không được"; done

    if [ -n "${EVAL_CONTAINER_API_KEY:-}" ]; then
        # Lớp A: khoá giới hạn THEO INSTANCE đi qua stdin vào tệp 0600; instance tự DELETE chính nó khi hết hạn.
        printf '%s' "$EVAL_CONTAINER_API_KEY" | rssh "umask 077; cat > /root/.vast-instance-key; \
(setsid nohup sh -c 'sleep $((hours * 3600 + 300)); curl -fsS -X DELETE -H \"Authorization: Bearer \$(cat /root/.vast-instance-key)\" https://console.vast.ai/api/v0/instances/$ID/' >/dev/null 2>&1 &)" \
            || die "không cài được lớp A"
        say "Layer A (tự huỷ trên instance): BẬT (+5 phút sau deadline B)"
    else
        say "Layer A (tự huỷ trên instance): BỎ - không có EVAL_CONTAINER_API_KEY (khoá giới hạn theo instance, không phải khoá tài khoản). Chỉ còn lớp B, D và EXIT."
    fi

    # --- đưa mã, dữ liệu, .env; dựng image trên instance
    local sha tgz; sha="$(git -C "$REPO" rev-parse --short=8 HEAD)" || die "EVAL_REPO_ROOT không phải repo git"
    tgz="$(mktemp --suffix=.tgz)"
    git -C "$REPO" archive --format=tar.gz -o "$tgz" HEAD || die "git archive lỗi"
    scp "${SSHO[@]}" -P "$port" "$tgz" "root@$host:/root/secai-src.tgz" >/dev/null || { rm -f "$tgz"; die "scp tarball lỗi"; }
    rm -f "$tgz"
    rssh "mkdir -p $REMOTE && tar -xzf /root/secai-src.tgz -C $REMOTE && mkdir -p $REMOTE/pkg" || die "giải nén mã lỗi"
    rsync -a -e "ssh ${SSHO[*]} -p $port" "$EVAL_DATA_PKG"/ "root@$host:$REMOTE/pkg/" || die "rsync gói dữ liệu lỗi"
    rssh "umask 077; cat > $REMOTE/stack/.env" <"$EVAL_ENV_FILE" || die "đẩy .env lỗi"
    rssh "SHA=$sha EXPLOIT=${EVAL_EXPLOIT:-0} bash -s" <<'REMOTE_PROVISION' || die "provision lỗi"
set -euo pipefail
cd /opt/secai-stack
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
command -v zstd >/dev/null || { apt-get update -qq && apt-get install -y -qq zstd; }
sysctl -w vm.max_map_count=262144 >/dev/null
docker build -t "secai-agent-gateway:eval-$SHA" .
docker build -t "secai-converter:eval-$SHA" stack/images/converter
docker build -t "secai-review-ui:eval-$SHA" stack/images/review-ui
P="translation,report,review"
if [ "$EXPLOIT" = 1 ]; then
    docker build -f stack/images/pentestcode-vm/Dockerfile --build-arg GIT_SHA="$SHA" -t "secai-pentestcode-vm:eval-$SHA" .
    P="exploit,$P"
fi
mkdir -p /srv/secai-data
# giá trị cuối trong .env thắng (compose và bootstrap.sh đều lấy dòng sau cùng)
cat >> stack/.env <<EOF
DATA_ROOT=/srv/secai-data
GATEWAY_IMAGE=secai-agent-gateway:eval-$SHA
VM_IMAGE=secai-pentestcode-vm:eval-$SHA
CONVERTER_IMAGE=secai-converter:eval-$SHA
REVIEW_UI_IMAGE=secai-review-ui:eval-$SHA
COMPOSE_PROFILES=$P
EOF
REMOTE_PROVISION
    DONE=1
    say "xong 'up'. Tiếp: eval-vast.sh run -> collect -> (review độc lập) -> down. Deadline cứng ở trên."
}

on_exit_up() {
    local rc=$?
    trap - EXIT
    if [ "${CREATED:-0}" = 1 ] && [ "${DONE:-0}" != 1 ]; then
        echo "!!!!!! up THẤT BẠI sau khi đã thuê instance: HUỶ NGAY !!!!!!" >&2
        STACK_EVAL_DIR="$SD" "$HERE/destroy-eval.sh" --yes || echo "!!!!!! HUỶ THẤT BẠI: kiểm console Vast ngay (instance $(sget id)); timer deadline vẫn chạy !!!!!!" >&2
    fi
    exit "$rc"
}

# ---------------------------------------------------------------------------------------------------- run / collect / down / status
cmd_run() {
    need_cmds ssh; load_conn
    # Chạy trên instance; mọi bước ghi log vào $REMOTE/results, kết thúc bằng SHA256SUMS. Không có secret trong script.
    rssh "bash -s" <<'REMOTE_RUN'
set -uo pipefail
cd /opt/secai-stack/stack
R=/opt/secai-stack/results; mkdir -p "$R"; rc=0; ABORT=0
step() { local name=$1; shift; [ "$ABORT" = 0 ] || { echo "$name skipped" >>"$R/steps.txt"; return 0; }
         "$@" >"$R/$name.log" 2>&1; local r=$?; echo "$name rc=$r" | tee -a "$R/steps.txt"; [ "$r" = 0 ] || rc=1; return "$r"; }
abort_on_fail() { "$@" || ABORT=1; }
DATA_ROOT=$(grep -E '^DATA_ROOT=' .env | tail -n1 | cut -d= -f2-); export DATA_ROOT
abort_on_fail step init ./bootstrap.sh init
abort_on_fail step preflight ./bootstrap.sh preflight
abort_on_fail step restore_files ./restore-data.sh ../pkg
abort_on_fail step qdrant_up docker compose up -d --wait qdrant
if [ "$ABORT" = 0 ]; then
    QIP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q qdrant)")
    abort_on_fail step restore_qdrant ./restore-data.sh --qdrant-url "http://$QIP:6333" ../pkg
fi
step up docker compose up -d --wait
step check ./check.sh all
step acceptance env ACCEPT_OUT="$R/acceptance" ./acceptance/run-api.sh http://127.0.0.1:8131
(cd "$R" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + >SHA256SUMS)
exit "$rc"
REMOTE_RUN
}

cmd_collect() {
    need_cmds ssh rsync sha256sum; load_conn
    local dest="$SD/results-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$dest"
    # Chỉ thư mục results (log + JSON); loại .env/khoá phòng khi lọt vào.
    rsync -a --exclude '.env*' --exclude '*.key' -e "ssh ${SSHO[*]} -p $PORT" "root@$HOST:$REMOTE/results/" "$dest/" || die "kéo kết quả lỗi"
    [ -s "$dest/SHA256SUMS" ] || die "thiếu/rỗng SHA256SUMS trong kết quả ($dest)"
    (cd "$dest" && sha256sum -c --strict --quiet SHA256SUMS) || die "SHA256 kết quả KHÔNG khớp ($dest)"
    say "kết quả đã kiểm sha256: $dest"
}

cmd_down() { exec env STACK_EVAL_DIR="$SD" "$HERE/destroy-eval.sh" --yes; }

cmd_status() {
    need_cmds vastai python3
    [ -f "$SD/state.json" ] || die "chưa có state ($SD/state.json)"
    local id label price deadline now list found
    id="$(sget id)"; label="$(sget label)"; price="$(sget price)"; deadline="$(sget deadline)"; now="$(date +%s)"
    list="$(vlist)" || die "danh sách vastai không đọc được (DEPRECATED/lỗi); không đoán trạng thái, kiểm tay console"
    echo "id=$id label=$label price=\$$price/h giờ còn lại=$(awk -v d="$deadline" -v n="$now" 'BEGIN{printf "%.2f", (d-n)/3600}')"
    found="$(printf '%s' "$list" | python3 -I -c 'import json,sys
for i in json.load(sys.stdin):
    if str(i.get("id")) == sys.argv[1]: print(i.get("label",""), i.get("dph_total",""), i.get("actual_status","")); break' "$id")"
    if [ -z "$found" ]; then echo "vastai: instance $id KHÔNG còn (đã huỷ)"; return 0; fi
    echo "vastai: $found"
    case "$found" in "$label "*) ;; *) die "label thật khác state: CẢNH BÁO, không dùng 'down' trước khi kiểm tay" ;; esac
}

case "${1:-}" in
    up) shift; cmd_up "$@" ;;
    run) cmd_run ;;
    collect) cmd_collect ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *) echo "usage: eval-vast.sh up [--dry-run] [--i-accept-higher] | run | collect | down | status" >&2; exit 2 ;;
esac
