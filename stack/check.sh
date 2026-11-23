#!/usr/bin/env bash
# check.sh env|config|models|health|all - kiểm gói stack trước và sau `docker compose up`.
#   env     chạy `bootstrap.sh preflight` (một điểm vào cho người vận hành).
#   config  `docker compose config` cho: không profile, từng profile lẻ, cả bốn; cấm container_name, docker.sock,
#           host.docker.internal, :latest; /home/ict cấm trong NGUỒN compose.yml (và image/command đã render).
#           network_mode chỉ được là service:translation-gateway và chỉ ở specialist-tunnel/translation-worker
#           (chung netns để thấy đầu tunnel 127.0.0.1:18868/18083); host/none/container:/service khác đều cấm.
#           Biến `:?` thiếu thì dùng giá trị giả; giá trị thật trong .env luôn thắng giá trị giả. Không in giá trị render.
#   models  LLM: GET $QWEN_BASE_URL/models. Embedding/rerank: gateway KHÔNG liệt kê model, nên probe y như gateway
#           (retrieval/embedding.py): POST $EMBEDDING_API_URL/v1/embeddings, POST $RERANKER_API_URL/v1/rerank, Bearer key.
#           404/model-not-found = tên model sai (RAG rơi về BM25-only dù health xanh).
#   health  GET $GATEWAY_URL/health (khối serving/databases do health_deep_checks) + MỘT truy vấn RAG thật qua
#           POST /sessions + /sessions/{sid}/chat/stream (UI-CHAT-1) + `docker compose ps` các service của profile bật.
#           Profile translation và TUNNEL_MODE != off: từ TRONG netns translation-gateway và translation-worker
#           (`compose exec`), nối 127.0.0.1:18868 (và 18083 nếu URL OCR là loopback) và đòi một dòng trạng thái HTTP:
#           chứng minh tunnel chuyển tới đầu xa, không chỉ là cổng nghe cục bộ.
#   all     env -> config -> models -> health, dừng ở giai đoạn đầu tiên trượt.
# Mọi bộ phân tích python (py_run) kết thúc bằng dòng __OK__; thiếu dấu, rc != 0 hoặc traceback = "parser error" = FAIL
# của đúng kiểm tra đó, không bao giờ là pass (đầu ra rỗng KHÔNG có nghĩa là không có lỗi).
# Biến: STACK_DIR (thư mục stack, test), STACK_ENV (đường dẫn .env, mặc định $STACK_DIR/.env), GATEWAY_URL,
#       CHECK_CHAT_TIMEOUT (giây, mặc định 120). Biến môi trường thắng .env. Khoá API đi qua `curl -K -` (stdin),
#       không bao giờ lên argv/ps và không bao giờ được in. URL trong thông báo đã bỏ user:pass@ và query.
set -euo pipefail

STACK_DIR="$(cd "${STACK_DIR:-$(dirname "${BASH_SOURCE[0]}")}" && pwd)"
export STACK_DIR
ENV_FILE="${STACK_ENV:-$STACK_DIR/.env}"
COMPOSE_FILE="$STACK_DIR/compose.yml"
FAILS=0

fail() { echo "FAIL: $*" >&2; FAILS=$((FAILS + 1)); }
pass() { echo "ok: $*"; }
usage() { echo "usage: check.sh env|config|models|health|all" >&2; exit 2; }
require_tools() { local t; for t in "$@"; do command -v "$t" >/dev/null 2>&1 || { echo "check.sh: thiếu lệnh '$t' trong PATH; cài rồi chạy lại" >&2; exit 1; }; done; }

# URL an toàn để in: bỏ user:pass@ và ?query/#fragment.
safe_url() { local u="$1"; u="${u%%\?*}"; u="${u%%\#*}"; printf '%s' "$u" | sed -E 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@#\1#'; }

# Giá trị cuối cùng của KEY trong .env (cùng cách bootstrap.sh: CRLF-safe, không source).
val() {
    local v
    v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r')" || true
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    printf '%s' "$v"
}
get() { local v="${!1:-}"; [ -n "$v" ] || v="$(val "$1")"; printf '%s' "$v"; }

profiles() { get COMPOSE_PROFILES | tr ',' '\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | grep -v '^$' || true; }

# Biến `:?` trong compose.yml (GATEWAY_IMAGE, DATA_ROOT, NEO4J_PASSWORD...): giá trị thật nếu có, không thì giả.
# Xuất cả giá trị thật vào môi trường để một biến shell rỗng không che giá trị trong .env.
export_compose_vars() {
    local k v
    for k in $(grep -oE '\$\{[A-Za-z_0-9]+:\?' "$COMPOSE_FILE" | sed -E 's/^\$\{//; s/:\?$//' | sort -u); do
        v="$(get "$k")"
        if [ -z "$v" ]; then
            case "$k" in
                *IMAGE) v="dummy/image:check-0000" ;;
                *ROOT|*DIR|*PATH) v="/nonexistent/check-dummy" ;;
                *) v="check-dummy-value" ;;
            esac
        fi
        export "$k=$v"
    done
}

