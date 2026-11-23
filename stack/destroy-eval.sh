#!/usr/bin/env bash
# destroy-eval.sh --yes | --force-deadline - huỷ ĐÚNG MỘT instance Vast do eval-vast.sh thuê (ID lấy từ state.json, không nhận ID qua argv).
#   --yes             người vận hành xác nhận (eval-vast.sh down).
#   --force-deadline  do timer systemd-run gọi: không hỏi, nhưng vẫn qua toàn bộ kiểm tra bên dưới.
# Từ chối (exit 3, KHÔNG huỷ) nếu: ID không phải một số nguyên đơn; ID nằm trong denylist (62088958 cứng + STACK_EVAL_PROTECTED,
# mặc định $STACK_EVAL_DIR/protected-instances); instance.id lệch state.json; label không bắt đầu `secai-stack-eval-` hoặc lệch state;
# giá > 1.5 x max_dph; danh sách vastai không parse được (DEPRECATED/lỗi: fail an toàn, không đoán).
# `vastai destroy instance <ID> -y --raw` (không -y thì vastai hỏi input(), stdin rỗng => "Aborted." exit 0 và KHÔNG huỷ gì), phải trả
# success=true. "Đã biến mất" CHỈ được kết luận bằng tra theo ID (`vastai show instance <ID> --raw` trả instances=null); danh sách
# (đã phân trang, rỗng/lỗi/id kiểu khác) không bao giờ là bằng chứng. Thử lại tối đa STACK_EVAL_DESTROY_RETRIES (5) lần, chờ
# STACK_EVAL_DESTROY_WAIT giây (10). Chưa chứng minh biến mất: exit 1, timer lặp (eval-vast.sh đặt) vẫn chạy. Chứng minh rồi: exit 0
# (idempotent), dừng timer, ghi destroyed_at.
# state.json có pending=true và id rỗng (create chưa trả ID): tìm theo label (phân trang, đúng một khớp); không thấy: exit 4 (timer
# tiếp tục) cho tới deadline, qua deadline thì exit 0 và dừng timer. Không có state.json mà có instance.id: vẫn kiểm prefix label + giá.
# Không đụng khoá API tài khoản Vast: vastai tự đọc ~/.config/vastai/vast_api_key.
set -uo pipefail

HARD_DENY=62088958   # instance prod: KHÔNG BAO GIỜ huỷ, bất kể state/label/protected file
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SD="${STACK_EVAL_DIR:-$REPO/local/stack-eval}"
PROTECTED="${STACK_EVAL_PROTECTED:-$SD/protected-instances}"
RETRIES="${STACK_EVAL_DESTROY_RETRIES:-5}"
WAIT="${STACK_EVAL_DESTROY_WAIT:-10}"

refuse() { echo "!!!!!! DESTROY-EVAL REFUSED (không huỷ gì): $* !!!!!!" >&2; exit 3; }
[ "$#" -eq 1 ] && { [ "$1" = "--yes" ] || [ "$1" = "--force-deadline" ]; } \
    || { echo "usage: destroy-eval.sh --yes|--force-deadline (không nhận ID; ID lấy từ state.json)" >&2; exit 2; }
[[ "$RETRIES" =~ ^[1-9][0-9]*$ ]] || refuse "STACK_EVAL_DESTROY_RETRIES không hợp lệ"

