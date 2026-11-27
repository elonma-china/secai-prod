#!/usr/bin/env bash
# ghcr-vm-test.sh - chạy TRÊN máy VM thuê (root, máy trắng chỉ có Docker), làm đúng các bước người vận hành làm với gói GHCR:
#   curl stack/secai + model stub từ raw GitHub ở commit REF -> stub model trên IP docker0 -> secai install V1 -> status/check ->
#   acceptance/run-api.sh -> upgrade V2 -> rollback -> data update D2 -> (FAILTEST=1) tắt stub rồi upgrade V2: phải tự rollback.
# Gọi bởi `eval-vast.sh ghcrtest`: ssh "REF=... V1=... V2=... D1=... D2=... REPO=... FAILTEST=... bash -s" < tệp này.
#   REF (SHA commit 40 hex), V1/V2 (YYYY.MM.DD-<sha8>, khác nhau), D1/D2 (YYYY.MM.DD[.N], khác nhau), REPO (owner/repo), FAILTEST (0|1),
#   DATA_DIR (tuỳ chọn: thư mục gói dữ liệu đã rsync lên VM; có thì install --data-dir và data load thay cho kéo secai-data
#   private), hoặc SECAI_DOCKERHUB_TOKEN_FILE (token Docker Hub CHỈ ĐỌC đã chép lên VM, 0600): kéo dữ liệu thật từ Docker Hub.
# Kết quả trong $RESULTS_DIR (/opt/secai-stack/results): steps.txt ('<bước> rc=<n>' hoặc '<bước> skipped'), <bước>.log, timings.tsv,
#   status-<n>.json, model-stub.log, acceptance/, summary.json, SHA256SUMS (cho `eval-vast.sh collect`). Thoát 0 chỉ khi verdict = pass.
# Không có secret thật: khoá model là khoá stub cố định, chỉ qua env đã export cho đúng lệnh install (không lên argv); log vẫn che
#   *TOKEN/KEY/SECRET/PASSWORD*=... như REMOTE_RUN trước khi tính SHA256SUMS.
# Chỉ để test (trên VM không đặt): RESULTS_DIR, SECAI_BIN (/root/secai), STUB_PY (/root/model_stub.py; pid ở <tên>.pid),
#   STUB_HOST (bỏ dò IP docker0), SECAI_HOME (/opt/secai).
set -uo pipefail

# Toàn bộ thân script nằm trong hàm, gọi ở dòng cuối: `bash -s` đọc hết rồi mới chạy (ssh đứt giữa chừng không chạy nửa script).
die() { echo "ghcr-vm-test: $*" >&2; exit 2; }

