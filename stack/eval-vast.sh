#!/usr/bin/env bash
# eval-vast.sh up|run|collect|down|status - chạy thử gói stack trên MỘT máy Vast VM thuê rẻ, rồi huỷ. Luồng:
#   up [--dry-run] [--i-accept-higher]   tìm offer rẻ nhất hợp lệ, thuê, GHI ID NGAY, đặt deadline, dựng image + nạp dữ liệu
#   run       bootstrap init/preflight + restore Qdrant + compose up + check.sh all + acceptance/run-api.sh (trên instance)
#   packtest  (sau up với EVAL_MODE=packtest) pack + restore gói dữ liệu THẬT ngay trên VM, đo thời gian/đĩa; xem cmd_packtest
#   collect   kéo log/JSON kết quả về $STACK_EVAL_DIR/results-<ts>/ (KHÔNG .env, KHÔNG dữ liệu) và kiểm sha256
#   (review độc lập bên ngoài script)
#   down      destroy-eval.sh --yes  (huỷ đúng ID trong state.json)
#   status    id/label/giờ còn lại/giá, đối chiếu `vastai show instances-v1` (không parse được thì fail an toàn)
# Ngân sách chủ dự án: giá <= $0.30/h (chặn cứng ở search), tối đa 3 giờ. Cờ --i-accept-higher mới cho EVAL_MAX_DPH > 0.30 / EVAL_MAX_HOURS > 3.
# Biến: EVAL_ENV_FILE (BẮT BUỘC cho up; .env do operator chỉ định, script không đọc .env prod), EVAL_DATA_PKG (thư mục gói dữ liệu từ
#   pack.sh data, BẮT BUỘC), EVAL_SSH_KEY (~/.ssh/id_ed25519), EVAL_MAX_DPH (0.30), EVAL_MAX_HOURS (3), EVAL_MIN_RAM_GB (32; 48 nếu
#   EVAL_EXPLOIT=1), EVAL_EXPLOIT=1 (bật profile exploit + build image VM), EVAL_CONTAINER_API_KEY (khoá GIỚI HẠN THEO INSTANCE cho lớp A;
#   không có thì bỏ lớp A), EVAL_TEMPLATE (98fff0d0a96048e83c19da789fe7a979, VM Ubuntu), STACK_EVAL_DIR (local/stack-eval),
#   EVAL_REPO_ROOT (nguồn git archive), EVAL_POLL_SLEEP (15), EVAL_DISK_GB (100; 20..600, tính vào giá search),
#   EVAL_MODE (full | packtest: không .env/gói dữ liệu/build image, chỉ docker+zstd+rsync+time+image qdrant/neo4j). Riêng run: EVAL_SKIP_CASES (mặc định UI-POC-1..3 UI-TR-2 UI-TR-3:
#   máy thuê không có lab/profile exploit, không có Hy-MT2/OCR), STACK_HAS_OCR (0). Ca bỏ qua được ghi kèm lý do (không tính là đạt).
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

KEYFILE="${XDG_CONFIG_HOME:-$HOME/.config}/vastai/vast_api_key"   # nơi timer (systemd --user) và vastai đọc khoá tài khoản
die() {
    echo "eval-vast: $*" >&2
    [ ! -d "$SD" ] || printf '%s eval-vast: %s\n' "$(date '+%F %T')" "$*" >>"$SD/ALERT.txt" 2>/dev/null || true
    exit 1
}
say() { echo "eval-vast: $*"; }
nap() { sleep "$POLL"; }
need_cmds() { local c; for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "thiếu lệnh '$c' trong PATH"; done; }

# sget KEY -> giá trị trong state.json (rỗng nếu thiếu)
sget() { python3 -I -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2],""); print(v if not isinstance(v,(list,dict)) else json.dumps(v))' "$SD/state.json" "$1" 2>/dev/null; }
# sset KEY=JSONVALUE... -> ghi vào state.json (nguyên tử)
sset() { python3 -I - "$SD/state.json" "$@" <<'PY'
import json, os, sys, tempfile
p = sys.argv[1]
d = json.load(open(p)) if os.path.exists(p) else {}
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1); d[k] = json.loads(v)
fd, t = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".state."); os.write(fd, json.dumps(d).encode()); os.close(fd)
os.chmod(t, 0o600); os.replace(t, p)
PY
}
# find_by_label LABEL -> in ID các instance mang đúng label, đi hết các trang next_token; rc != 0 nếu không đọc được
# (vastai lỗi/DEPRECATED/không phải JSON). id chuỗi/float được chuẩn hoá. Chỉ dùng để tìm instance mồ côi theo label.
find_by_label() { python3 -I - "$1" <<'PY'
import json, subprocess, sys
def sid(x): return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)
out, tok = [], None
try:
    for _ in range(200):
        cmd = ["vastai", "show", "instances-v1", "--raw", "-l", "25"] + (["--next-token", tok] if tok else [])
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        d = json.loads(r.stdout)
        if isinstance(d, list): out += d; break
        assert r.returncode == 0 and isinstance(d, dict) and isinstance(d.get("instances"), list)
        out += d["instances"]; tok = d.get("next_token")
        if not tok: break
    else:
        raise RuntimeError("too many pages")
except Exception:
    sys.exit(2)
for i in out:
    if isinstance(i, dict) and i.get("label") == sys.argv[1]: print(sid(i.get("id"))); break