ID=""; LABEL=""; CAP=0.30; TIMERS=""; PENDING=0; DEADLINE=0
if [ -f "$SD/state.json" ]; then
    # i:<id> l:<label> m:<max_dph> t:<timers> p:<pending> d:<deadline>; id chỉ nhận chuỗi/số nguyên, kiểu khác thành BAD.
    IFS=$'\t' read -r si sl sm st sp sd < <(python3 -I - "$SD/state.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    i = d.get("id")
    i = str(i) if isinstance(i, (int, str)) and not isinstance(i, bool) else "BAD"
    l = d.get("label"); l = l if isinstance(l, str) else ""
    try: m = float(d.get("max_dph", 0.30))
    except (TypeError, ValueError): m = 0.30
    try: dl = int(d.get("deadline", 0))
    except (TypeError, ValueError): dl = 0
    t = " ".join(x for x in d.get("timers", []) if isinstance(x, str)) if isinstance(d.get("timers", []), list) else ""
    print("\t".join(["i:" + i, "l:" + l, "m:%s" % m, "t:" + t, "p:%d" % (d.get("pending") is True), "d:%d" % dl]).replace("\n", " "))
except Exception:
    print("i:BAD\tl:\tm:0.30\tt:\tp:0\td:0")
PY
    ) || refuse "đọc state.json lỗi"
    ID="${si#i:}"; LABEL="${sl#l:}"; CAP="${sm#m:}"; TIMERS="${st#t:}"; PENDING="${sp#p:}"; DEADLINE="${sd#d:}"
elif [ -f "$SD/instance.id" ]; then
    ID="$(tr -d '[:space:]' <"$SD/instance.id")"   # không có state: vẫn kiểm prefix label + giá mặc định bên dưới
else
    refuse "không có $SD/state.json hay instance.id (không biết huỷ ID nào)"
fi

# list_by_label LABEL -> in ID các instance mang đúng label (đi hết các trang next_token); rc != 0 nếu không đọc được.
list_by_label() {
    python3 -I - "$1" <<'PY'
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
    if isinstance(i, dict) and i.get("label") == sys.argv[1]: print(sid(i.get("id")))
PY
}

stop_timers() {
    local u
    for u in $TIMERS; do
        [[ "$u" =~ ^[A-Za-z0-9_.@-]+$ ]] || continue
        systemctl --user stop "$u.timer" >/dev/null 2>&1 || true
    done
}
mark_destroyed() {
    [ -f "$SD/state.json" ] || return 0
    python3 -I - "$SD/state.json" <<'PY' >/dev/null 2>&1 || true
import json, os, sys, time
p = sys.argv[1]; d = json.load(open(p)); d.setdefault("destroyed_at", int(time.time()))
t = p + ".tmp"; open(t, "w").write(json.dumps(d)); os.chmod(t, 0o600); os.replace(t, p)
PY
}

if [ "$PENDING" = 1 ] && [ -z "$ID" ]; then
    case "$LABEL" in secai-stack-eval-?*) ;; *) refuse "state pending nhưng label '$LABEL' không bắt đầu secai-stack-eval-" ;; esac
    found="$(list_by_label "$LABEL")" || refuse "danh sách vastai không đọc được khi tìm theo label; không đoán, không huỷ"
    n="$(printf '%s' "$found" | grep -c .)"
    [ "$n" -le 1 ] || refuse "$n instance cùng label '$LABEL'; kiểm tay trên console"
    if [ "$n" = 0 ]; then
        if [ "$(date +%s)" -ge "$DEADLINE" ]; then
            stop_timers; echo "destroy-eval: không có instance nào mang label $LABEL tới hết deadline; dừng timer."; exit 0
        fi
        echo "destroy-eval: chưa thấy instance nào mang label $LABEL (create chưa lộ ra?); giữ timer tới deadline." >&2; exit 4
    fi
    ID="$found"
    python3 -I - "$SD/state.json" "$ID" <<'PY' >/dev/null 2>&1 || true
import json, os, sys
p = sys.argv[1]; d = json.load(open(p)); d["id"] = sys.argv[2]; d["pending"] = False
t = p + ".tmp"; open(t, "w").write(json.dumps(d)); os.chmod(t, 0o600); os.replace(t, p)
PY
fi

[[ "$ID" =~ ^[1-9][0-9]*$ ]] || refuse "ID '$ID' không phải một số nguyên đơn (rỗng/glob/nhiều ID/sai kiểu)"
[ "$ID" != "$HARD_DENY" ] || refuse "ID $ID là instance PROD (denylist cứng)"
if [ -f "$PROTECTED" ]; then
    while IFS= read -r p || [ -n "$p" ]; do
        p="${p//[[:space:]]/}"
        [ -n "$p" ] || continue
        [ "$p" != "$ID" ] || refuse "ID $ID nằm trong danh sách bảo vệ $PROTECTED"
    done <"$PROTECTED"