cq() { local s="$1"; s="${s//\\/\\\\}"; printf '%s' "${s//\"/\\\"}"; }   # escape cho giá trị trong tệp cấu hình curl

# http METHOD URL TIMEOUT [KEY] [JSON_BODY] -> HTTP_CODE (rỗng nếu curl không in mã), HTTP_BODY, HTTP_RC (mã thoát curl; 28 = quá thời gian).
# Mọi tham số (kể cả Authorization) đi qua stdin của `curl -K -` nên không lên argv/ps.
HTTP_CODE=""; HTTP_BODY=""; HTTP_RC=0
http() {
    local method="$1" url="$2" tmo="$3" key="${4:-}" body="${5:-}" out
    HTTP_RC=0
    out="$({
        printf 'silent\nconnect-timeout = 5\nmax-time = %s\nrequest = "%s"\nurl = "%s"\n' "$tmo" "$method" "$(cq "$url")"
        printf 'write-out = "\\n%%{http_code}"\n'
        [ -z "$key" ] || printf 'header = "Authorization: Bearer %s"\n' "$(cq "$key")"
        [ -z "$body" ] || printf 'header = "Content-Type: application/json"\ndata = "%s"\n' "$(cq "$body")"
    } | curl -K - 2>/dev/null)" || HTTP_RC=$?
    HTTP_CODE="${out##*$'\n'}"
    if [[ "$HTTP_CODE" =~ ^[0-9]{3}$ ]]; then HTTP_BODY="${out%$'\n'*}"; else HTTP_CODE=""; HTTP_BODY="$out"; fi
}
is2xx() { [[ "$HTTP_CODE" =~ ^2[0-9]{2}$ ]]; }
timed_out() { [ "$HTTP_RC" = 28 ]; }

# py_run CHECK CODE: chạy CODE (python3 -I) với stdin=$PY_IN. CODE phải in __OK__ làm dòng cuối cùng.
# Thành công -> PY_OUT = các dòng đầu ra trước dấu, trả 0. Lỗi (rc != 0, thiếu dấu, traceback) -> một dòng FAIL, trả 1.
PY_IN=""; PY_OUT=""
py_run() {
    local check="$1" code="$2" ef out rc=0 last reason
    ef="$(mktemp)"
    out="$(python3 -I -c "$code" 2>"$ef" <<<"$PY_IN")" || rc=$?
    last="${out##*$'\n'}"
    if [ "$rc" -ne 0 ] || [ "$last" != "__OK__" ] || grep -q Traceback "$ef"; then
        reason="$(grep -v '^[[:space:]]*$' "$ef" | tail -n1 | cut -c1-160 || true)"
        rm -f "$ef"; PY_OUT=""
        fail "$check: parser error: ${reason:-python rc=$rc, không có dấu __OK__}"
        return 1
    fi
    rm -f "$ef"
    PY_OUT="${out%__OK__}"; PY_OUT="${PY_OUT%$'\n'}"
}

summary() {
    local what="$1"
    if [ "$FAILS" -gt 0 ]; then echo "$what: $FAILS lỗi" >&2; exit 1; fi
    echo "$what: OK"
}

# ───────────────────────────── env ─────────────────────────────
cmd_env() { exec bash "$STACK_DIR/bootstrap.sh" preflight; }

# ───────────────────────────── config ──────────────────────────
# Các chuỗi cấm. /home/ict chỉ xét ở NGUỒN compose.yml và image/command đã render: nguồn bind mount render ra đường dẫn
# tuyệt đối theo vị trí worktree (có thể chứa tên người dùng hợp lệ), và env_file đã bung cả .env (DATA_ROOT...) vào environment.
FORBIDDEN=(docker.sock host.docker.internal container_name ':latest')
NETNS_OK_LINE='[[:space:]]*network_mode: service:translation-gateway[[:space:]]*$'   # dạng duy nhất được phép trong nguồn