PY
}
# vshow ID -> in "GONE" hoặc "FIELDS<TAB>label<TAB>price<TAB>status<TAB>ssh_host<TAB>ssh_port" (tra theo ID); rc 2 nếu không đọc được.
vshow() { vastai show instance "$1" --raw </dev/null 2>/dev/null | python3 -I -c '
import json, sys
try:
    d = json.load(sys.stdin); row = d
    if isinstance(d, dict) and "instances" in d:
        row = d["instances"]
        if row is None: print("GONE"); sys.exit(0)
    if isinstance(row, list) and len(row) == 1: row = row[0]
    assert isinstance(row, dict) and str(int(row["id"])) == sys.argv[1]
except Exception:
    sys.exit(2)
g = lambda k: str(row.get(k, "") if row.get(k) is not None else "").replace("\t", " ")
pt = row.get("ports") or {}
dp = ""
try: dp = str(pt["22/tcp"][0]["HostPort"])
except Exception: pass
print("\t".join(["FIELDS", g("label"), g("dph_total"), g("actual_status"), g("ssh_host") or g("public_ipaddr"), g("ssh_port") or "22", g("public_ipaddr"), dp]))' "$1"; }

ssh_opts() { SSHO=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=30); }
# Chuỗi cho `rsync -e`: rsync tách theo khoảng trắng và hiểu nháy kép, nên đường dẫn khoá có dấu cách vẫn đúng.
rsh_cmd() { printf 'ssh -i "%s" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=30 -p %s' "$KEY" "$PORT"; }
load_conn() {
    [ -f "$SD/state.json" ] || die "chưa có state ($SD/state.json); chạy 'up' trước"
    HOST="$(sget ssh_host)"; PORT="$(sget ssh_port)"
    [ -n "$HOST" ] && [[ "$PORT" =~ ^[0-9]+$ ]] || die "state thiếu ssh_host/ssh_port (up chưa xong?)"
    ssh_opts
}
rssh() { ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" "$@"; }

# ---------------------------------------------------------------------------------------------------- up
# Khoá API tài khoản Vast không bao giờ được lên máy thuê: EVAL_CONTAINER_API_KEY phải là khoá khác (khoá giới hạn theo instance).
refuse_if_account_key() {
    local k="$1" f v
    for v in "${VAST_API_KEY:-}"; do [ -z "$v" ] || [ "$v" != "$k" ] || die "EVAL_CONTAINER_API_KEY trùng khoá tài khoản Vast (VAST_API_KEY); dùng khoá giới hạn theo instance"; done
    for f in "${XDG_CONFIG_HOME:-$HOME/.config}/vastai/vast_api_key" "${XDG_CONFIG_HOME:-$HOME/.config}/vastai/vast_tfa_key" "$HOME/.vast_api_key"; do
        [ -f "$f" ] || continue
        v="$(tr -d '[:space:]' <"$f")"
        [ -z "$v" ] || [ "$v" != "$k" ] || die "EVAL_CONTAINER_API_KEY trùng khoá tài khoản Vast ($f); dùng khoá giới hạn theo instance"
    done
}

cmd_up() {
    local dry=0 higher=0 a
    for a in "$@"; do case "$a" in --dry-run) dry=1 ;; --i-accept-higher) higher=1 ;; *) die "tham số lạ: $a" ;; esac; done
    local cap="${EVAL_MAX_DPH:-0.30}" hours="${EVAL_MAX_HOURS:-3}" ram="${EVAL_MIN_RAM_GB:-}" disk="${EVAL_DISK_GB:-100}" mode="${EVAL_MODE:-full}"
    [[ "$disk" =~ ^[1-9][0-9]*$ ]] && [ "$disk" -ge 20 ] && [ "$disk" -le 600 ] || die "EVAL_DISK_GB phải là số nguyên 20..600: $disk"
    [[ "$mode" =~ ^(full|packtest)$ ]] || die "EVAL_MODE phải là full hoặc packtest: $mode"
    [[ "$cap" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "EVAL_MAX_DPH không hợp lệ: $cap"
    [[ "$hours" =~ ^[1-9][0-9]*$ ]] || die "EVAL_MAX_HOURS phải là số nguyên >= 1: $hours"
    if [ "$higher" != 1 ]; then
        awk -v c="$cap" 'BEGIN { exit !(c <= 0.30) }' || die "EVAL_MAX_DPH=$cap vượt ngân sách chốt \$0.30/h; muốn vượt phải thêm --i-accept-higher"
        [ "$hours" -le 3 ] || die "EVAL_MAX_HOURS=$hours vượt ngân sách chốt 3 giờ; muốn vượt phải thêm --i-accept-higher"
    fi
    [ -n "$ram" ] || { ram=32; [ "${EVAL_EXPLOIT:-0}" != 1 ] || ram=48; }
    need_cmds vastai python3

    # Offer rẻ nhất thoả điều kiện; lọc LẠI ở phía client nên giá > cap bị chặn dù vastai trả gì.
    # cpu_ram trong truy vấn CLI tính bằng GB (CLI nhân 1000); --storage $disk để dph_total gồm cả đĩa; vms_enabled=true cho VM.
    local query="dph_total<=$cap cpu_ram>=$ram disk_space>=$disk inet_down>=200 reliability>=0.98 rentable=true verified=true vms_enabled=true"
    local offers pick OFFER PRICE
    offers="$(vastai search offers "$query" --storage "$disk" -o dph_total --raw 2>/dev/null)" || die "vastai search lỗi"
    pick="$(printf '%s' "$offers" | python3 -I -c '
import json, sys
cap, ram, disk = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[4])
skip = set(sys.argv[3].split())   # EVAL_EXCLUDE_OFFERS: offer đã thuê mà không lên được ssh
try:
    d = json.load(sys.stdin)
    d = d if isinstance(d, list) else d.get("offers", [])
except Exception:
    sys.exit(2)
ok = [o for o in d if isinstance(o, dict) and str(o.get("id")) not in skip and isinstance(o.get("dph_total"), (int, float)) and o["dph_total"] <= cap
      and (o.get("cpu_ram") or 0) >= ram * 1024 and (o.get("disk_space") or 0) >= disk and (o.get("inet_down") or 0) >= 200
      and (o.get("reliability2") or o.get("reliability") or 0) >= 0.98]
if not ok: sys.exit(1)
b = min(ok, key=lambda o: o["dph_total"]); print(b["id"], b["dph_total"])' "$cap" "$ram" "${EVAL_EXCLUDE_OFFERS:-}" "$disk")" \
        || die "không có offer nào <= \$$cap/h, RAM >= ${ram}GB, disk >= ${disk}GB, mạng >= 200Mbps, reliability >= 0.98 (hoặc vastai trả dữ liệu lạ); không thuê"
    read -r OFFER PRICE <<<"$pick"
    [[ "$OFFER" =~ ^[0-9]+$ ]] || die "offer id lạ: '$OFFER'"
    local label="secai-stack-eval-$(date +%Y%m%d)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    local create=(vastai create instance "$OFFER" --template_hash "$TEMPLATE" --disk "$disk" --label "$label" --ssh --direct --raw)
    say "offer $OFFER \$$PRICE/h (cap \$$cap/h, tối đa ${hours}h => tối đa ~\$$(awk -v p="$PRICE" -v h="$hours" 'BEGIN{printf "%.2f", p*h}'))"
    if [ "$dry" = 1 ]; then say "DRY-RUN, không thuê. Lệnh sẽ chạy: ${create[*]}"; return 0; fi

    if [ "$mode" = full ]; then   # packtest không đẩy .env/gói dữ liệu: pack và restore chạy trên chính VM
        [ -f "${EVAL_ENV_FILE:-}" ] || die "đặt EVAL_ENV_FILE = tệp .env cho instance (script không tự đọc .env prod)"
        [ -d "${EVAL_DATA_PKG:-}" ] && [ -f "$EVAL_DATA_PKG/SHA256SUMS" ] || die "đặt EVAL_DATA_PKG = thư mục gói dữ liệu (pack.sh data) có SHA256SUMS"
    fi
    [ -f "$KEY" ] || die "không thấy khoá ssh $KEY (EVAL_SSH_KEY)"
    [ -z "${EVAL_CONTAINER_API_KEY:-}" ] || refuse_if_account_key "$EVAL_CONTAINER_API_KEY"
    need_cmds ssh scp rsync systemd-run git flock loginctl
    # Timer systemd --user chỉ chạy khi phiên user còn sống: không linger thì đăng xuất là mất deadline.
    [ "$(loginctl show-user "${USER:-$(id -un)}" -p Linger 2>/dev/null)" = "Linger=yes" ] \
        || die "systemd --user chưa bật linger (timer deadline sẽ chết khi đăng xuất): chạy 'sudo loginctl enable-linger ${USER:-$(id -un)}' rồi thử lại"
    # Timer systemd --user chỉ có HOME (không có VAST_API_KEY của shell này): vastai ở đó đọc đúng $KEYFILE. Khoá env khác tệp, hay tệp
    # thiếu => timer 401 mãi, hoặc (khác tài khoản) "show instance" trả null = "đã huỷ" giả. Nên bắt buộc: có tệp, và env (nếu có) = tệp.
    [ -r "$KEYFILE" ] && [ -n "$(tr -d '[:space:]' <"$KEYFILE")" ] || die "không đọc được khoá Vast $KEYFILE (timer deadline dùng tệp này); chạy 'vastai set api-key' rồi thử lại"
    if [ -n "${VAST_API_KEY:-}" ] && [ "$VAST_API_KEY" != "$(tr -d '[:space:]' <"$KEYFILE")" ]; then
        die "VAST_API_KEY trong môi trường khác khoá trong $KEYFILE: timer sẽ dùng khoá ở tệp (có thể là tài khoản khác). Bỏ VAST_API_KEY hoặc đồng bộ tệp"
    fi
    # Khoá 2FA (vast_tfa_key): một số bản CLI ưu tiên nó hơn tệp khoá/env, và timer (không có env) dùng tệp: nếu khác thì up và timer
    # có thể là hai tài khoản khác nhau. Bắt buộc mọi cách phân giải (env, tfa, tệp khoá) ra cùng một khoá; show user bên dưới dùng đúng nó.
    local tfa="$(dirname "$KEYFILE")/vast_tfa_key"
    if [ -s "$tfa" ] && [ "$(tr -d '[:space:]' <"$tfa")" != "$(tr -d '[:space:]' <"$KEYFILE")" ]; then
        die "$tfa khác $KEYFILE: CLI vastai có thể ưu tiên khoá 2FA nên up và timer có thể thành hai tài khoản khác nhau. Đồng bộ hoặc gỡ vast_tfa_key"
    fi
    # .env sẽ rời máy này: không được chứa khoá tài khoản Vast (khoá đọc trong python qua tệp/môi trường, không qua argv, không in).
    [ "$mode" != full ] || python3 -I - "$EVAL_ENV_FILE" "$KEYFILE" "$(dirname "$KEYFILE")/vast_tfa_key" "$HOME/.vast_api_key" <<'PY' || die "$EVAL_ENV_FILE chứa khoá API tài khoản Vast: từ chối đẩy sang máy thuê (gỡ nó khỏi tệp)"
import os, sys
text = open(sys.argv[1], errors="replace").read()
keys = {os.environ.get("VAST_API_KEY", "").strip()}
for f in sys.argv[2:]:   # khoá api, khoá 2FA, khoá cũ (legacy): tệp không có thì bỏ qua
    try: keys.add(open(f).read().strip())
    except OSError: pass
sys.exit(1 if any(len(k) >= 8 and k in text for k in keys) else 0)
PY
    local acct
    acct="$(vastai show user --raw </dev/null 2>/dev/null | python3 -I -c 'import json,sys
print(int(json.load(sys.stdin)["id"]))' 2>/dev/null)" || die "không đọc được danh tính tài khoản (vastai show user); không thuê"
    mkdir -p "$SD"; chmod 700 "$SD"
    exec 9>"$SD/.lock"; flock -n 9 || die "đang có một 'up' khác chạy ($SD/.lock)"
    if [ -f "$SD/state.json" ]; then
        if [ -z "$(sget destroyed_at)" ]; then
            # abandoned_at chỉ hợp lệ khi chưa từng có ID (state không có id và không có instance.id), nếu không thì bỏ nhầm instance đang tính tiền
            [ -n "$(sget abandoned_at)" ] && [ -z "$(sget id)" ] && [ ! -s "$SD/instance.id" ] \
                || die "state.json cũ chưa huỷ hoặc đã 'abandoned' nhưng có ID (id '$(sget id)', label $(sget label), instance.id $(cat "$SD/instance.id" 2>/dev/null)); chạy 'down' hoặc kiểm console"
        fi
        mv "$SD/state.json" "$SD/state-$(date +%s).json"; rm -f "$SD/instance.id"
    fi
    [ -f "$SD/protected-instances" ] || (umask 077; echo "$HARD_DENY" >"$SD/protected-instances")
    [ "$mode" != full ] || echo "CẢNH BÁO: $EVAL_ENV_FILE (chứa API key) sẽ được CHUYỂN sang máy thuê ngoài của Vast. Xoay các khoá đó sau khi eval xong."

    # --- Chuẩn bị TRƯỚC khi thuê: trap, state "pending" (có label, chưa có ID) và timer deadline. Nếu create thành công mà ID không rõ,
    # timer/trap vẫn huỷ được qua destroy-eval.sh (tra theo label). Không có deadline thì không thuê.
    DONE=0; CREATING=0; PREPARED=1; trap 'on_exit_up' EXIT; trap 'exit 130' INT; trap 'exit 143' TERM
    local now; now="$(date +%s)"
    sset 'id=""' "label=\"$label\"" "created_at=$now" "deadline=$((now + hours * 3600))" "price=$PRICE" \
         "max_dph=$cap" "max_hours=$hours" "timers=[]" "pending=true" "account_id=\"$acct\"" "mode=\"$mode\"" "disk_gb=$disk" || die "không ghi được state.json"
    local tag unit timers=() secs
    for tag in B D; do
        secs=$((hours * 3600)); [ "$tag" = D ] && secs=$((secs + 600))
        unit="secai-eval-destroy-$tag-$(date +%Y%m%d)-$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        local envs=(--setenv=STACK_EVAL_DIR="$SD" --setenv=PATH="$PATH" --setenv=HOME="$HOME" --setenv=EVAL_EXPECT_LABEL="$label")
        [ -z "${XDG_CONFIG_HOME:-}" ] || envs+=(--setenv=XDG_CONFIG_HOME="$XDG_CONFIG_HOME")
        [ -z "${STACK_EVAL_PROTECTED:-}" ] || envs+=(--setenv=STACK_EVAL_PROTECTED="$STACK_EVAL_PROTECTED")
        # --on-unit-active: lặp lại mỗi 10 phút (huỷ thất bại/chưa chứng minh được thì thử lại); destroy-eval.sh tự dừng timer khi thành công.
        systemd-run --user --unit="$unit" --on-active="$secs" --on-unit-active=600 -p RuntimeMaxSec=300 "${envs[@]}" "$HERE/destroy-eval.sh" --force-deadline >/dev/null 2>&1 \
            || die "không đặt được timer deadline $tag (không có deadline thì không thuê)"
        timers+=("\"$unit\""); sset "timers=[$(IFS=,; echo "${timers[*]}")]" || die "không ghi được state.json"
    done
    say "deadline cứng $(date -d "@$((now + hours * 3600))" '+%F %T') (lớp B) và +10 phút (lớp D), lặp mỗi 10 phút tới khi huỷ được chứng minh; timers: ${timers[*]//\"/}"

    # --- thuê. Từ đây mọi lỗi phải huỷ instance (trap EXIT).
    CREATING=1
    local out ID="" t
    out="$("${create[@]}" 2>&1)"; local rc=$?
    ID="$(printf '%s' "$out" | python3 -I -c 'import json,sys
try: print(json.load(sys.stdin).get("new_contract",""))
except Exception: pass' 2>/dev/null)"
    if [ "$rc" != 0 ] || ! [[ "$ID" =~ ^[1-9][0-9]*$ ]]; then
        ID=""
        say "create trả lỗi/không rõ (rc=$rc); tìm instance mồ côi theo label $label"
        for t in 1 2 3; do
            ID="$(find_by_label "$label")" || die "create lỗi và danh sách vastai không đọc được: timer sẽ tra theo label $label; KIỂM TAY console Vast"
            [ -z "$ID" ] || break
            nap
        done
        [ -n "$ID" ] || die "create lỗi và chưa thấy instance nào mang label $label (timer vẫn tra theo label tới deadline)"
    fi
    [[ "$ID" =~ ^[1-9][0-9]*$ ]] && [ "$ID" != "$HARD_DENY" ] || die "ID lạ sau create: '$ID' (không huỷ gì); kiểm tay console, label $label"
    # GHI ID NGAY, trước mọi bước khác.
    (umask 077; printf '%s\n' "$ID" >"$SD/instance.id") || die "không ghi được instance.id"
    sset "id=\"$ID\"" "pending=false" || die "không ghi được state.json"
    say "đã thuê instance $ID ($label); ID ghi vào $SD/instance.id"

    # --- chờ máy chạy + ssh
    local host="" port="" n info row rprice priced=0
    for n in $(seq 1 60); do
        row="$(vshow "$ID")" || row=""
        rprice="$(awk -F'\t' '$1 == "FIELDS" { print $3 }' <<<"$row")"
        if [ "$priced" = 0 ] && [ -n "$rprice" ]; then   # giá THẬT của instance sau create (offer có thể đã đổi giá)
            priced=1
            awk -v p="$rprice" -v c="$cap" 'BEGIN { exit !(p ~ /^[0-9.]+$/ && p + 0 <= c + 1e-9) }' \
                || die "giá thật của instance $ID là \$$rprice/h > cap \$$cap/h: huỷ (qua destroy-eval; nếu nó từ chối vì > 1.5 x cap thì HUỶ TAY trên console Vast, ID $ID)"
        fi
        info="$(awk -F'\t' '$1 == "FIELDS" && $4 == "running" && $5 != "" { print $5, $6 }' <<<"$row")"
        [ -z "$info" ] || break
        nap
    done
    read -r host port <<<"$info"
    [ -n "$host" ] || die "instance $ID chưa running/chưa có ssh sau khi chờ"
    # Thử proxy ssh_host:ssh_port trước, rồi cổng trực tiếp public_ip:ports[22/tcp]; ghi lỗi ssh cuối để chẩn đoán.
    local dhost dport cand sshok=0 sshlog="$SD/ssh-last-error.txt"
    read -r dhost dport <<<"$(awk -F'\t' '$1 == "FIELDS" { print $7, $8 }' <<<"$(vshow "$ID")")"
    HOST="$host"; PORT="$port"; ssh_opts
    for n in $(seq 1 60); do
        for cand in "$host $port" "${dhost:--} ${dport:--}"; do
            read -r HOST PORT <<<"$cand"
            [ "$HOST" != "-" ] && [[ "$PORT" =~ ^[0-9]+$ ]] || continue
            if rssh true >/dev/null 2>"$sshlog"; then sshok=1; break; fi
        done
        [ "$sshok" = 1 ] && break
        nap
    done
    [ "$sshok" = 1 ] || die "ssh vào $host:$port (và ${dhost:--}:${dport:--}) không được sau 60 lần; lỗi cuối: $(tail -c 300 "$sshlog" 2>/dev/null | tr '\n' ' ')"
    sset "ssh_host=\"$HOST\"" "ssh_port=$PORT" || die "không ghi được state.json"
    host="$HOST"; port="$PORT"   # các bước scp/rsync/ssh bên dưới dùng đúng đường đã vào được (proxy hoặc trực tiếp)

    if [ -n "${EVAL_CONTAINER_API_KEY:-}" ]; then
        # Lớp A: khoá giới hạn THEO INSTANCE đi qua stdin vào tệp header 0600 (curl -H @tệp, không lên argv); instance tự DELETE chính nó.
        printf '%s' "$EVAL_CONTAINER_API_KEY" | rssh "umask 077; { printf 'Authorization: %s ' Bearer; cat; echo; } > /root/.vast-instance-hdr; \
(setsid nohup sh -c 'sleep $((hours * 3600 + 300)); curl -fsS -X DELETE -H @/root/.vast-instance-hdr https://console.vast.ai/api/v0/instances/$ID/' >/dev/null 2>&1 &)" \
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
    if [ "$mode" = packtest ]; then
        # Chỉ công cụ cho pack.sh/restore-data.sh + image qdrant/neo4j; không .env, không gói dữ liệu, không build image.
        rssh "bash -s" <<'REMOTE_PROVISION_PACKTEST' || die "provision packtest lỗi"
set -euo pipefail
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
miss=""
for c in zstd rsync curl python3 shred; do command -v "$c" >/dev/null || miss="$miss $c"; done
[ -x /usr/bin/time ] || miss="$miss time"
miss="${miss/ shred/ coreutils}"
[ -z "$miss" ] || { apt-get update -qq && apt-get install -y -qq $miss; }
docker pull -q qdrant/qdrant:v1.10.1
docker pull -q neo4j:5.20-community
mkdir -p /srv/src-root /srv/packtest
REMOTE_PROVISION_PACKTEST
        DONE=1
        say "xong 'up' (packtest). Tiếp: eval-vast.sh packtest -> collect -> down. Deadline cứng ở trên."
        return 0
    fi
    rsync -a --no-links -e "$(rsh_cmd)" "$EVAL_DATA_PKG"/ "root@$host:$REMOTE/pkg/" || die "rsync gói dữ liệu lỗi"
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
# Profile translation luôn cần image sidecar (cả TUNNEL_MODE=off). init chạy bằng root sẽ đặt TUNNEL_UID=0 (preflight từ chối): ghim uid khác 0.
TU=$(grep -E '^TUNNEL_UID=' stack/.env | tail -n1 | cut -d= -f2- || true)   # set -e + pipefail: thiếu dòng không được làm chết provision
[[ "$TU" =~ ^[1-9][0-9]*$ ]] || TU=10002
docker build --build-arg TUNNEL_UID="$TU" -t "secai-specialist-tunnel:eval-$SHA" stack/images/tunnel
mkdir -p /srv/secai-data
# giá trị cuối trong .env thắng (compose và bootstrap.sh đều lấy dòng sau cùng). Máy thuê có IP công khai, gateway không xác thực: chỉ loopback.
cat >> stack/.env <<EOF
DATA_ROOT=/srv/secai-data
GATEWAY_IMAGE=secai-agent-gateway:eval-$SHA
VM_IMAGE=secai-pentestcode-vm:eval-$SHA
CONVERTER_IMAGE=secai-converter:eval-$SHA
REVIEW_UI_IMAGE=secai-review-ui:eval-$SHA
TUNNEL_IMAGE=secai-specialist-tunnel:eval-$SHA
TUNNEL_UID=$TU
GATEWAY_BIND=127.0.0.1
COMPOSE_PROFILES=$P
EOF
REMOTE_PROVISION
    DONE=1
    say "xong 'up'. Tiếp: eval-vast.sh run -> collect -> (review độc lập) -> down. Deadline cứng ở trên."
}

