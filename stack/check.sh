#!/usr/bin/env bash
# check.sh env|config|models|health|all - kiểm gói stack trước và sau `docker compose up`.
#   env     chạy `bootstrap.sh preflight` (một điểm vào cho người vận hành).
#   config  `docker compose config` cho: không profile, từng profile lẻ, cả bốn; cấm container_name, network_mode,
#           docker.sock, host.docker.internal, :latest; /home/ict cấm trong NGUỒN compose.yml (và image/command đã render).
#           Biến `:?` thiếu thì dùng giá trị giả; giá trị thật trong .env luôn thắng giá trị giả. Không in giá trị render.
#   models  LLM: GET $QWEN_BASE_URL/models. Embedding/rerank: gateway KHÔNG liệt kê model, nên probe y như gateway
#           (retrieval/embedding.py): POST $EMBEDDING_API_URL/v1/embeddings, POST $RERANKER_API_URL/v1/rerank, Bearer key.
#           404/model-not-found = tên model sai (RAG rơi về BM25-only dù health xanh).
#   health  GET $GATEWAY_URL/health (khối serving/databases do health_deep_checks) + MỘT truy vấn RAG thật qua
#           POST /sessions + /sessions/{sid}/chat/stream (UI-CHAT-1) + `docker compose ps` các service của profile bật.
#   all     env -> config -> models -> health, dừng ở giai đoạn đầu tiên trượt.
# Biến: STACK_DIR (thư mục stack, test), STACK_ENV (đường dẫn .env, mặc định $STACK_DIR/.env), GATEWAY_URL,
#       CHECK_CHAT_TIMEOUT (giây, mặc định 120). Biến môi trường thắng .env. Khoá API đi qua `curl -K -` (stdin),
#       không bao giờ lên argv/ps và không bao giờ được in.
set -euo pipefail

STACK_DIR="$(cd "${STACK_DIR:-$(dirname "${BASH_SOURCE[0]}")}" && pwd)"
export STACK_DIR
ENV_FILE="${STACK_ENV:-$STACK_DIR/.env}"
COMPOSE_FILE="$STACK_DIR/compose.yml"
FAILS=0

fail() { echo "FAIL: $*" >&2; FAILS=$((FAILS + 1)); }
pass() { echo "ok: $*"; }
usage() { echo "usage: check.sh env|config|models|health|all" >&2; exit 2; }

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

# http METHOD URL TIMEOUT [KEY] [JSON_BODY] -> HTTP_CODE (rỗng nếu curl không in mã) và HTTP_BODY.
# Mọi tham số (kể cả Authorization) đi qua stdin của `curl -K -` nên không lên argv/ps.
HTTP_CODE=""; HTTP_BODY=""
http() {
    local method="$1" url="$2" tmo="$3" key="${4:-}" body="${5:-}" out
    out="$({
        printf 'silent\nconnect-timeout = 5\nmax-time = %s\nrequest = "%s"\nurl = "%s"\n' "$tmo" "$method" "$(cq "$url")"
        printf 'write-out = "\\n%%{http_code}"\n'
        [ -z "$key" ] || printf 'header = "Authorization: Bearer %s"\n' "$(cq "$key")"
        [ -z "$body" ] || printf 'header = "Content-Type: application/json"\ndata = "%s"\n' "$(cq "$body")"
    } | curl -K - 2>/dev/null)" || true
    HTTP_CODE="${out##*$'\n'}"
    if [[ "$HTTP_CODE" =~ ^[0-9]{3}$ ]]; then HTTP_BODY="${out%$'\n'*}"; else HTTP_CODE=""; HTTP_BODY="$out"; fi
}
is2xx() { [[ "$HTTP_CODE" =~ ^2[0-9]{2}$ ]]; }
jq_py() { python3 -I -c "$1" 2>/dev/null <<<"$HTTP_BODY" || true; }   # đọc HTTP_BODY từ stdin

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
FORBIDDEN=(docker.sock network_mode host.docker.internal container_name ':latest')

scan_rendered() {   # stdin: JSON của `docker compose config`; in "mẫu<TAB>service" mỗi lần trúng
    python3 -I -c '
import json, sys
cfg = json.load(sys.stdin)
PAT = ["docker.sock", "network_mode", "host.docker.internal", "container_name", ":latest"]
def hit(txt, pats, where):
    for p in pats:
        if p in txt: print(p + "\t" + where)
for name, svc in (cfg.get("services") or {}).items():
    hit(json.dumps(svc), PAT, name)
    hit(json.dumps([svc.get("image"), svc.get("command"), svc.get("entrypoint")]), ["/home/ict"], name)
hit(json.dumps({k: v for k, v in cfg.items() if k != "services"}), PAT, "(top-level)")
'
}