validate() {
    local vre='^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{8}$' dre='^[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[0-9]{1,2})?$'
    [[ "$REF" =~ ^[0-9a-f]{40}$ ]] || die "REF phải là SHA commit đủ 40 ký tự hex thường: '$REF'"
    [[ "$V1" =~ $vre ]] && [[ "$V2" =~ $vre ]] || die "V1/V2 phải dạng YYYY.MM.DD-<sha8>: '$V1' '$V2'"
    [ "$V1" != "$V2" ] || die "V1 và V2 phải khác nhau"
    [[ "$D1" =~ $dre ]] && [[ "$D2" =~ $dre ]] || die "D1/D2 phải dạng YYYY.MM.DD hoặc YYYY.MM.DD.N: '$D1' '$D2'"
    [ "$D1" != "$D2" ] || die "D1 và D2 phải khác nhau"
    [[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "REPO phải dạng owner/repo: '$REPO'"
    [[ "$FAILTEST" =~ ^[01]$ ]] || die "FAILTEST phải là 0 hoặc 1: '$FAILTEST'"
    [ -z "${STUB_HOST:-}" ] || is_ipv4 "$STUB_HOST" || die "STUB_HOST phải là IPv4: '$STUB_HOST'"
    [ -z "$DATA_DIR" ] || [[ "$DATA_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "DATA_DIR phải là đường dẫn tuyệt đối đơn giản: '$DATA_DIR'"
    [ -z "${SECAI_DOCKERHUB_TOKEN_FILE:-}" ] || [[ "$SECAI_DOCKERHUB_TOKEN_FILE" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        || die "SECAI_DOCKERHUB_TOKEN_FILE phải là đường dẫn tuyệt đối đơn giản"
    [ -n "$DATA_DIR" ] || [ -n "${SECAI_DOCKERHUB_TOKEN_FILE:-}" ] \
        || die "cần DATA_DIR (gói dữ liệu) hoặc SECAI_DOCKERHUB_TOKEN_FILE (token Docker Hub chỉ đọc): dữ liệu là repo private"
}
is_ipv4() { [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; }

# step TÊN LỆNH...: chạy với stdin=/dev/null (script đang được đọc từ stdin), ghi <TÊN>.log, 'TÊN rc=N' và thời gian.
step() {
    local name=$1 t0 r; shift
    if [ "$ABORT" != 0 ]; then echo "$name skipped" >>"$R/steps.txt"; echo "$name skipped"; return 0; fi
    t0=$(date +%s)
    "$@" </dev/null >"$R/$name.log" 2>&1; r=$?
    printf '%s\t%s\n' "$name" "$(($(date +%s) - t0))" >>"$R/timings.tsv"
    echo "$name rc=$r" >>"$R/steps.txt"
    echo "$name rc=$r"
    [ "$r" = 0 ] || rc=1
    return "$r"
}
abort_on_fail() { "$@" || ABORT=1; }

facts() {
    local c
    for c in nproc "free -g" "df -h /" "docker version" "uname -a"; do echo "### $c"; $c; done
    return 0
}

fetch() {
    local base="https://raw.githubusercontent.com/$REPO/$REF"
    curl -fsSL --retry 3 "$base/stack/secai" -o "$SECAI_BIN" || return 1
    chmod +x "$SECAI_BIN" || return 1
    curl -fsSL --retry 3 "$base/stack/acceptance/stubs/model_stub.py" -o "$STUB_PY" || return 1
    echo "nguồn: $base"
    sha256sum "$SECAI_BIN" "$STUB_PY"
}

stub_ip() {
    if [ -n "${STUB_HOST:-}" ]; then echo "$STUB_HOST"; return 0; fi
    ip -4 -o addr show docker0 | awk '{ split($4, a, "/"); print a[1]; exit }'
}
# Dừng đúng tiến trình stub ghi trong tệp pid (pid có thể đã bị tái dùng: kiểm cmdline trước khi kill).
stop_stub() {
    local p i
    p=$(cat "$PIDF" 2>/dev/null) || return 0
    rm -f "$PIDF"
    [[ "$p" =~ ^[0-9]+$ ]] || return 0
    tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qF -- "$STUB_PY" || return 0
    kill "$p" 2>/dev/null
    wait "$p" 2>/dev/null   # con của shell này thì thu dọn luôn (không để zombie làm kill -0 tưởng còn sống)
    for i in $(seq 1 10); do kill -0 "$p" 2>/dev/null || return 0; sleep 1; done
    kill -9 "$p" 2>/dev/null
    return 0
}
wait_stub() {
    local i
    for i in $(seq 1 30); do
        if curl -fsS --max-time 2 "http://$IP:$STUB_PORT/health"; then echo; echo "stub trả /health sau $i lần thử"; return 0; fi
        sleep 1
    done
    echo "stub không trả /health sau 30 s"
    return 1
}
start_stub() {
    stop_stub
    IP=$(stub_ip)
    is_ipv4 "$IP" || { echo "không lấy được IPv4 của docker0: '$IP'"; return 1; }
    echo "stub: $IP:$STUB_PORT"
    nohup python3 -I "$STUB_PY" --host "$IP" --port "$STUB_PORT" </dev/null >>"$R/model-stub.log" 2>&1 &
    echo "$!" >"$PIDF"
    wait_stub
}

# Sau khi stub sống lại, /health của gateway còn cache 30 s kết quả "embedding/rerank hỏng": chờ gateway thấy lại stub
# (serving.embedding.ok và serving.reranker.ok) rồi mới check, tối đa GW_WAIT_SECS (90) giây.
wait_gateway_deps() {
    local i url="${GW_HEALTH_URL:-http://127.0.0.1:8131/health}" n=$(( ${GW_WAIT_SECS:-90} / 3 + 1 ))
    for i in $(seq 1 "$n"); do
        curl -sS --max-time 6 "$url" 2>/dev/null | python3 -I -c 'import json,sys
h = json.load(sys.stdin); s = h.get("serving") or {}
sys.exit(0 if (s.get("embedding") or {}).get("ok") and (s.get("reranker") or {}).get("ok") else 1)' 2>/dev/null && { echo "gateway thấy stub sau ~$(( (i - 1) * 3 ))s"; return 0; }
        sleep 3
    done
    echo "gateway chưa thấy stub sau ${GW_WAIT_SECS:-90}s"; return 1
}

# Cấu hình chỉ cho lệnh install, qua env đã export (không trên dòng lệnh), như người vận hành export trước khi chạy.
install_v1() (
    STUBKEY=stub-test-key-not-secret   # khoá stub công khai, không phải secret
    export LLM_BASE_URL="http://$IP:$STUB_PORT/v1" LLM_MODEL=DuyTa/Cyber-F1-AWQ LLM_API_KEY="$STUBKEY"
    export EMBEDDING_API_URL="http://$IP:$STUB_PORT" EMBEDDING_MODEL=DuyTa/sec-embedding EMBEDDING_API_KEY="$STUBKEY"
    export RERANKER_API_URL="http://$IP:$STUB_PORT" RERANKER_MODEL=DuyTa/sec-rerank RERANKER_API_KEY="$STUBKEY"
    export VERIFY_ALLOWED_TARGETS=10.250.0.0/24 GATEWAY_BIND=127.0.0.1 SECAI_DATA_VERSION="$D1"
    if [ -n "$DATA_DIR" ]; then exec "$SECAI_BIN" install "$V1" --data-dir "$DATA_DIR"; fi
    exec "$SECAI_BIN" install "$V1"
)

# check_status N KHOÁ=GIÁ_TRỊ...: lưu `secai status --json` vào status-N.json, đòi đúng các giá trị và images_match = true.
check_status() {
    local n=$1; shift
    "$SECAI_BIN" status --json >"$R/status-$n.json" || { echo "secai status --json lỗi"; return 1; }
    python3 -I -c '
import json, sys
try:
    s = json.load(open(sys.argv[1]))
    assert isinstance(s, dict)
except Exception as e:
    print("status --json không phải một object JSON:", type(e).__name__); sys.exit(1)
bad = 0
for k, v in [kv.split("=", 1) for kv in sys.argv[2:]] + [("images_match", True)]:
    ok = s.get(k) is v if v is True else s.get(k) == v
    bad += not ok
    print("đúng" if ok else "SAI", k, "=", json.dumps(s.get(k)), "" if ok else "(mong đợi %s)" % json.dumps(v))
print("services:", json.dumps(s.get("services"), ensure_ascii=False))
sys.exit(1 if bad else 0)' "$R/status-$n.json" "$@"
}

acceptance() (
    cd "$SECAI_HOME/stack" || exit 1
    export ACCEPT_OUT="$R/acceptance" SKIP_CASES="UI-POC-1 UI-POC-2 UI-POC-3 UI-TR-2 UI-TR-3" SKIP_REASONS='{
"UI-POC-1": "không chạy: cần LLM thật gọi tool + lab", "UI-POC-2": "không chạy: cần LLM thật gọi tool + lab",
"UI-POC-3": "không chạy: cần LLM thật gọi tool + lab",
"UI-TR-2": "không chạy: profile translation không thuộc bộ phát hành",
"UI-TR-3": "không chạy: profile translation không thuộc bộ phát hành"}'
    exec ./acceptance/run-api.sh http://127.0.0.1:8131
)

# Stub tắt => check sau upgrade hỏng => secai phải tự rollback: rc 3, hoặc 4 (đã rollback nhưng check sau rollback cũng hỏng vì stub tắt).
failtest() {
    local r
    stop_stub
    if curl -fsS --max-time 2 "http://$IP:$STUB_PORT/health" >/dev/null 2>&1; then echo "stub vẫn trả lời sau khi tắt"; return 1; fi
    echo "stub đã tắt; upgrade $V2 phải hỏng ở check và tự rollback về $V1"
    "$SECAI_BIN" upgrade "$V2"; r=$?
    echo "$r" >"$R/failtest-upgrade.rc"
    echo "secai upgrade rc=$r (mong đợi 3 hoặc 4)"
    [ "$r" = 3 ] || [ "$r" = 4 ]
}

# summary.json; in verdict ra stdout. pass chỉ khi mọi bước rc=0 và nghiệm thu có ca đạt, không ca trượt.
summarize() {
    python3 -I -c '
import hashlib, json, os, sys
R, rc = sys.argv[1], int(sys.argv[2])
steps, timings = {}, {}
for l in open(R + "/steps.txt"):
    n, _, v = l.strip().partition(" ")
    if n: steps[n] = v
try:
    for l in open(R + "/timings.tsv"):
        n, _, t = l.rstrip("\n").partition("\t")
        if t.isdigit(): timings[n] = int(t)
except OSError:
    pass
acc = None
if steps.get("acceptance", "").startswith("rc="):
    try: acc = json.load(open(R + "/acceptance/summary.json"))
    except (OSError, ValueError): acc = None
def sha(p):
    try: return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError: return None
try: ftrc = int(open(R + "/failtest-upgrade.rc").read())
except (OSError, ValueError): ftrc = None
if rc != 0 or not steps or any(v != "rc=0" for v in steps.values()) or (isinstance(acc, dict) and acc.get("fail")):
    verdict = "fail"
elif not isinstance(acc, dict) or not acc.get("pass"):
    verdict = "incomplete"
else:
    verdict = "pass"
e = os.environ
json.dump({"verdict": verdict, "steps": steps, "timings_s": timings,
           "versions": {"ref": e["REF"], "repo": e["REPO"], "v1": e["V1"], "v2": e["V2"], "d1": e["D1"], "d2": e["D2"], "data_dir": e.get("DATA_DIR", "")},
           "fetched_sha256": {"secai": sha(e["SECAI_BIN"]), "model_stub": sha(e["STUB_PY"])},
           "failtest": e["FAILTEST"] == "1", "failtest_upgrade_rc": ftrc, "acceptance": acc},
          open(R + "/summary.json", "w"), ensure_ascii=False, indent=1)
print(verdict)' "$R" "$rc"
}

main() {
    REF=${REF:-} V1=${V1:-} V2=${V2:-} D1=${D1:-} D2=${D2:-} REPO=${REPO:-elonma-china/secai-prod} FAILTEST=${FAILTEST:-1}
    DATA_DIR=${DATA_DIR:-}
    validate
    R=${RESULTS_DIR:-/opt/secai-stack/results}
    SECAI_BIN=${SECAI_BIN:-/root/secai} STUB_PY=${STUB_PY:-/root/model_stub.py}
    PIDF=${STUB_PY%.py}.pid STUB_PORT=18000 IP=""
    export REF V1 V2 D1 D2 REPO FAILTEST SECAI_BIN STUB_PY DATA_DIR
    export SECAI_HOME=${SECAI_HOME:-/opt/secai} SECAI_YES=1
    # Mỗi lần chạy một thư mục kết quả mới; thư mục rỗng do `up` tạo thì dùng lại.
    rmdir "$R" 2>/dev/null
    [ ! -e "$R" ] || mv "$R" "$R.prev-$(date +%Y%m%d-%H%M%S)-$$" || die "không dời được kết quả cũ $R"
    mkdir -p "$R" || die "không tạo được $R"
    # Mất ssh (stdout/stderr đóng): không chết vì SIGPIPE, chạy tiếp tới hết và ghi phần còn lại vào console.log.
    trap 'exec >>"$R/console.log" 2>&1' PIPE
    rc=0 ABORT=0
    echo "ghcr-vm-test: $REPO@$REF, stack $V1 -> $V2, dữ liệu $D1 -> $D2, failtest=$FAILTEST"

    step facts facts
    abort_on_fail step fetch fetch
    abort_on_fail step stub start_stub
    abort_on_fail step install install_v1
    step status1 check_status 1 version="$V1" data_version="$D1"
    step check1 "$SECAI_BIN" check
    step acceptance acceptance
    abort_on_fail step upgrade "$SECAI_BIN" upgrade "$V2"
    step status2 check_status 2 version="$V2" previous_version="$V1"
    abort_on_fail step rollback "$SECAI_BIN" rollback
    step status3 check_status 3 version="$V1"
    if [ -n "$DATA_DIR" ]; then step dataupdate "$SECAI_BIN" data load "$DATA_DIR" "$D2"
    else step dataupdate "$SECAI_BIN" data update "$D2"; fi
    step status4 check_status 4 data_version="$D2" previous_data_version="$D1"
    step check2 "$SECAI_BIN" check
    if [ "$FAILTEST" = 1 ]; then
        step failtest failtest
        step status5 check_status 5 version="$V1"
        step stub_restart start_stub
        step gw_deps wait_gateway_deps
        step check3 "$SECAI_BIN" check
    fi
    stop_stub

    local verdict
    verdict=$(summarize)
    echo "ghcr-vm-test: verdict=${verdict:-lỗi} (kết quả: $R)"   # trước SHA256SUMS: console.log (nếu có) không đổi sau đó
    sed -i -E 's/([A-Za-z0-9_]*(TOKEN|KEY|SECRET|PASSWORD)[A-Za-z0-9_]*)=[^[:space:]]+/\1=<redacted>/g' "$R"/*.log
    (cd "$R" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + >SHA256SUMS)
    [ "$verdict" = pass ]
}

main
exit $?