on_exit_up() {
    local rc=$?
    trap - EXIT
    if [ "${CREATING:-0}" = 0 ] && [ "${PREPARED:-0}" = 1 ] && [ "${DONE:-0}" != 1 ] && [ -f "$SD/state.json" ]; then
        # Chưa thuê gì (lỗi lúc chuẩn bị/đặt timer): dừng timer đã đặt, bỏ state pending để lần `up` sau không bị chặn 3 giờ.
        local u
        for u in $(python3 -I -c 'import json,sys; print(" ".join(x for x in json.load(open(sys.argv[1])).get("timers", []) if isinstance(x, str)))' "$SD/state.json" 2>/dev/null); do
            systemctl --user stop "$u.timer" >/dev/null 2>&1 || true
        done
        mv "$SD/state.json" "$SD/state-aborted-$(date +%s).json" 2>/dev/null || true
    fi
    if [ "${CREATING:-0}" = 1 ] && [ "${DONE:-0}" != 1 ]; then
        echo "!!!!!! up THẤT BẠI sau khi (có thể) đã thuê instance: HUỶ NGAY !!!!!!" >&2
        STACK_EVAL_DIR="$SD" "$HERE/destroy-eval.sh" --yes; local d=$?
        case "$d" in
            0) ;;
            4|6) echo "eval-vast: chưa thấy instance nào theo label; timer lặp vẫn chạy. KIỂM CONSOLE Vast (label $(sget label)); chỉ khi chắc không có: destroy-eval.sh --abandon-pending" >&2 ;;
            *) echo "!!!!!! HUỶ CHƯA XONG (rc=$d): kiểm console Vast ngay (instance '$(sget id)', label $(sget label)); timer lặp vẫn chạy !!!!!!" >&2 ;;
        esac
    fi
    exit "$rc"
}