SCAN_PY='
import json, sys
cfg = json.load(sys.stdin)
if not isinstance(cfg, dict): raise ValueError("config render không phải JSON object")
svcs = cfg.get("services")
if not isinstance(svcs, dict) or not svcs: raise ValueError("config render không có services")
PAT = ["docker.sock", "host.docker.internal", "container_name", ":latest"]
NETNS = {"specialist-tunnel", "translation-worker"}   # chung netns với translation-gateway (đầu tunnel loopback)
def hit(txt, pats, where):
    for p in pats:
        if p in txt: print(p + "\t" + where)
for name, svc in svcs.items():
    if not isinstance(svc, dict): raise ValueError("service %s không phải object" % name)
    hit(json.dumps(svc), PAT, name)
    nm = svc.get("network_mode")
    if nm is not None and not (name in NETNS and nm == "service:translation-gateway"):
        print("network_mode=%s\t%s" % (nm, name))
    hit(json.dumps([svc.get("image"), svc.get("command"), svc.get("entrypoint")]), ["/home/ict"], name)
hit(json.dumps({k: v for k, v in cfg.items() if k != "services"}), PAT, "(top-level)")
print("__OK__")
'

cmd_config() {
    command -v docker >/dev/null 2>&1 || { fail "config: không có lệnh docker"; summary config; }
    [ -f "$COMPOSE_FILE" ] || { fail "config: thiếu $COMPOSE_FILE"; summary config; }
    local p n
    for p in "${FORBIDDEN[@]}" /home/ict; do
        while IFS=: read -r n _; do
            [ -z "$n" ] || fail "config: '$p' có trong nguồn compose.yml (dòng $n)"
        done < <(grep -nF -- "$p" "$COMPOSE_FILE" || true)
    done
    while IFS=: read -r n _; do
        [ -z "$n" ] || fail "config: 'network_mode' khác 'service:translation-gateway' trong nguồn compose.yml (dòng $n)"
    done < <(grep -n -- network_mode "$COMPOSE_FILE" | grep -vE "^[0-9]+:$NETNS_OK_LINE" || true)
    export_compose_vars
    export COMPOSE_PROFILES=""   # profile chỉ đến từ cờ --profile; COMPOSE_PROFILES trong .env không được làm lệch "không profile"
    local combos=("" exploit translation report review "exploit translation report review") c label args out err rc pat where line
    for c in "${combos[@]}"; do
        args=(); label="không profile"
        if [ -n "$c" ]; then label="profile: ${c// /,}"; for p in $c; do args+=(--profile "$p"); done; fi
        err="$(mktemp)"; rc=0
        out="$(docker compose -f "$COMPOSE_FILE" "${args[@]}" config --format json 2>"$err")" || rc=$?
        if [ "$rc" -ne 0 ]; then
            fail "config [$label]: docker compose config lỗi: $(head -n 3 "$err" | tr '\n' ' ')"; rm -f "$err"; continue
        fi
        rm -f "$err"
        PY_IN="$out"
        py_run "config [$label]" "$SCAN_PY" || continue
        n=0
        while IFS=$'\t' read -r pat where; do
            [ -n "$pat" ] || continue
            fail "config [$label]: '$pat' trong config đã render (service $where)"; n=1
        done <<<"$PY_OUT"
        [ "$n" -ne 0 ] || pass "config [$label]"
    done
    summary config
}

# ───────────────────────────── models ──────────────────────────
need_vars() {   # need_vars TÊN... : mỗi biến trống một dòng FAIL; trả 1 nếu thiếu
    local k miss=0
    for k in "$@"; do [ -n "$(get "$k")" ] || { fail "models: $k chưa đặt (trong .env hoặc môi trường)${EXTRA_HINT:+; $EXTRA_HINT}"; miss=1; }; done
    return $miss
}

model_mismatch() { [[ "$HTTP_CODE" == 404 || "$HTTP_BODY" =~ [Nn]ot[[:space:]_-]?[Ff]ound|does[[:space:]]not[[:space:]]exist|[Uu]nknown[[:space:]]model|[Ii]nvalid[[:space:]]model ]]; }

