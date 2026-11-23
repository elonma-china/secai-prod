#!/usr/bin/env bash
# destroy-eval.sh --yes | --force-deadline - huỷ ĐÚNG MỘT instance Vast do eval-vast.sh thuê (ID lấy từ state.json, không nhận ID qua argv).
#   --yes             người vận hành xác nhận (eval-vast.sh down).
#   --force-deadline  do timer systemd-run gọi: không hỏi, nhưng vẫn qua toàn bộ kiểm tra bên dưới.
# Từ chối (exit 3, KHÔNG huỷ) nếu: ID không phải một số nguyên đơn; ID nằm trong denylist (62088958 cứng + STACK_EVAL_PROTECTED,
# mặc định $STACK_EVAL_DIR/protected-instances); instance.id lệch state.json; label không bắt đầu `secai-stack-eval-` hoặc lệch state;
# giá > 1.5 x max_dph; danh sách vastai không parse được (DEPRECATED/lỗi: fail an toàn, không đoán).
# Sau `vastai destroy instance <ID>`: kiểm danh sách đã hết ID, thử lại tối đa STACK_EVAL_DESTROY_RETRIES (5) lần, mỗi lần chờ
# STACK_EVAL_DESTROY_WAIT giây (10). Hết lượt mà chưa biến mất: exit 1, timer vẫn chạy. Đã biến mất: exit 0 (idempotent) và dừng timer.
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

[ -f "$SD/state.json" ] || refuse "không có $SD/state.json (không biết huỷ ID nào)"
# i:<id> l:<label> m:<max_dph> t:<timers> ; id chỉ nhận chuỗi/số nguyên, mọi kiểu khác (list, null...) thành BAD.
IFS=$'\t' read -r si sl sm st < <(python3 -I - "$SD/state.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    i = d.get("id")
    i = str(i) if isinstance(i, (int, str)) and not isinstance(i, bool) else "BAD"
    l = d.get("label"); l = l if isinstance(l, str) else ""
    try: m = float(d.get("max_dph", 0.30))
    except (TypeError, ValueError): m = 0.30
    t = " ".join(x for x in d.get("timers", []) if isinstance(x, str)) if isinstance(d.get("timers", []), list) else ""
    print("\t".join(["i:" + i, "l:" + l, "m:%s" % m, "t:" + t]).replace("\n", " "))
except Exception:
    print("i:BAD\tl:\tm:0.30\tt:")
PY
) || refuse "đọc state.json lỗi"
ID="${si#i:}"; LABEL="${sl#l:}"; CAP="${sm#m:}"; TIMERS="${st#t:}"

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
case "$LABEL" in secai-stack-eval-?*) ;; *) refuse "label state '$LABEL' không bắt đầu secai-stack-eval-" ;; esac

# find_instance -> in "FOUND<TAB>label<TAB>price" hoặc "GONE"; rc 2 nếu vastai không in JSON danh sách parse được.
find_instance() {
    vastai show instances-v1 --raw 2>/dev/null | python3 -I -c '
import json, sys
try:
    d = json.load(sys.stdin)
    if isinstance(d, dict): d = d.get("instances")
    assert isinstance(d, list)
except Exception:
    sys.exit(2)
want = sys.argv[1]
for i in d:
    if isinstance(i, dict) and str(i.get("id")) == want:
        print("FOUND\t%s\t%s" % (str(i.get("label", "")).replace("\t", " "), i.get("dph_total", "")))
        sys.exit(0)
print("GONE")' "$ID"
}

stop_timers() {
    local u
    for u in $TIMERS; do
        [[ "$u" =~ ^[A-Za-z0-9_.@-]+$ ]] || continue
        systemctl --user stop "$u.timer" >/dev/null 2>&1 || true
    done
}
finish() {
    stop_timers
    python3 -I - "$SD/state.json" <<'PY' >/dev/null 2>&1 || true
import json, sys, time
p = sys.argv[1]; d = json.load(open(p)); d.setdefault("destroyed_at", int(time.time()))
json.dump(d, open(p, "w"))
PY
    echo "destroy-eval: instance $ID đã biến mất; timer đã dừng."
    exit 0
}

res="$(find_instance)" || refuse "danh sách instance của vastai không parse được (DEPRECATED/lỗi); không đoán, không huỷ"
if [ "$res" = "GONE" ]; then finish; fi
case "$res" in FOUND*) ;; *) refuse "kết quả vastai lạ" ;; esac
IFS=$'\t' read -r _ ilabel iprice <<<"$res"
[ "$ilabel" = "$LABEL" ] || refuse "label thật của instance $ID ('$ilabel') khác state ('$LABEL')"
awk -v p="$iprice" -v c="$CAP" 'BEGIN { exit !(p ~ /^[0-9.]+$/ && c ~ /^[0-9.]+$/ && p + 0 <= 1.5 * c + 1e-9) }' \
    || refuse "giá '$iprice' vượt 1.5 x cap $CAP (hoặc không đọc được)"

for ((n = 1; n <= RETRIES; n++)); do
    vastai destroy instance "$ID" >/dev/null 2>&1 || echo "destroy-eval: lần $n: lệnh destroy trả lỗi" >&2
    sleep "$WAIT"
    res="$(find_instance)" && [ "$res" = "GONE" ] && finish
    echo "destroy-eval: lần $n/$RETRIES: instance $ID chưa biến mất" >&2
done
echo "destroy-eval: THẤT BẠI sau $RETRIES lần; instance $ID CÒN và đang tính tiền; timer tiếp tục thử lại. Huỷ tay trên console Vast nếu cần." >&2
exit 1