# ---------------------------------------------------------------------------------------------------- run / collect / down / status
cmd_run() {
    local skip="${EVAL_SKIP_CASES-UI-POC-1 UI-POC-2 UI-POC-3 UI-TR-2 UI-TR-3}" ocr="${STACK_HAS_OCR:-0}"
    # Hai giá trị này lên argv của ssh (shell từ xa): chỉ nhận ký tự an toàn.
    [[ "$skip" =~ ^[A-Z0-9\ -]*$ ]] || die "EVAL_SKIP_CASES chỉ gồm mã ca (A-Z, 0-9, '-') cách nhau bằng dấu cách: '$skip'"
    [[ "$ocr" =~ ^[01]$ ]] || die "STACK_HAS_OCR phải là 0 hoặc 1: '$ocr'"
    need_cmds ssh; load_conn
    # Chạy trên instance; mọi bước ghi log vào $REMOTE/results, kết thúc bằng SHA256SUMS. Không có secret trong script.
    rssh "SKIP_CASES='$skip' STACK_HAS_OCR=$ocr bash -s" <<'REMOTE_RUN'
set -uo pipefail
cd /opt/secai-stack/stack
# Mỗi lần run một thư mục kết quả mới: kết quả lần trước (kể cả ca đạt) không được lẫn vào lần này.
R=/opt/secai-stack/results; [ ! -e "$R" ] || mv "$R" "$R.prev-$(date +%Y%m%d-%H%M%S)-$$"; mkdir -p "$R"; rc=0; ABORT=0
envv() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
# Lý do cho từng ca bỏ qua (theo cấu hình thật trong .env), ghi vào <id>.json và summary.json: ca bỏ qua không bao giờ là đạt.
SKIP_REASONS=$(python3 -c '
import json, sys
ids, tunnel, profiles = sys.argv[1].split(), sys.argv[2] or "ssh", [p.strip() for p in sys.argv[3].split(",")]
r = {}
for i in ids:
    if i in ("UI-TR-2", "UI-TR-3") and tunnel == "off":
        r[i] = "không chạy: không có Hy-MT2/OCR (TUNNEL_MODE=off)"
    elif i.startswith("UI-POC-") and "exploit" not in profiles:
        r[i] = "không chạy: profile exploit tắt (COMPOSE_PROFILES=%s)" % sys.argv[3]
    else:
        r[i] = "không chạy: bỏ qua theo EVAL_SKIP_CASES (TUNNEL_MODE=%s, COMPOSE_PROFILES=%s)" % (tunnel, sys.argv[3])
print(json.dumps(r, ensure_ascii=False))' "$SKIP_CASES" "$(envv TUNNEL_MODE)" "$(envv COMPOSE_PROFILES)")
export SKIP_CASES STACK_HAS_OCR SKIP_REASONS
step() { local name=$1; shift; [ "$ABORT" = 0 ] || { echo "$name skipped" >>"$R/steps.txt"; return 0; }
         "$@" >"$R/$name.log" 2>&1; local r=$?; echo "$name rc=$r" | tee -a "$R/steps.txt"; [ "$r" = 0 ] || rc=1; return "$r"; }
abort_on_fail() { "$@" || ABORT=1; }
DATA_ROOT=$(envv DATA_ROOT); export DATA_ROOT
abort_on_fail step init ./bootstrap.sh init
abort_on_fail step restore_files ./restore-data.sh ../pkg   # trước preflight: preflight đòi các thư mục dữ liệu do gói cung cấp
abort_on_fail step preflight ./bootstrap.sh preflight
abort_on_fail step qdrant_up docker compose up -d --wait qdrant
if [ "$ABORT" = 0 ]; then
    QIP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q qdrant)")
    abort_on_fail step restore_qdrant ./restore-data.sh --qdrant-url "http://$QIP:6333" ../pkg
fi
# Không dùng `up --wait`: translation-worker cố ý không có healthcheck (kế thừa healthcheck 8131 luôn hỏng trên bridge) nên --wait báo lỗi.
up_and_wait() {
    docker compose up -d || return 1
    local i
    for i in $(seq 1 60); do
        [ -z "$(docker ps -q --filter health=starting)" ] && [ -z "$(docker ps -q --filter health=unhealthy)" ] && return 0
        sleep 5
    done
    docker compose ps -a; return 1
}
step up up_and_wait
step check ./check.sh all
step acceptance env ACCEPT_OUT="$R/acceptance" ./acceptance/run-api.sh http://127.0.0.1:8131
# summary.json: rc từng bước + kết quả ca của LẦN NÀY; acceptance=null nghĩa là bộ ca không chạy.
# verdict: pass chỉ khi mọi bước rc=0, bộ ca chạy, có ca đạt và không ca nào trượt; trượt/bước lỗi -> fail; còn lại -> incomplete.
python3 -c '
import json, os, sys
R, rc = sys.argv[1], int(sys.argv[2]); steps = {}
for l in open(R + "/steps.txt"):
    n, _, v = l.strip().partition(" ")
    steps[n] = v
a = R + "/acceptance/summary.json"
acc = json.load(open(a)) if steps.get("acceptance", "").startswith("rc=") and os.path.exists(a) else None
if rc != 0 or any(v not in ("rc=0", "skipped") for v in steps.values()) or (acc and acc["fail"]):
    verdict = "fail"
elif acc is None or not acc["pass"]:
    verdict = "incomplete"
else:
    verdict = "pass"
json.dump({"verdict": verdict, "steps": steps, "skip_cases": os.environ["SKIP_CASES"].split(),
           "stack_has_ocr": os.environ["STACK_HAS_OCR"], "acceptance": acc},
          open(R + "/summary.json", "w"), ensure_ascii=False, indent=1)' "$R" "$rc"
# init in token review admin một lần ra stdout; không mang token/khoá nào về máy điều khiển.
sed -i -E 's/([A-Za-z0-9_]*(TOKEN|KEY|SECRET|PASSWORD)[A-Za-z0-9_]*)=[^[:space:]]+/\1=<redacted>/g' "$R"/*.log
(cd "$R" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + >SHA256SUMS)
exit "$rc"
REMOTE_RUN
}

cmd_collect() {
    need_cmds ssh rsync sha256sum; load_conn
    local dest="$SD/results-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$dest"
    # Chỉ thư mục results (log + JSON); loại .env/khoá phòng khi lọt vào.
    rsync -a --no-links --exclude '.env*' --exclude '*.key' -e "$(rsh_cmd)" "root@$HOST:$REMOTE/results/" "$dest/" || die "kéo kết quả lỗi"
    [ -z "$(find "$dest" -type l -print -quit)" ] || die "kết quả chứa symlink ($dest); từ chối (không kiểm sha256 qua symlink)"
    [ -s "$dest/SHA256SUMS" ] || die "thiếu/rỗng SHA256SUMS trong kết quả ($dest)"
    (cd "$dest" && sha256sum -c --strict --quiet SHA256SUMS) || die "SHA256 kết quả KHÔNG khớp ($dest)"
    say "kết quả đã kiểm sha256: $dest"
}

# ---------------------------------------------------------------------------------------------------- packtest
# Chứng minh pack + restore gói dữ liệu THẬT trên VM (EVAL_MODE=packtest). Máy cục bộ chỉ: kiểm trước, đo băng thông, rsync nguồn
# (chỉ đọc), mở tunnel ngược tới Qdrant prod và dọn snapshot do CHÍNH lần chạy này tạo. Mọi thứ ghi cục bộ nằm trong $SD/packtest.
# Biến: PACKTEST_SRC_ROOT (/home/ict/AIEngines/secai/secai-data), PACKTEST_MCP_DIR (<src>/../exploitdb-mcp-server),
#   PACKTEST_QDRANT_URL (http://127.0.0.1:6333), PACKTEST_QDRANT_COLLECTION (cve_kb_hybrid), QDRANT_API_KEY hoặc
#   PACKTEST_QDRANT_KEY_FILE (0600), PACKTEST_NEO4J_NODES (số node mong đợi, không có thì chỉ ghi lại), PACKTEST_VM_MIN_FREE_GB (60).
PT_MIN_LOCAL_FREE_GB=45 PT_MIN_MBPS=8
qcurl() {   # khoá Qdrant qua cấu hình curl trên stdin (-K -), không lên argv
    if [ -n "$QKEY" ]; then printf 'header = "api-key: %s"\n' "$QKEY" | curl -K - -fsS --max-time 60 "$@"
    else curl -fsS --max-time 60 "$@"; fi
}
local_free() { df -B1 --output=avail / | tail -n1 | tr -d ' '; }
# tmo CMD...: không chạy quá deadline (trừ 10 phút cho huỷ); treo thì bị giết.
tmo() { local l=$(( DL - 600 - $(date +%s) )); [ "$l" -gt 0 ] || die "packtest: đã sát deadline, dừng"; timeout --kill-after=30 "$l" "$@"; }
# own_snaps: snapshot KHÔNG có trong danh sách trước khi chạy, tên đúng collection, tạo sau lúc bắt đầu (lệch tối đa 120 s).
own_snaps() { qcurl "$QURL/collections/$COLL/snapshots" | python3 -I -c '
import json, re, sys, datetime as dt
before, coll, start = set(json.load(open(sys.argv[1]))), sys.argv[2], int(sys.argv[3])
for s in json.load(sys.stdin)["result"]:
    n = s.get("name", "")
    if n in before or not n.startswith(coll + "-") or not re.fullmatch(r"[A-Za-z0-9._-]+", n): continue
    try: t = dt.datetime.fromisoformat(str(s["creation_time"])[:19]).replace(tzinfo=dt.timezone.utc).timestamp()
    except Exception: print("UNPARSED " + n, file=sys.stderr); continue
    if t >= start - 120: print(n)' "$PT/snapshots-before.json" "$COLL" "$PT_START"; }
cleanup_snaps() {
    [ -f "$PT/snapshots-before.json" ] || return 0
    local i n left=""
    for i in 1 2 3 4 5; do
        left="$(own_snaps)" || { left="?"; nap; continue; }
        [ -n "$left" ] || return 0
        for n in $left; do
            qcurl -X DELETE -o /dev/null "$QURL/collections/$COLL/snapshots/$n" && echo "$(date '+%F %T') deleted $n" >>"$PT/snapshots-deleted.log"
        done
        nap
    done
    printf '%s eval-vast: packtest: snapshot Qdrant prod chưa dọn được: %s (xoá tay: curl -X DELETE %s/collections/%s/snapshots/<tên>)\n' \
        "$(date '+%F %T')" "$left" "$QURL" "$COLL" | tee -a "$SD/ALERT.txt" >&2
    return 1
}
on_exit_packtest() {
    local rc=$? f
    trap - EXIT
    if [ -n "${TUN:-}" ]; then kill "$TUN" 2>/dev/null; wait "$TUN" 2>/dev/null; fi   # tunnel trước: pack.sh không tạo thêm snapshot được nữa
    cleanup_snaps || rc=1
    timeout 60 ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" 'shred -u /srv/packtest/qkey 2>/dev/null; true' </dev/null >/dev/null 2>&1 || true
    f="$(local_free)"
    if [[ "$f" =~ ^[0-9]+$ ]] && [ $(( PT_FREE0 - f )) -gt $((1 << 30)) ]; then
        printf '%s eval-vast: packtest: / cục bộ còn %s byte, thấp hơn lúc đầu (%s) quá 1 GB: kiểm snapshot Qdrant prod\n' "$(date '+%F %T')" "$f" "$PT_FREE0" | tee -a "$SD/ALERT.txt" >&2
        rc=1
    fi
    [ "$rc" = 0 ] && say "packtest XONG (đạt). Tiếp: collect -> down" || echo "eval-vast: packtest KHÔNG ĐẠT/lỗi (rc=$rc); vẫn chạy collect rồi down" >&2
    exit "$rc"
}

cmd_packtest() {
    need_cmds ssh rsync curl python3 timeout df; load_conn
    [ "$(sget mode)" = packtest ] || die "packtest cần instance dựng bằng EVAL_MODE=packtest eval-vast.sh up"
    DL="$(sget deadline)"; [[ "$DL" =~ ^[0-9]+$ ]] || die "state thiếu deadline"
    [ $(( DL - 600 - $(date +%s) )) -gt 0 ] || die "packtest: còn < 10 phút tới deadline; không bắt đầu"
    local src="${PACKTEST_SRC_ROOT:-/home/ict/AIEngines/secai/secai-data}" mcp ne="${PACKTEST_NEO4J_NODES:-}" vmfree="${PACKTEST_VM_MIN_FREE_GB:-60}" bw="${PACKTEST_BW_MB:-200}"
    mcp="${PACKTEST_MCP_DIR:-$src/../exploitdb-mcp-server}"
    QURL="${PACKTEST_QDRANT_URL:-http://127.0.0.1:6333}"; QURL="${QURL%/}"; COLL="${PACKTEST_QDRANT_COLLECTION:-cve_kb_hybrid}"
    [[ "$QURL" =~ ^http://(127\.0\.0\.1|localhost):([0-9]{1,5})$ ]] || die "PACKTEST_QDRANT_URL phải là http://127.0.0.1:<cổng> (Qdrant prod trên máy này): $QURL"
    local qport="${BASH_REMATCH[2]}"
    [[ "$COLL" =~ ^[A-Za-z0-9_-]+$ ]] || die "PACKTEST_QDRANT_COLLECTION lạ: $COLL"
    [ -z "$ne" ] || [[ "$ne" =~ ^[0-9]+$ ]] || die "PACKTEST_NEO4J_NODES phải là số: $ne"
    [[ "$vmfree" =~ ^[0-9]+$ ]] && [[ "$bw" =~ ^[1-9][0-9]*$ ]] || die "PACKTEST_VM_MIN_FREE_GB/PACKTEST_BW_MB phải là số"
    QKEY="${QDRANT_API_KEY:-}"; unset QDRANT_API_KEY
    if [ -n "${PACKTEST_QDRANT_KEY_FILE:-}" ]; then
        [ -f "$PACKTEST_QDRANT_KEY_FILE" ] && [ "$(stat -c '%a %u' "$PACKTEST_QDRANT_KEY_FILE")" = "600 $(id -u)" ] \
            || die "PACKTEST_QDRANT_KEY_FILE phải là tệp 0600 của $(id -un)"
        QKEY="$(tr -d '[:space:]' <"$PACKTEST_QDRANT_KEY_FILE")"
    fi
    [ -z "$QKEY" ] || [[ "$QKEY" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || die "khoá Qdrant có ký tự lạ (không đưa vào cấu hình curl)"

    # --- a. kiểm cục bộ (fail closed, chưa tạo gì)
    local p missing=()
    for p in "$src/exploitdb" "$src/poc/catalog.db" "$src/poc/store" "$src/poc-sources" "$src/neo4j/neo4j.dump" "$mcp"; do [ -e "$p" ] || missing+=("$p"); done
    [ ${#missing[@]} -eq 0 ] || die "thiếu nguồn: ${missing[*]}"
    PT_FREE0="$(local_free)"; [[ "$PT_FREE0" =~ ^[0-9]+$ ]] || die "không đọc được dung lượng trống của /"
    [ "$PT_FREE0" -ge $((PT_MIN_LOCAL_FREE_GB << 30)) ] \
        || die "/ cục bộ chỉ còn $((PT_FREE0 >> 30)) GB < $PT_MIN_LOCAL_FREE_GB GB (snapshot Qdrant ghi lên đĩa prod); không chạy"
    PT="$SD/packtest"; mkdir -p "$PT"; chmod 700 "$PT"
    qcurl "$QURL/collections/$COLL" >"$PT/prod-collection.raw" || die "không đọc được Qdrant prod $QURL/collections/$COLL"
    python3 -I -c 'import json,sys
r = json.load(open(sys.argv[1]))["result"]
json.dump({"points_count": r["points_count"], "params": r["config"]["params"], "payload_schema_keys": sorted(r.get("payload_schema") or {})}, open(sys.argv[2], "w"))' \
        "$PT/prod-collection.raw" "$PT/prod-collection.json" || die "collection info Qdrant prod không đúng dạng"
    rm -f "$PT/prod-collection.raw"
    PT_START="$(date +%s)"
    qcurl "$QURL/collections/$COLL/snapshots" | python3 -I -c 'import json,sys; json.dump([s["name"] for s in json.load(sys.stdin)["result"]], open(sys.argv[1], "w"))' \
        "$PT/snapshots-before.json" || die "không liệt kê được snapshot Qdrant prod"
    say "Qdrant prod: $(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["points_count"])' "$PT/prod-collection.json") điểm; snapshot có sẵn (KHÔNG bao giờ xoá): $(tr -d '[]"' <"$PT/snapshots-before.json")"
    TUN=""; trap on_exit_packtest EXIT; trap 'exit 130' INT; trap 'exit 143' TERM

    # --- b. băng thông: ~40 GB phải xong trong hạn 3 giờ
    local t0 t1 mbps
    t0="$(date +%s.%N)"
    head -c "$((bw << 20))" /dev/urandom | tmo ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" 'cat >/dev/null' || die "đo băng thông lỗi"
    t1="$(date +%s.%N)"
    mbps="$(awk -v b="$bw" -v a="$t0" -v z="$t1" 'BEGIN { d = z - a; if (d < 0.001) d = 0.001; printf "%.1f", b / d }')"
    say "băng thông tới VM: $mbps MB/s ($bw MB)"
    if awk -v m="$mbps" -v k="$PT_MIN_MBPS" 'BEGIN { exit !(m < k) }'; then
        echo "!!!!!! BĂNG THÔNG $mbps MB/s < $PT_MIN_MBPS MB/s: ~40 GB không xong trong hạn 3 giờ. KHÔNG chuyển dữ liệu. Chạy 'eval-vast.sh down' !!!!!!" >&2
        die "packtest: băng thông $mbps MB/s < $PT_MIN_MBPS MB/s, dừng trước khi chuyển dữ liệu"
    fi

    # --- c. đưa nguồn lên VM (rsync chỉ đọc nguồn; không -z vì phần lớn đã nén/nhị phân)
    local rsh s0 secs bytes
    rsh="$(rsh_cmd)"; s0="$(date +%s)"
    tmo ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" 'mkdir -p /srv/src-root/neo4j /srv/packtest' </dev/null || die "tạo thư mục trên VM lỗi"
    tmo rsync -a --no-links --stats -e "$rsh" "$src/exploitdb" "$src/poc" "$src/poc-sources" "root@$HOST:/srv/src-root/" >"$PT/rsync-src.log" || die "rsync nguồn lỗi"
    tmo rsync -a --no-links --stats -e "$rsh" "$src/neo4j/neo4j.dump" "root@$HOST:/srv/src-root/neo4j/" >"$PT/rsync-neo4j.log" || die "rsync neo4j.dump lỗi"
    # như pack.sh: .git/.env*/khoá của mcp-server không rời máy
    tmo rsync -a --no-links --stats --exclude .git --exclude '.env*' --exclude '*.pem' --exclude '*.key' -e "$rsh" "$mcp"/ \
        "root@$HOST:/srv/src-root/exploitdb-mcp-server/" >"$PT/rsync-mcp.log" || die "rsync exploitdb-mcp-server lỗi"
    secs=$(( $(date +%s) - s0 ))
    bytes="$(cat "$PT"/rsync-*.log | awk -F': ' '/^Total transferred file size/ { gsub(/[^0-9]/, "", $2); s += $2 } END { print s + 0 }')"
    say "đưa nguồn: ${secs}s, $bytes byte"

    # --- d. khoá (nếu có) qua stdin vào tệp 0600; thông tin collection prod; tunnel ngược Qdrant prod -> VM 127.0.0.1:16333
    if [ -n "$QKEY" ]; then
        printf '%s' "$QKEY" | tmo ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" 'umask 077; cat > /srv/packtest/qkey' || die "đẩy khoá Qdrant lỗi"
    fi
    tmo ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" 'cat > /srv/packtest/prod-collection.json' <"$PT/prod-collection.json" || die "đẩy thông tin collection lỗi"
    # timeout chạy trực tiếp (không qua hàm tmo) để $! là PID của timeout: kill nó thì timeout chuyển TERM cho ssh.
    timeout --kill-after=30 $(( DL - 600 - $(date +%s) )) ssh "${SSHO[@]}" -p "$PORT" -N -o ExitOnForwardFailure=yes -o ServerAliveCountMax=3 \
        -R "127.0.0.1:16333:127.0.0.1:$qport" "root@$HOST" </dev/null >"$PT/tunnel.log" 2>&1 &
    TUN=$!
    local n ok=0
    for n in $(seq 1 20); do
        kill -0 "$TUN" 2>/dev/null || die "tunnel ngược chết ngay (xem $PT/tunnel.log)"
        if tmo ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" 'curl -s -o /dev/null --max-time 5 http://127.0.0.1:16333/' </dev/null; then ok=1; break; fi
        sleep 1
    done
    [ "$ok" = 1 ] || die "VM không tới được Qdrant prod qua tunnel"

    # --- e/f. pack + restore + kiểm trên VM; kết quả ở /opt/secai-stack/results (collect kéo về)
    local prc=0
    tmo ssh "${SSHO[@]}" -p "$PORT" "root@$HOST" "COLL=$COLL NEO4J_EXPECT='$ne' VM_MIN_FREE_GB=$vmfree STAGE_SECS=$secs STAGE_BYTES=$bytes BW_MBPS=$mbps bash -s" <<'REMOTE_PACKTEST' || prc=$?
set -uo pipefail
R=/opt/secai-stack/results; [ ! -e "$R" ] || mv "$R" "$R.prev-$(date +%Y%m%d-%H%M%S)-$$"; mkdir -p "$R"
W=/srv/packtest SRC=/srv/src-root OUT=/srv/pkg-out DR=/srv/restore-test ST=/opt/secai-stack/stack
QIMG=qdrant/qdrant:v1.10.1 NIMG=neo4j:5.20-community
TIMEV=/usr/bin/time
CHK="$R/checks.tsv"; : >"$CHK"; : >"$R/facts.tsv"
chk() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$CHK"; }       # tên, pass|fail|recorded, chi tiết
fact() { printf '%s\t%s\n' "$1" "$2" >>"$R/facts.tsv"; }
cleanup() { kill "$DFP" 2>/dev/null; shred -u "$W/qkey" 2>/dev/null; docker rm -f packtest-qdrant packtest-neo4j >/dev/null 2>&1; true; }
( while :; do df -B1 --output=used,avail /srv | tail -n1; sleep 10; done ) >"$R/disk-samples.txt" 2>/dev/null &
DFP=$!; trap cleanup EXIT
cat >"$W/facts.py" <<'PY'
# facts.py ROOT -> {dir: {files, bytes}} cho cây dữ liệu + số dòng từng bảng catalog.db (không theo symlink)
import json, os, sqlite3, sys
root, out = sys.argv[1], {}
for d in ("exploitdb", "poc/store", "poc-sources", "exploitdb-mcp-server"):
    n = b = 0
    for dp, dn, fn in os.walk(os.path.join(root, d)):
        for f in fn:
            p = os.path.join(dp, f)
            if os.path.isfile(p) and not os.path.islink(p): n += 1; b += os.path.getsize(p)
    out[d] = {"files": n, "bytes": b}
c = sqlite3.connect("file:" + os.path.join(root, "poc/catalog.db") + "?mode=ro", uri=True)
out["catalog_rows"] = {t: c.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0]
                       for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
json.dump(out, sys.stdout, sort_keys=True)
PY
cat >"$W/qsample.py" <<'PY'
# qsample.py prod URL COLL OUT: 20 điểm ngẫu nhiên trong 1000 id đầu (scroll) của Qdrant prod, kèm payload + vector. Khoá đọc từ tệp.
import json, os, random, sys, urllib.request
url, coll, out = sys.argv[1:4]
key = open("/srv/packtest/qkey").read().strip() if os.path.exists("/srv/packtest/qkey") else ""
def call(path, body):
    rq = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **({"api-key": key} if key else {})})
    return json.load(urllib.request.urlopen(rq, timeout=120))["result"]