fi
if [ -f "$SD/instance.id" ]; then
    [ "$(tr -d '[:space:]' <"$SD/instance.id")" = "$ID" ] || refuse "instance.id lệch state.json (không rõ huỷ ID nào)"
fi
[ -z "$LABEL" ] || case "$LABEL" in secai-stack-eval-?*) ;; *) refuse "label state '$LABEL' không bắt đầu secai-stack-eval-" ;; esac

# show_instance -> "FOUND<TAB>label<TAB>price" hoặc "GONE"; rc 2 nếu không đọc được (lỗi/DEPRECATED/không phải JSON/id lạ).
# GONE chỉ khi vastai nói rõ instances=null. Tra theo ID, không dùng danh sách.
show_instance() {
    vastai show instance "$ID" --raw </dev/null 2>/dev/null | python3 -I -c '
import json, sys
def sid(x): return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)
try:
    d = json.load(sys.stdin)
    row = d
    if isinstance(d, dict) and "instances" in d:
        row = d["instances"]
        if row is None:
            print("GONE"); sys.exit(0)
    if isinstance(row, list) and len(row) == 1: row = row[0]
    assert isinstance(row, dict) and sid(row.get("id")) == sys.argv[1]
except Exception:
    sys.exit(2)
print("FOUND\t%s\t%s" % (str(row.get("label", "")).replace("\t", " "), row.get("dph_total", "")))' "$ID"
}

finish() {
    stop_timers
    mark_destroyed
    echo "destroy-eval: instance $ID đã được chứng minh biến mất (tra theo ID); timer đã dừng."
    exit 0
}

res="$(show_instance)" || refuse "tra instance $ID bằng vastai không đọc được (DEPRECATED/lỗi); không đoán, không huỷ"
if [ "$res" = "GONE" ]; then finish; fi
case "$res" in FOUND*) ;; *) refuse "kết quả vastai lạ" ;; esac
IFS=$'\t' read -r _ ilabel iprice <<<"$res"
case "$ilabel" in secai-stack-eval-?*) ;; *) refuse "label thật của instance $ID ('$ilabel') không bắt đầu secai-stack-eval-" ;; esac
[ -z "$LABEL" ] || [ "$ilabel" = "$LABEL" ] || refuse "label thật của instance $ID ('$ilabel') khác state ('$LABEL')"
awk -v p="$iprice" -v c="$CAP" 'BEGIN { exit !(p ~ /^[0-9.]+$/ && c ~ /^[0-9.]+$/ && p + 0 <= 1.5 * c + 1e-9) }' \
    || refuse "giá '$iprice' vượt 1.5 x cap $CAP (hoặc không đọc được). Nếu đúng là instance eval của bạn: HUỶ TAY trên console Vast (ID $ID)"

for ((n = 1; n <= RETRIES; n++)); do
    out="$(vastai destroy instance "$ID" -y --raw </dev/null 2>/dev/null)" || true
    if ! printf '%s' "$out" | python3 -I -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("success") is True else 1)' 2>/dev/null; then
        echo "destroy-eval: lần $n: destroy KHÔNG báo success (đầu ra: $(printf '%s' "$out" | head -c 200))" >&2
    fi
    sleep "$WAIT"
    res="$(show_instance)" && [ "$res" = "GONE" ] && finish
    echo "destroy-eval: lần $n/$RETRIES: chưa chứng minh instance $ID đã biến mất" >&2
done
echo "!!!!!! destroy-eval: THẤT BẠI sau $RETRIES lần. Instance $ID CÓ THỂ CÒN và đang tính tiền: HUỶ TAY trên console Vast (https://console.vast.ai/instances/) ngay. Timer lặp (nếu có) vẫn thử lại. !!!!!!" >&2
exit 1