cmd_config() {
    command -v docker >/dev/null 2>&1 || { fail "config: không có lệnh docker"; summary config; }
    [ -f "$COMPOSE_FILE" ] || { fail "config: thiếu $COMPOSE_FILE"; summary config; }
    local p n
    for p in "${FORBIDDEN[@]}" /home/ict; do
        while IFS=: read -r n _; do
            [ -z "$n" ] || fail "config: '$p' có trong nguồn compose.yml (dòng $n)"
        done < <(grep -nF -- "$p" "$COMPOSE_FILE" || true)
    done
    export_compose_vars
    export COMPOSE_PROFILES=""   # profile chỉ đến từ cờ --profile; COMPOSE_PROFILES trong .env không được làm lệch "không profile"
    local combos=("" exploit translation report review "exploit translation report review") c label args out err rc pat where
    for c in "${combos[@]}"; do
        args=(); label="không profile"
        if [ -n "$c" ]; then label="profile: ${c// /,}"; for p in $c; do args+=(--profile "$p"); done; fi
        err="$(mktemp)"; rc=0
        out="$(docker compose -f "$COMPOSE_FILE" "${args[@]}" config --format json 2>"$err")" || rc=$?
        if [ "$rc" -ne 0 ]; then
            fail "config [$label]: docker compose config lỗi: $(head -n 3 "$err" | tr '\n' ' ')"; rm -f "$err"; continue
        fi
        rm -f "$err"
        n=0
        while IFS=$'\t' read -r pat where; do
            [ -n "$pat" ] || continue
            fail "config [$label]: '$pat' trong config đã render (service $where)"; n=1
        done < <(scan_rendered <<<"$out")
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

# probe_post ROLE URLVAR PATH KEYVAR MODELVAR JSON_BODY_TEMPLATE_PY VALIDATOR_PY
probe_post() {
    local role="$1" urlvar="$2" path="$3" keyvar="$4" modvar="$5" bodypy="$6" okpy="$7" url key model body
    EXTRA_HINT="cần để kiểm $modvar=$(get "$modvar")"
    need_vars "$urlvar" "$keyvar" "$modvar" || { EXTRA_HINT=""; return; }
    EXTRA_HINT=""
    url="$(get "$urlvar")"; url="${url%/}"; key="$(get "$keyvar")"; model="$(get "$modvar")"
    body="$(M="$model" python3 -I -c "$bodypy")"
    http POST "$url$path" 15 "$key" "$body"
    if is2xx && [ "$(jq_py "$okpy")" = "ok" ]; then pass "models: $role '$model' phục vụ được ($urlvar)"
    elif [ "$HTTP_CODE" = "000" ]; then fail "models: $urlvar ($url) không kết nối được/quá 15 s; không kiểm được $modvar='$model'"
    elif [ "$HTTP_CODE" = 401 ] || [ "$HTTP_CODE" = 403 ]; then fail "models: $keyvar bị từ chối (HTTP $HTTP_CODE) tại $urlvar; không kiểm được $modvar='$model'"
    elif model_mismatch; then fail "models: $modvar: model '$model' không có ở serving ($urlvar) (HTTP ${HTTP_CODE:-?}); tên phải khớp serving, không dùng model stock (RAG sẽ rơi về BM25-only)"
    else fail "models: $modvar='$model': $urlvar$path trả HTTP ${HTTP_CODE:-?} không có kết quả $role hợp lệ (kiểm tên model và serving)"; fi
}

cmd_models() {
    local url key model served
    # LLM: gateway gọi GET {base}/models với Bearer (api/routes.py /health).
    if need_vars QWEN_BASE_URL QWEN_API_KEY QWEN_MODEL; then
        url="$(get QWEN_BASE_URL)"; url="${url%/}"; key="$(get QWEN_API_KEY)"; model="$(get QWEN_MODEL)"
        http GET "$url/models" 15 "$key"
        if [ "$HTTP_CODE" = "000" ]; then fail "models: QWEN_BASE_URL ($url) không kết nối được/quá 15 s"
        elif [ "$HTTP_CODE" = 401 ] || [ "$HTTP_CODE" = 403 ]; then fail "models: QWEN_API_KEY bị từ chối (HTTP $HTTP_CODE) tại QWEN_BASE_URL"
        elif [ -n "$HTTP_CODE" ] && ! is2xx; then fail "models: GET QWEN_BASE_URL/models trả HTTP $HTTP_CODE"
        else
            served="$(jq_py 'import json,sys
try: print(",".join(str(m.get("id")) for m in json.load(sys.stdin).get("data", [])))
except Exception: print("")')"
            if [[ ",$served," == *",$model,"* ]]; then pass "models: LLM '$model' có trong danh sách serving"
            else fail "models: QWEN_MODEL: mong đợi '$model' nhưng serving công bố: ${served:-<không đọc được danh sách>}"; fi
        fi
    fi
    # Embedding/rerank: cùng đường gọi và Bearer như retrieval/embedding.py (không có endpoint liệt kê model).
    probe_post embedding EMBEDDING_API_URL /v1/embeddings EMBEDDING_API_KEY EMBEDDING_MODEL \
        'import json,os;print(json.dumps({"model":os.environ["M"],"input":"healthcheck"}))' \
        'import json,sys
try: print("ok" if json.load(sys.stdin)["data"][0]["embedding"] else "")
except Exception: print("")'
    probe_post rerank RERANKER_API_URL /v1/rerank RERANKER_API_KEY RERANKER_MODEL \
        'import json,os;print(json.dumps({"model":os.environ["M"],"query":"healthcheck","documents":["healthcheck"],"top_n":1}))' \
        'import json,sys
try: print("ok" if isinstance(json.load(sys.stdin)["results"], list) else "")
except Exception: print("")'
    summary models
}

# ───────────────────────────── health ──────────────────────────
declare -A PROFILE_SERVICES=([exploit]="vm-a vm-b" [translation]="translation-gateway translation-worker translation-converter" [report]="report-backend" [review]="review-frontend")

cmd_health() {
    local gw="${GATEWAY_URL:-http://127.0.0.1:$(get GATEWAY_HOST_PORT)}" line
    [ -n "${GATEWAY_URL:-}" ] || gw="http://127.0.0.1:$(get GATEWAY_HOST_PORT | sed 's/^$/8131/')"
    gw="${gw%/}"
    # 1) /health. Gateway KHÔNG báo chế độ truy hồi trong câu trả lời; tín hiệu BM25-only duy nhất tin cậy là các probe
    #    sâu của /health (embedding/rerank/qdrant). `ready`/`problems` KHÔNG dùng: kb_sync cron luôn đỏ trên stack mới.
    http GET "$gw/health" 10
    if [ "$HTTP_CODE" != 200 ]; then fail "health: GET $gw/health trả HTTP ${HTTP_CODE:-?}"
    else
        local n=0
        while IFS= read -r line; do [ -n "$line" ] || continue; fail "health: $line"; n=1; done < <(jq_py '
import json, sys
try: h = json.load(sys.stdin)
except Exception: print("/health không phải JSON"); sys.exit()
if not (h.get("llm") or h.get("qwen")): print("LLM không phản hồi /models (llm=false)")
if h.get("redis") == "unreachable": print("redis unreachable")
s, d = h.get("serving"), h.get("databases")
if not s or not d:
    print("/health không có khối serving/databases (HEALTH_DEEP_CHECKS tắt?): không xác minh được embedding/rerank/Qdrant; RAG có thể rơi về BM25-only mà không ai biết")
else:
    for role in ("embedding", "reranker"):
        r = s.get(role) or {}
        if not r.get("ok"): print("%s lỗi (%s): RAG rơi về BM25-only/không rerank" % (role, r.get("detail") or "không ok"))
    q = d.get("qdrant") or {}
    if not q.get("ok"): print("qdrant lỗi (%s): không có hit Qdrant" % (q.get("detail") or "không ok"))
    elif not q.get("points"): print("qdrant: collection rỗng (points=%s): không có hit Qdrant, nạp dữ liệu/restore" % q.get("points"))
    if not (d.get("neo4j") or {}).get("ok"): print("neo4j lỗi (%s)" % ((d.get("neo4j") or {}).get("detail") or "không ok"))
')
        [ "$n" -ne 0 ] || pass "health: /health (llm, embedding, rerank, qdrant, neo4j)"
    fi
    # 2) Một truy vấn RAG thật: đúng đường UI-CHAT-1 (POST /sessions, POST /sessions/{sid}/chat/stream, SSE final).
    local user="check-$$" sid msg="CVE-2021-44228 là lỗ hổng gì? Nêu sản phẩm bị ảnh hưởng, điểm CVSS và cách khắc phục."
    http POST "$gw/sessions" 15 "" "$(U="$user" python3 -I -c 'import json,os;print(json.dumps({"user_id":os.environ["U"],"title":"check.sh"}))')"
    sid="$(jq_py 'import json,sys
try: print(json.load(sys.stdin).get("session_id") or "")
except Exception: print("")')"
    if [ "$HTTP_CODE" != 200 ] || [ -z "$sid" ]; then fail "health: POST $gw/sessions trả HTTP ${HTTP_CODE:-?} (không tạo được phiên)"
    else
        http POST "$gw/sessions/$(S="$sid" python3 -I -c 'import os,urllib.parse;print(urllib.parse.quote(os.environ["S"],safe=""))')/chat/stream" \
            "${CHECK_CHAT_TIMEOUT:-120}" "" "$(U="$user" M="$msg" python3 -I -c 'import json,os;print(json.dumps({"user_id":os.environ["U"],"message":os.environ["M"]}))')"
        if [ "$HTTP_CODE" != 200 ]; then fail "health: RAG chat/stream trả HTTP ${HTTP_CODE:-?} (000 = quá thời gian ${CHECK_CHAT_TIMEOUT:-120} s)"
        else
            local n=0
            while IFS= read -r line; do [ -n "$line" ] || continue; fail "health: RAG UI-CHAT-1: $line"; n=1; done < <(jq_py '
import json, re, sys
ev, final, toks, errs = None, None, [], False
for raw in sys.stdin.read().splitlines():
    if raw.startswith("event:"): ev = raw[6:].strip()
    elif raw.startswith("data:"):
        d = raw[5:].strip()
        if d == "[DONE]": continue
        try: j = json.loads(d)
        except ValueError: continue
        if ev == "token": toks.append(j.get("delta", ""))
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
')
            [ "$n" -ne 0 ] || pass "health: RAG CVE-2021-44228 trả lời đủ dấu hiệu"
        fi
    fi
    # 3) Service của profile đang bật (chỉ `ps`, chỉ đọc).
    if ! command -v docker >/dev/null 2>&1; then fail "health: không có lệnh docker để kiểm service"
    else
        local want="gateway qdrant neo4j redis es" p proj ps_out
        while IFS= read -r p; do want+=" ${PROFILE_SERVICES[$p]:-}"; done < <(profiles)
        proj="$(get COMPOSE_PROJECT_NAME)"; proj="${proj:-secai-stack}"
        export_compose_vars
        if ! ps_out="$(docker compose -p "$proj" -f "$COMPOSE_FILE" ps -a --format json 2>/dev/null)"; then
            fail "health: 'docker compose -p $proj ps' lỗi"
        else
            local n=0
            while IFS= read -r line; do [ -n "$line" ] || continue; fail "health: $line"; n=1; done < <(W="$want" python3 -I -c '
import json, os, sys
txt = sys.stdin.read().strip()
rows = []
try: rows = json.loads(txt) if txt.startswith("[") else [json.loads(l) for l in txt.splitlines() if l.strip()]
except ValueError: print("không đọc được đầu ra docker compose ps"); sys.exit()
by = {r.get("Service"): r for r in rows}
for s in os.environ["W"].split():
    r = by.get(s)
    if not r: print("service %s chưa chạy (không có container)" % s)
    elif r.get("State") != "running": print("service %s State=%s" % (s, r.get("State")))
    elif r.get("Health") not in ("", None, "healthy"): print("service %s Health=%s" % (s, r.get("Health")))
' <<<"$ps_out")
            [ "$n" -ne 0 ] || pass "health: service chạy tốt ($want)"
        fi
    fi
    summary health
}

# ───────────────────────────── all ─────────────────────────────
cmd_all() {
    local s
    for s in env config models health; do
        echo "== $s =="
        bash "${BASH_SOURCE[0]}" "$s" || { echo "all: giai đoạn '$s' trượt; dừng, không chạy các giai đoạn sau" >&2; exit 1; }
    done
    echo "all: OK"
}

case "${1:-}" in
    env) cmd_env ;;
    config) cmd_config ;;
    models) cmd_models ;;
    health) cmd_health ;;
    all) cmd_all ;;
    *) usage ;;
esac