ids = [p["id"] for p in call(f"/collections/{coll}/points/scroll", {"limit": 1000, "with_payload": False, "with_vector": False})["points"]]
pick = random.sample(ids, min(20, len(ids)))
json.dump(call(f"/collections/{coll}/points", {"ids": pick, "with_payload": True, "with_vector": True}), open(out, "w"))
PY
used0=$(df -B1 --output=used /srv | tail -n1 | tr -d ' '); fact disk_used_start "$used0"
dfs() { df -B1 --output=used,avail /srv | tail -n1 >>"$R/disk-samples.txt"; }   # mẫu thêm ở các mốc (lấy mẫu 10 s có thể lỡ đỉnh)

# 1. số liệu nguồn (trước khi pack, vì nguồn bị xoá trước restore để vừa đĩa)
if python3 "$W/facts.py" "$SRC" >"$R/src-facts.json"; then chk src_facts pass ""; else chk src_facts fail "facts.py nguồn lỗi"; fi
avail=$(df -B1 --output=avail /srv | tail -n1 | tr -d ' ')
if [ "$avail" -ge $((VM_MIN_FREE_GB << 30)) ]; then chk vm_free pass "$avail"; else chk vm_free fail "$avail < ${VM_MIN_FREE_GB} GB"; fi

ok=0
if ! awk -F'\t' '$2 == "fail" { f = 1 } END { exit !f }' "$CHK"; then
    # 2. mẫu điểm prod + pack (snapshot qua tunnel). Khoá chỉ nằm trong tệp 0600, bị shred ngay sau pack.
    if python3 "$W/qsample.py" http://127.0.0.1:16333 "$COLL" "$W/prod-sample.json" 2>"$R/qsample.log"; then chk qdrant_sample_fetch pass ""; else chk qdrant_sample_fetch fail "xem qsample.log"; fi
    t=$(date +%s)
    QDRANT_API_KEY=$(cat "$W/qkey" 2>/dev/null) QDRANT_URL=http://127.0.0.1:16333 QDRANT_COLLECTION="$COLL" \
        "$TIMEV" -v -o "$R/pack.time" "$ST/pack.sh" data --mcp-dir "$SRC/exploitdb-mcp-server" --neo4j-dump "$SRC/neo4j/neo4j.dump" "$SRC" "$OUT" >"$R/pack.log" 2>&1
    prc=$?; fact pack_secs $(( $(date +%s) - t )); shred -u "$W/qkey" 2>/dev/null; dfs
    if [ "$prc" = 0 ]; then chk pack pass ""; ok=1; else chk pack fail "rc=$prc"; fi