# probe_post ROLE URLVAR PATH KEYVAR MODELVAR BODY_PY VALIDATOR_PY (validator in "ok" hoặc rỗng rồi __OK__)
probe_post() {
    local role="$1" urlvar="$2" path="$3" keyvar="$4" modvar="$5" bodypy="$6" okpy="$7" url key model body shown v=""
    EXTRA_HINT="cần để kiểm $modvar=$(get "$modvar")"
    need_vars "$urlvar" "$keyvar" "$modvar" || { EXTRA_HINT=""; return 0; }
    EXTRA_HINT=""
    url="$(get "$urlvar")"; url="${url%/}"; key="$(get "$keyvar")"; model="$(get "$modvar")"; shown="$(safe_url "$url")"
    body="$(M="$model" python3 -I -c "$bodypy")"
    http POST "$url$path" 15 "$key" "$body"
    if timed_out || [ "$HTTP_CODE" = "000" ]; then fail "models: $urlvar ($shown) không kết nối được/quá thời gian 15 s; không kiểm được $modvar='$model'"; return 0; fi
    if is2xx; then PY_IN="$HTTP_BODY"; py_run "models: $role ($urlvar)" "$okpy" || return 0; v="${PY_OUT%%$'\n'*}"; fi
    if [ "$v" = ok ]; then pass "models: $role '$model' phục vụ được ($urlvar)"
    elif [ "$HTTP_CODE" = 401 ] || [ "$HTTP_CODE" = 403 ]; then fail "models: $keyvar bị từ chối (HTTP $HTTP_CODE) tại $urlvar; không kiểm được $modvar='$model'"
    elif model_mismatch; then fail "models: $modvar: model '$model' không có ở serving ($urlvar) (HTTP ${HTTP_CODE:-?}); tên phải khớp serving, không dùng model stock (RAG sẽ rơi về BM25-only)"
    else fail "models: $modvar='$model': $urlvar$path trả HTTP ${HTTP_CODE:-?} không có kết quả $role hợp lệ (kiểm tên model và serving)"; fi
}

cmd_models() {
    require_tools python3 curl
    local url key model served shown
    # LLM: gateway gọi GET {base}/models với Bearer (api/routes.py /health).
    if need_vars QWEN_BASE_URL QWEN_API_KEY QWEN_MODEL; then
        url="$(get QWEN_BASE_URL)"; url="${url%/}"; key="$(get QWEN_API_KEY)"; model="$(get QWEN_MODEL)"; shown="$(safe_url "$url")"
        http GET "$url/models" 15 "$key"
        if timed_out || [ "$HTTP_CODE" = "000" ]; then fail "models: QWEN_BASE_URL ($shown) không kết nối được/quá thời gian 15 s"
        elif [ "$HTTP_CODE" = 401 ] || [ "$HTTP_CODE" = 403 ]; then fail "models: QWEN_API_KEY bị từ chối (HTTP $HTTP_CODE) tại QWEN_BASE_URL"
        elif ! is2xx; then fail "models: GET QWEN_BASE_URL/models trả HTTP ${HTTP_CODE:-?}"
        else
            PY_IN="$HTTP_BODY"
            if py_run "models: LLM (QWEN_BASE_URL)" 'import json,sys
j = json.load(sys.stdin)
if not isinstance(j, dict) or not isinstance(j.get("data"), list): raise ValueError("thiếu mảng data")
print(",".join(str(m.get("id")) for m in j["data"] if isinstance(m, dict)))
print("__OK__")'; then
                served="$PY_OUT"
                if [[ ",$served," == *",$model,"* ]]; then pass "models: LLM '$model' có trong danh sách serving"
                else fail "models: QWEN_MODEL: mong đợi '$model' nhưng serving công bố: ${served:-<danh sách rỗng>}"; fi
            fi
        fi
    fi
    # Embedding/rerank: cùng đường gọi và Bearer như retrieval/embedding.py (không có endpoint liệt kê model).
    probe_post embedding EMBEDDING_API_URL /v1/embeddings EMBEDDING_API_KEY EMBEDDING_MODEL \
        'import json,os;print(json.dumps({"model":os.environ["M"],"input":"healthcheck"}))' \
        'import json,sys
j = json.load(sys.stdin)
d = j.get("data") if isinstance(j, dict) else None
e = d[0].get("embedding") if isinstance(d, list) and d and isinstance(d[0], dict) else None
print("ok" if isinstance(e, list) and e else "")
print("__OK__")'
    probe_post rerank RERANKER_API_URL /v1/rerank RERANKER_API_KEY RERANKER_MODEL \
        'import json,os;print(json.dumps({"model":os.environ["M"],"query":"healthcheck","documents":["healthcheck"],"top_n":1}))' \
        'import json,sys
j = json.load(sys.stdin)
r = j.get("results") if isinstance(j, dict) else None
print("ok" if isinstance(r, list) and r else "")
print("__OK__")'
    summary models
}

