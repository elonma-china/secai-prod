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
#   EVAL_REPO_ROOT (nguồn git archive), EVAL_POLL_SLEEP (15). Riêng run: EVAL_SKIP_CASES (mặc định UI-POC-1..3 UI-TR-2 UI-TR-3:
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
    # cpu_ram trong truy vấn CLI tính bằng GB (CLI nhân 1000); --storage 100 để dph_total gồm cả đĩa 100GB; vms_enabled=true cho VM.
    local query="dph_total<=$cap cpu_ram>=$ram disk_space>=100 inet_down>=200 reliability>=0.98 rentable=true verified=true vms_enabled=true"
    local offers pick OFFER PRICE
    offers="$(vastai search offers "$query" --storage 100 -o dph_total --raw 2>/dev/null)" || die "vastai search lỗi"
    pick="$(printf '%s' "$offers" | python3 -I -c '
import json, sys
cap, ram = float(sys.argv[1]), float(sys.argv[2])
skip = set(sys.argv[3].split())   # EVAL_EXCLUDE_OFFERS: offer đã thuê mà không lên được ssh
try:
    d = json.load(sys.stdin)
    d = d if isinstance(d, list) else d.get("offers", [])
except Exception:
    sys.exit(2)
ok = [o for o in d if isinstance(o, dict) and str(o.get("id")) not in skip and isinstance(o.get("dph_total"), (int, float)) and o["dph_total"] <= cap
      and (o.get("cpu_ram") or 0) >= ram * 1024 and (o.get("disk_space") or 0) >= 100 and (o.get("inet_down") or 0) >= 200
      and (o.get("reliability2") or o.get("reliability") or 0) >= 0.98]
if not ok: sys.exit(1)
b = min(ok, key=lambda o: o["dph_total"]); print(b["id"], b["dph_total"])' "$cap" "$ram" "${EVAL_EXCLUDE_OFFERS:-}")" \
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
    python3 -I - "$EVAL_ENV_FILE" "$KEYFILE" "$(dirname "$KEYFILE")/vast_tfa_key" "$HOME/.vast_api_key" <<'PY' || die "$EVAL_ENV_FILE chứa khoá API tài khoản Vast: từ chối đẩy sang máy thuê (gỡ nó khỏi tệp)"
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
    echo "CẢNH BÁO: $EVAL_ENV_FILE (chứa API key) sẽ được CHUYỂN sang máy thuê ngoài của Vast. Xoay các khoá đó sau khi eval xong."

    # --- Chuẩn bị TRƯỚC khi thuê: trap, state "pending" (có label, chưa có ID) và timer deadline. Nếu create thành công mà ID không rõ,
    # timer/trap vẫn huỷ được qua destroy-eval.sh (tra theo label). Không có deadline thì không thuê.
    DONE=0; CREATING=0; PREPARED=1; trap 'on_exit_up' EXIT; trap 'exit 130' INT; trap 'exit 143' TERM
    local now; now="$(date +%s)"
    sset 'id=""' "label=\"$label\"" "created_at=$now" "deadline=$((now + hours * 3600))" "price=$PRICE" \
         "max_dph=$cap" "max_hours=$hours" "timers=[]" "pending=true" "account_id=\"$acct\"" || die "không ghi được state.json"
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
    collect) cmd_collect ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *) echo "usage: eval-vast.sh up [--dry-run] [--i-accept-higher] | run | collect | down | status" >&2; exit 2 ;;
esac