fi
if [ "$ok" = 1 ]; then
    fact package_bytes "$(du -sb "$OUT" | cut -f1)"
    (cd "$OUT" && ls -l --time-style=+ qdrant neo4j ./*.tar.zst) >"$R/package-files.txt" 2>&1
    if (cd "$OUT" && sha256sum -c --strict --quiet SHA256SUMS) >"$R/sha256.log" 2>&1; then chk package_sha256 pass ""; else chk package_sha256 fail "xem sha256.log"; fi
    rm -rf "$SRC"   # nguồn đã đo; xoá để restore vừa đĩa
    # 3. restore vào DATA_ROOT mới với Qdrant cục bộ (đúng image compose) cho recover snapshot
    mkdir -p "$DR/_state/qdrant_storage" "$DR/qdrant"
    docker rm -f packtest-qdrant >/dev/null 2>&1
    docker run -d --name packtest-qdrant -p 127.0.0.1:16334:6333 -v "$DR/_state/qdrant_storage:/qdrant/storage" \
        -v "$DR/qdrant:/qdrant/snapshots" "$QIMG" >/dev/null
    for i in $(seq 1 60); do curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:16334/ && break; sleep 2; done
    t=$(date +%s)
    env -u QDRANT_API_KEY DATA_ROOT="$DR" "$TIMEV" -v -o "$R/restore.time" "$ST/restore-data.sh" --qdrant-url http://127.0.0.1:16334 "$OUT" >"$R/restore.log" 2>&1
    rrc=$?; fact restore_secs $(( $(date +%s) - t )); dfs
    if [ "$rrc" = 0 ]; then chk restore pass ""; else chk restore fail "rc=$rrc"; fi
    # 4. kiểm
    curl -fsS --max-time 60 "http://127.0.0.1:16334/collections/$COLL" >"$W/restored-collection.json" 2>/dev/null || echo '{}' >"$W/restored-collection.json"
    python3 - "$W/prod-collection.json" "$W/restored-collection.json" "$W/prod-sample.json" "$COLL" >>"$CHK" <<'PY'
import json, sys, urllib.request
prod, rest = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])).get("result") or {}
def row(n, ok, d): print(f"{n}\t{'pass' if ok else 'fail'}\t{d}")
rp = rest.get("points_count")
row("qdrant_points", rp == prod["points_count"], f"prod={prod['points_count']} restored={rp}")
rparams = (rest.get("config") or {}).get("params") or {}
row("qdrant_vectors_config", rparams.get("vectors") == prod["params"].get("vectors") and rparams.get("sparse_vectors") == prod["params"].get("sparse_vectors"),
    json.dumps({"vectors": rparams.get("vectors"), "sparse": rparams.get("sparse_vectors")}))
rk = sorted(rest.get("payload_schema") or {})
row("qdrant_payload_indexes", rk == prod["payload_schema_keys"], f"prod={len(prod['payload_schema_keys'])} restored={len(rk)}")
try:
    sample = json.load(open(sys.argv[3]))
    rq = urllib.request.Request(f"http://127.0.0.1:16334/collections/{sys.argv[4]}/points",
                                data=json.dumps({"ids": [p["id"] for p in sample], "with_payload": True, "with_vector": True}).encode(),
                                headers={"Content-Type": "application/json"})
    got = {str(p["id"]): p for p in json.load(urllib.request.urlopen(rq, timeout=120))["result"]}
    def shape(v):
        v = v if isinstance(v, dict) else {"": v}
        return {k: (len(x) if isinstance(x, list) else len(x.get("indices") or []) if isinstance(x, dict) else None) for k, x in v.items()}
    bad = [str(p["id"]) for p in sample if str(p["id"]) not in got or got[str(p["id"])].get("payload") != p.get("payload")
           or shape(got[str(p["id"])].get("vector")) != shape(p.get("vector"))]
    row("qdrant_sample_points", len(sample) > 0 and not bad, f"n={len(sample)} mismatched={bad[:5]}")
except Exception as e:
    row("qdrant_sample_points", False, type(e).__name__)
PY
    # Neo4j: mở bản khôi phục trong container tạm (không auth), đếm node
    docker rm -f packtest-neo4j >/dev/null 2>&1
    docker run -d --name packtest-neo4j -e NEO4J_AUTH=none -v "$DR/_state/neo4j_data:/data" "$NIMG" >/dev/null
    cnt=""
    for i in $(seq 1 60); do
        cnt=$(docker exec packtest-neo4j cypher-shell --format plain 'MATCH (n) RETURN count(n)' 2>/dev/null | tail -n1 | tr -d ' \r')
        [[ "$cnt" =~ ^[0-9]+$ ]] && break; cnt=""; sleep 3
    done
    if [ -z "$cnt" ]; then chk neo4j_nodes fail "không đếm được"
    elif [ -z "$NEO4J_EXPECT" ]; then chk neo4j_nodes recorded "$cnt"
    elif [ "$cnt" = "$NEO4J_EXPECT" ]; then chk neo4j_nodes pass "$cnt"
    else chk neo4j_nodes fail "expected=$NEO4J_EXPECT got=$cnt"; fi
    # tệp: số tệp + tổng byte từng cây, số dòng bảng catalog.db
    if python3 "$W/facts.py" "$DR" >"$R/restored-facts.json"; then
        python3 - "$R/src-facts.json" "$R/restored-facts.json" >>"$CHK" <<'PY'
import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
for k in sorted(a):
    print(f"files_{k.replace('/', '_')}\t{'pass' if a[k] == b.get(k) else 'fail'}\tsrc={json.dumps(a[k])[:200]} restored={json.dumps(b.get(k))[:200]}")
PY
    else chk restored_facts fail "facts.py bản khôi phục lỗi"; fi
    # quyền theo restore-data.sh: poc và poc/store = 10001:10100 có g+w; neo4j data = 7474
    for d in poc poc/store; do
        read -r o m <<<"$(stat -c '%u:%g %a' "$DR/$d" 2>/dev/null)"
        if [ "$o" = 10001:10100 ] && [ $(( 0$m & 020 )) -ne 0 ]; then chk "perm_${d/\//_}" pass "$o $m"; else chk "perm_${d/\//_}" fail "${o:-?} ${m:-?}"; fi
    done
    o=$(stat -c '%u' "$DR/_state/neo4j_data/databases" 2>/dev/null)
    if [ "$o" = 7474 ]; then chk perm_neo4j_data pass "$o"; else chk perm_neo4j_data fail "${o:-?}"; fi
fi
cleanup; trap - EXIT
# Tóm tắt: không có secret (chỉ số đếm, thời gian, kích thước). verdict pass khi mọi kiểm pass (recorded không tính trượt).
python3 - "$R" "$CHK" <<'PY'
import json, os, re, sys
R, chk = sys.argv[1], sys.argv[2]
checks = {}
for l in open(chk):
    n, s, d = (l.rstrip("\n").split("\t") + ["", ""])[:3]
    checks[n] = {"status": s, "detail": d}
facts = dict(l.rstrip("\n").split("\t", 1) for l in open(R + "/facts.tsv") if "\t" in l)
def num(k): return int(facts[k]) if k in facts and facts[k].isdigit() else None
used = [int(l.split()[0]) for l in open(R + "/disk-samples.txt") if l.split() and l.split()[0].isdigit()] + ([num("disk_used_start")] if num("disk_used_start") else [])
def tv(f):
    try: t = open(f).read()
    except OSError: return None
    m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", t); e = re.search(r"Elapsed \(wall clock\) time.*: (\S+)", t)
    return {"max_rss_kb": int(m.group(1)) if m else None, "elapsed": e.group(1) if e else None}
needed = {"pack", "restore", "qdrant_points", "qdrant_sample_points", "package_sha256"}
st = {c["status"] for c in checks.values()}
verdict = "fail" if "fail" in st or not needed <= checks.keys() else "pass"
s = {"verdict": verdict, "checks": checks,
     "timings_s": {"stage_rsync": int(os.environ["STAGE_SECS"]), "pack": num("pack_secs"), "restore": num("restore_secs")},
     "time_v": {"pack": tv(R + "/pack.time"), "restore": tv(R + "/restore.time")},
     "sizes_bytes": {"staged": int(os.environ["STAGE_BYTES"]), "package": num("package_bytes")},
     "disk": {"used_start": num("disk_used_start"), "used_peak": max(used) if used else None,
              "peak_over_start": (max(used) - num("disk_used_start")) if used and num("disk_used_start") is not None else None, "samples": len(used)},
     "bandwidth_mbps": float(os.environ["BW_MBPS"])}
for f in ("src-facts.json", "restored-facts.json"):
    try: s[f.split(".")[0].replace("-", "_")] = json.load(open(f"{R}/{f}"))
    except Exception: pass
json.dump(s, open(R + "/packtest-summary.json", "w"), ensure_ascii=False, indent=1)
print("packtest verdict:", verdict); [print(f"  {k}: {v['status']} {v['detail']}") for k, v in checks.items()]
sys.exit(0 if verdict == "pass" else 1)
PY
rc=$?
sed -i -E 's/([A-Za-z0-9_]*(TOKEN|KEY|SECRET|PASSWORD)[A-Za-z0-9_]*)=[^[:space:]]+/\1=<redacted>/g' "$R"/*.log
(cd "$R" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + >SHA256SUMS)
exit "$rc"
REMOTE_PACKTEST
    [ "$prc" = 0 ] || die "packtest trên VM không đạt/lỗi (rc=$prc); collect để xem results/packtest-summary.json"
}

cmd_down() { exec env STACK_EVAL_DIR="$SD" "$HERE/destroy-eval.sh" --yes; }

cmd_status() {
    need_cmds vastai python3
    if [ -s "$SD/ALERT.txt" ]; then echo "=== ALERT ($SD/ALERT.txt) ==="; tail -n 20 "$SD/ALERT.txt"; echo "=== hết ALERT ==="; fi
    [ -f "$SD/state.json" ] || die "chưa có state ($SD/state.json)"
    local id label price deadline now row
    id="$(sget id)"; label="$(sget label)"; price="$(sget price)"; deadline="$(sget deadline)"; now="$(date +%s)"
    [[ "$id" =~ ^[1-9][0-9]*$ ]] || die "state chưa có ID (pending, label $label); chạy destroy-eval.sh hoặc kiểm tay console"
    row="$(vshow "$id")" || die "tra instance $id bằng vastai không đọc được (DEPRECATED/lỗi); không đoán trạng thái, kiểm tay console"
    echo "id=$id label=$label price=\$$price/h giờ còn lại=$(awk -v d="$deadline" -v n="$now" 'BEGIN{printf "%.2f", (d-n)/3600}')"
    if [ "$row" = GONE ]; then echo "vastai: instance $id KHÔNG còn (tra theo ID)"; return 0; fi
    IFS=$'\t' read -r _ rlabel rprice rstatus _ <<<"$row"
    echo "vastai: label=$rlabel price=\$$rprice/h status=$rstatus"
    [ "$rlabel" = "$label" ] || die "label thật khác state: CẢNH BÁO, không dùng 'down' trước khi kiểm tay"
}

case "${1:-}" in
    up) shift; cmd_up "$@" ;;
    run) cmd_run ;;
    packtest) cmd_packtest ;;
    collect) cmd_collect ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *) echo "usage: eval-vast.sh up [--dry-run] [--i-accept-higher] | run | packtest | collect | down | status" >&2; exit 2 ;;
esac