# ───────────────────────────── health ──────────────────────────
declare -A PROFILE_SERVICES=([exploit]="vm-a vm-b" [translation]="translation-gateway translation-worker translation-converter" [report]="report-backend" [review]="review-frontend")

# Chạy trong container (python của image gateway): nối 127.0.0.1:<cổng>, gửi GET /health, đòi dòng trạng thái "HTTP/".
# Bất kỳ mã HTTP nào (kể cả 401/404) đều chứng minh đầu xa trả lời; ssh -L nhận kết nối rồi đóng khi đầu xa chết.
# Đọc trọn dòng trạng thái (readline: chịu được dòng bị chia nhiều gói; hết 10 s thì lỗi) và đòi dạng "HTTP/x.y NNN".
TUNNEL_PROBE_PY='import re,socket,sys; s=socket.create_connection(("127.0.0.1",int(sys.argv[1])),5); s.settimeout(10); s.sendall(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"); sys.exit(0 if re.match(rb"HTTP/[0-9][.][0-9] [0-9]{3}[ \r\n]", s.makefile("rb").readline(256)) else 1)'

# is_loopback_url URL: http(s)://127.0.0.1|localhost|[::1]...
is_loopback_url() { [[ "$1" =~ ^https?://(127\.[0-9.]+|localhost|\[::1\])(:|/|$) ]]; }

check_tunnel() {   # check_tunnel PROJECT
    local proj="$1" svc port ports="18868" k v
    if [ "$(get TUNNEL_MODE)" = direct ]; then
        echo "WARNING: health: TUNNEL_MODE=direct CHỈ ĐỂ TEST: dịch/OCR đi tới stub qua TCP thô, KHÔNG phải model thật; kết quả dịch không có giá trị chất lượng" >&2
    fi
    # Đầu tunnel cố định: đúng chuỗi, không localhost/[::1]/https.
    for k in TRANSLATE_V2_SPECIALIST_BASE_URL MULTILANG_SPECIALIST_BASE_URL; do
        v="$(get "$k")"
        [ -z "$v" ] || [ "$v" = http://127.0.0.1:18868 ] || fail "health: $k phải đúng http://127.0.0.1:18868 khi bật tunnel (đang khác)"
    done
    v="$(get TRANSLATE_V2_OCR_BASE_URL)"
    if [ "$v" = http://127.0.0.1:18083 ]; then ports+=" 18083"
    elif is_loopback_url "$v"; then fail "health: TRANSLATE_V2_OCR_BASE_URL loopback phải đúng http://127.0.0.1:18083 khi bật tunnel"; fi
    for svc in translation-gateway translation-worker; do
        for port in $ports; do
            if docker compose -p "$proj" -f "$COMPOSE_FILE" exec -T "$svc" python -c "$TUNNEL_PROBE_PY" "$port" >/dev/null 2>&1; then
                pass "health: tunnel 127.0.0.1:$port trả HTTP từ trong netns $svc"
            else
                fail "health: tunnel 127.0.0.1:$port không chuyển tới dịch vụ đầu xa từ trong netns $svc (TUNNEL_MODE=$(get TUNNEL_MODE); xem 'docker compose logs specialist-tunnel'; translation-gateway khởi động lại thì 'docker compose up -d')"
            fi
        done
    done
}

HEALTH_PY='
import json, sys
h = json.load(sys.stdin)
if not isinstance(h, dict): raise ValueError("/health không phải JSON object")
def row(x): return x if isinstance(x, dict) else {}
if not (h.get("llm") or h.get("qwen")): print("LLM không phản hồi /models (llm=false)")
if h.get("redis") == "unreachable": print("redis unreachable")
s, d = h.get("serving"), h.get("databases")
if not isinstance(s, dict) or not isinstance(d, dict) or not s or not d:
    print("/health không có khối serving/databases (HEALTH_DEEP_CHECKS tắt?): không xác minh được embedding/rerank/Qdrant; RAG có thể rơi về BM25-only mà không ai biết")
else:
    for role in ("embedding", "reranker"):
        r = row(s.get(role))
        if not r.get("ok"): print("%s lỗi (%s): RAG rơi về BM25-only/không rerank" % (role, r.get("detail") or "không ok"))
    q = row(d.get("qdrant"))
    if not q.get("ok"): print("qdrant lỗi (%s): không có hit Qdrant" % (q.get("detail") or "không ok"))
    elif not q.get("points"): print("qdrant: collection rỗng (points=%s): không có hit Qdrant, nạp dữ liệu/restore" % q.get("points"))
    n4 = row(d.get("neo4j"))
    if not n4.get("ok"): print("neo4j lỗi (%s)" % (n4.get("detail") or "không ok"))
print("__OK__")
'

SSE_PY='
import json, re, sys
ev, final, toks, errs = None, None, [], False
for raw in sys.stdin.read().splitlines():
    if raw.startswith("event:"): ev = raw[6:].strip()
    elif raw.startswith("data:"):
        d = raw[5:].strip()
        if d == "[DONE]": continue
        try: j = json.loads(d)
        except ValueError: continue
        if not isinstance(j, dict): continue   # khung không phải object không mang văn bản; thiếu final thì dấu hiệu bên dưới trượt
        if ev == "token":
            if isinstance(j.get("delta"), str): toks.append(j["delta"])
        elif ev == "error": errs = True
        elif ev == "final":
            for k in ("text", "response", "answer", "content", "message"):
                if isinstance(j.get(k), str) and j[k]: final = j[k]; break
text = final or "".join(toks)
if errs: print("luồng SSE có sự kiện error")
for label, pat in (("Log4Shell|Log4j", r"Log4Shell|Log4j"), ("10.0 (CVSS)", r"10\.0"), ("2.16.0|2.15.0 (bản vá)", r"2\.16\.0|2\.15\.0")):
    if not re.search(pat, text, re.I): print("thiếu dấu hiệu " + label)
for bad in ("ProviderError", "Connection error"):
    if bad.lower() in text.lower(): print("câu trả lời chứa " + bad)
if len(text) < 500: print("câu trả lời ngắn %d < 500 ký tự" % len(text))
print("__OK__")
'

PS_PY='
import json, os, sys
txt = sys.stdin.read().strip()
rows = json.loads(txt) if txt.startswith("[") else [json.loads(l) for l in txt.splitlines() if l.strip()]
if not all(isinstance(r, dict) for r in rows): raise ValueError("docker compose ps: phần tử không phải object")
by = {r.get("Service"): r for r in rows}
for s in os.environ["W"].split():
    r = by.get(s)
    if not r: print("service %s chưa chạy (không có container)" % s)
    elif r.get("State") != "running": print("service %s State=%s" % (s, r.get("State")))
    elif r.get("Health") not in ("", None, "healthy"): print("service %s Health=%s" % (s, r.get("Health")))
print("__OK__")
'

cmd_health() {
    require_tools python3 curl
    local gw line n sid user="check-$$" msg="CVE-2021-44228 là lỗ hổng gì? Nêu sản phẩm bị ảnh hưởng, điểm CVSS và cách khắc phục."
    gw="${GATEWAY_URL:-}"
    if [ -z "$gw" ]; then gw="$(get GATEWAY_HOST_PORT)"; gw="http://127.0.0.1:${gw:-8131}"; fi
    gw="${gw%/}"
    local shown; shown="$(safe_url "$gw")"
    # 1) /health. Gateway KHÔNG báo chế độ truy hồi trong câu trả lời; tín hiệu BM25-only duy nhất tin cậy là các probe
    #    sâu của /health (embedding/rerank/qdrant). `ready`/`problems` KHÔNG dùng: kb_sync cron luôn đỏ trên stack mới.
    http GET "$gw/health" 10
    if timed_out; then fail "health: GET $shown/health quá thời gian 10 s"
    elif [ "$HTTP_CODE" != 200 ]; then fail "health: GET $shown/health trả HTTP ${HTTP_CODE:-?}"
    else
        PY_IN="$HTTP_BODY"
        if py_run "health: /health" "$HEALTH_PY"; then
            n=0
            while IFS= read -r line; do [ -n "$line" ] || continue; fail "health: $line"; n=1; done <<<"$PY_OUT"
            [ "$n" -ne 0 ] || pass "health: /health (llm, embedding, rerank, qdrant, neo4j)"
        fi
    fi
    # 2) Một truy vấn RAG thật: đúng đường UI-CHAT-1 (POST /sessions, POST /sessions/{sid}/chat/stream, SSE final).
    http POST "$gw/sessions" 15 "" "$(U="$user" python3 -I -c 'import json,os;print(json.dumps({"user_id":os.environ["U"],"title":"check.sh"}))')"
    sid=""
    if timed_out; then fail "health: POST $shown/sessions quá thời gian 15 s"
    elif [ "$HTTP_CODE" != 200 ]; then fail "health: POST $shown/sessions trả HTTP ${HTTP_CODE:-?} (không tạo được phiên)"
    else
        PY_IN="$HTTP_BODY"
        if py_run "health: POST /sessions" 'import json,sys
j = json.load(sys.stdin)
if not isinstance(j, dict): raise ValueError("phản hồi không phải JSON object")
print(j.get("session_id") if isinstance(j.get("session_id"), str) else "")
print("__OK__")'; then
            sid="$PY_OUT"
            [ -n "$sid" ] || fail "health: POST $shown/sessions không trả session_id"
        fi
    fi
    if [ -n "$sid" ]; then
        local tmo="${CHECK_CHAT_TIMEOUT:-120}"
        http POST "$gw/sessions/$(S="$sid" python3 -I -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["S"],safe=""))')/chat/stream" \
            "$tmo" "" "$(U="$user" M="$msg" python3 -I -c 'import json,os;print(json.dumps({"user_id":os.environ["U"],"message":os.environ["M"]}))')"
        if timed_out; then fail "health: RAG chat/stream quá thời gian ${tmo} s (curl 28), luồng SSE chưa xong; không đánh giá dấu hiệu"
        elif [ "$HTTP_CODE" != 200 ]; then fail "health: RAG chat/stream trả HTTP ${HTTP_CODE:-?} (000 = không kết nối được)"
        else
            PY_IN="$HTTP_BODY"
            if py_run "health: RAG UI-CHAT-1" "$SSE_PY"; then
                n=0
                while IFS= read -r line; do [ -n "$line" ] || continue; fail "health: RAG UI-CHAT-1: $line"; n=1; done <<<"$PY_OUT"
                [ "$n" -ne 0 ] || pass "health: RAG CVE-2021-44228 trả lời đủ dấu hiệu"
            fi
        fi
    fi
    # 3) Service của profile đang bật (chỉ `ps`, chỉ đọc).
    if ! command -v docker >/dev/null 2>&1; then fail "health: không có lệnh docker để kiểm service"
    else
        local want="gateway qdrant neo4j redis es" p proj ps_out
        local tunnel=0
        while IFS= read -r p; do
            want+=" ${PROFILE_SERVICES[$p]:-}"
            if [ "$p" = translation ] && [ "$(get TUNNEL_MODE)" != off ]; then want+=" specialist-tunnel"; tunnel=1; fi
        done < <(profiles)
        proj="$(get COMPOSE_PROJECT_NAME)"; proj="${proj:-secai-stack}"
        export_compose_vars
        if ! ps_out="$(docker compose -p "$proj" -f "$COMPOSE_FILE" ps -a --format json 2>/dev/null)"; then
            fail "health: 'docker compose -p $proj ps' lỗi"
        else
            PY_IN="$ps_out"
            if W="$want" py_run "health: docker compose ps" "$PS_PY"; then
                n=0
                while IFS= read -r line; do [ -n "$line" ] || continue; fail "health: $line"; n=1; done <<<"$PY_OUT"
                [ "$n" -ne 0 ] || pass "health: service chạy tốt ($want)"
            fi
        fi
        [ "$tunnel" -eq 0 ] || check_tunnel "$proj"
    fi
    summary health
}

# ───────────────────────────── all ─────────────────────────────
cmd_all() {
    local s
    require_tools python3 curl
    for s in env config models health; do
        echo "== $s =="
        bash "${BASH_SOURCE[0]}" "$s" || { echo "all: giai đoạn '$s' trượt; dừng, không chạy các giai đoạn sau" >&2; exit 1; }
    done
    echo "all: OK"
}

case "${1:-}" in
    env) require_tools python3; cmd_env ;;
    config) require_tools python3; cmd_config ;;
    models) cmd_models ;;
    health) cmd_health ;;
    all) cmd_all ;;
    *) usage ;;
esac
