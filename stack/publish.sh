#!/usr/bin/env bash
# publish.sh - phát hành SecAI stack: image lên GHCR public, dữ liệu lên Docker Hub PRIVATE. Lệnh tay của NGƯỜI PHÁT HÀNH,
# chạy trên máy dev (không phải máy đích).
#
#   publish [--ref REF] [--data-version V] [--stable] [--dry-run]
#       gate (pytest toàn bộ trên git archive REF, build FULL, import app, /app/src == archive, smoke *-smoke-<ngày> trên
#       127.0.0.1) -> image VM -> digest image nền -> secai-stack.json -> bundle FROM scratch (đã quét secret) -> kiểm 3 package
#       image public -> push gateway, vm, bundle (cùng tag YYYY.MM.DD-<sha8>), kiểm digest ẩn danh. --stable: dời tag stable.
#   publish-data [--version YYYY.MM.DD[.N]] [--src-root D] [--mcp-dir D] [--from-pkg D --qdrant-url URL]
#                [--neo4j-password-file F] [--keep] [--dry-run]
#       (--from-pkg: gói có sẵn; bắt buộc --qdrant-url của collection nguồn để quét payload, số điểm phải khớp MANIFEST)
#       pack.sh data (hoặc gói có sẵn) -> quét secret toàn bộ nội dung gói (+ payload Qdrant, chính sách corpus cố định
#       trong lib/secret_scan.py, secret thật của máy này) -> image FROM scratch -> kiểm repo Docker Hub ĐÃ CÓ và PRIVATE -> push
#       $SECAI_DATA_REPO:<ngày>. Cảnh báo khi repo có hơn 3 tag dữ liệu.
#   prune-data [--keep N] [--yes]   xoá tag dữ liệu cũ trên Docker Hub, giữ N bản mới nhất (mặc định 3; không --yes thì chỉ in)
#   promote VERSION     dời tag stable của bundle sang VERSION (bản đó phải đã có trên GHCR).
#   scan PATH...        chỉ quét secret (lib/secret_scan.py).
#
# Image (secai-agent-gateway, secai-pentestcode-vm, secai-stack) ở GHCR phải PUBLIC. Dữ liệu ở Docker Hub, một repo PRIVATE
# duy nhất SECAI_DATA_REPO=docker.io/<namespace>/secai-data (gói miễn phí chỉ có 1 repo private; mỗi bản dữ liệu là một tag).
# Token Docker Hub đọc từ SECAI_DOCKERHUB_TOKEN_FILE (tệp 0600/0400 của user này; Read & Write để push, thêm Delete để prune),
# user SECAI_DOCKERHUB_USER (mặc định = namespace); chỉ đi vào stdin của `docker login` với DOCKER_CONFIG tạm.
# Không push gì khi một gate trượt, khi quét secret có phát hiện, khi visibility sai, hoặc với --dry-run.
# Mã thoát: 0 đạt; 1 lỗi/gate trượt/quét có phát hiện; 5 visibility sai; 6 khớp SECRET THẬT (dừng, báo người phụ trách);
#   7 registry từ chối vì quota/billing (không thử lách).
# Token gh / Docker Hub chỉ đi qua stdin của `docker login` (DOCKER_CONFIG tạm, xoá khi thoát); không lên argv/log.
# Biến (chủ yếu cho test/máy khác): PUBLISH_REPO_ROOT, PUBLISH_PYTHON (python có pytest), PUBLISH_WORK_ROOT (~/secai-releases),
#   PUBLISH_MIN_FREE_GB (45), SECAI_GHCR_OWNER (elonma-china), SECAI_DOCKER_DIR (thư mục .env prod cho smoke),
#   SECAI_DATA_ROOT (dữ liệu nguồn), SECAI_REGISTRY_HELPER (lib/registry.py), SECAI_SEED_BLOB (seed_blob.py của secai-serving),
#   QDRANT_URL (http://127.0.0.1:6333), NEO4J_PASSWORD (hoặc --neo4j-password-file),
#   PUBLISH_KNOWN_SECRETS (thay danh sách nguồn secret thật, mỗi dòng một spec của secret_scan.py --known-secrets; cho test),
#   PUBLISH_NO_FETCH=1 (bỏ `git fetch origin`; chỉ cho test/offline).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${PUBLISH_REPO_ROOT:-$(cd "$HERE/.." && pwd)}"
OWNER="${SECAI_GHCR_OWNER:-elonma-china}"
REGISTRY="ghcr.io/$OWNER"
PKG_GATEWAY=secai-agent-gateway; PKG_VM=secai-pentestcode-vm; PKG_STACK=secai-stack
DATA_REPO="${SECAI_DATA_REPO:-}"                    # docker.io/<namespace>/secai-data (không ghi cứng namespace)
DATA_REPO_RE='^docker\.io/[a-z0-9][a-z0-9_.-]*/secai-data$'
HUB_USER="${SECAI_DOCKERHUB_USER:-}"
HUB_TOKEN_FILE="${SECAI_DOCKERHUB_TOKEN_FILE:-}"
DATA_KEEP=3                                          # Docker Hub có thể bóp băng thông khi dùng nhiều dung lượng
SOURCE_URL="https://github.com/elonma-china/secai-prod"
MAIN="$(git -C "$REPO" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')" || MAIN=""
MAIN="${MAIN:-$REPO}"
BASE="$(dirname "$MAIN")"
PY="${PUBLISH_PYTHON:-$MAIN/.venv/bin/python}"
DOCKER_DIR="${SECAI_DOCKER_DIR:-$BASE/secai-docker}"
DATA_SRC="${SECAI_DATA_ROOT:-$BASE/secai-data}"
WORK_ROOT="${PUBLISH_WORK_ROOT:-$HOME/secai-releases}"
REGHELP="${SECAI_REGISTRY_HELPER:-$HERE/lib/registry.py}"
SCANNER="$HERE/lib/secret_scan.py"
SEED_BLOB="${SECAI_SEED_BLOB:-$BASE/secai-serving/docker/seed_blob.py}"
MIN_FREE_GB="${PUBLISH_MIN_FREE_GB:-45}"
VERSION_RE='^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{8}$'
DATA_RE='^[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[0-9]{1,2})?$'
BASE_KEYS=(QDRANT_IMAGE NEO4J_IMAGE REDIS_IMAGE ES_IMAGE)
WORK=""

die() { echo "publish: $*" >&2; [ -z "$WORK" ] || printf '%s FAIL: %s\n' "$(date '+%F %T')" "$*" >>"$WORK/publish.log" 2>/dev/null || true; exit 1; }
log() { echo "==> $*" >&2; [ -z "$WORK" ] || printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$WORK/publish.log" 2>/dev/null || true; }
need_cmds() { local c; for c in "$@"; do command -v "$c" >/dev/null 2>&1 || die "thiếu lệnh '$c'"; done; }
regq() { python3 -I "$REGHELP" "$@"; }
regq_hub() { python3 -I "$REGHELP" --auth "$HUB_USER:$HUB_TOKEN_FILE" "$@"; }   # token Docker Hub chỉ đọc trong tiến trình python

data_repo_ok() {   # kiểm SECAI_DATA_REPO, suy ra DATA_NS_REPO và user mặc định
    [[ "$DATA_REPO" =~ $DATA_REPO_RE ]] || die "đặt SECAI_DATA_REPO=docker.io/<namespace>/secai-data (đang: '${DATA_REPO:-trống}')"
    DATA_NS_REPO="${DATA_REPO#docker.io/}"
    HUB_USER="${HUB_USER:-${DATA_NS_REPO%%/*}}"
}
hub_token_ok() {   # tệp token Docker Hub: có, thuộc user này, mode 600/400
    local mode owner
    [ -n "$HUB_TOKEN_FILE" ] && [ -f "$HUB_TOKEN_FILE" ] || { echo "publish: cần SECAI_DOCKERHUB_TOKEN_FILE (tệp 0600 chứa token Docker Hub)" >&2; return 1; }
    read -r mode owner <<<"$(stat -L -c '%a %u' "$HUB_TOKEN_FILE")"
    [[ "$mode" =~ ^[64]00$ ]] && [ "$owner" = "$(id -u)" ] \
        || { echo "publish: SECAI_DOCKERHUB_TOKEN_FILE phải thuộc user này và mode 600/400 (đang $mode, uid $owner)" >&2; return 1; }
}
# Push vào repo chưa có thì Docker Hub tự tạo repo, có thể PUBLIC: phải thấy repo đã có và is_private=true mới push.
require_hub_private() {
    local v rc=0
    v="$(regq_hub hub-repo "$DATA_NS_REPO" 2>/dev/null)" || rc=$?
    case "$rc:$v" in
        0:private) return 0 ;;
        0:*) echo "publish: repo Docker Hub $DATA_NS_REPO đang '$v', phải PRIVATE; KHÔNG push" >&2; exit 5 ;;
        1:*) echo "publish: repo Docker Hub $DATA_NS_REPO chưa có hoặc token không đọc được (push sẽ tự tạo repo, có thể public): tạo repo PRIVATE trên hub.docker.com trước; KHÔNG push" >&2; exit 5 ;;
        *) die "không kiểm được visibility của $DATA_NS_REPO trên Docker Hub (rc=$rc); KHÔNG push" ;;
    esac
}

# Nguồn secret THẬT của máy này (chủ dự án 2026-11-27). Thiếu nguồn bắt buộc thì quét lỗi (fail closed); '?' = tuỳ chọn.
known_secret_specs() {
    [ -z "$HUB_TOKEN_FILE" ] || printf '%s\n' "?raw:$HUB_TOKEN_FILE"   # token Docker Hub cũng không được lọt vào gói
    local f; for f in "$HOME"/.config/secai/*.token; do [ ! -f "$f" ] || printf '?raw:%s\n' "$f"; done
    if [ -n "${PUBLISH_KNOWN_SECRETS+x}" ]; then printf '%s\n' "$PUBLISH_KNOWN_SECRETS"; return; fi
    printf '%s\n' gh "env:$DOCKER_DIR/.env" "env:$MAIN/.env" "?env:$REPO/stack/.env" "?env:$MAIN/stack/.env" \
        "compose:$DOCKER_DIR/docker-compose.yml" "shell:$HOME/Downloads/CVE-DB/run_sync.sh" "json:$HOME/secai-releases/*/*.json" \
        "?raw:$HOME/.config/vastai/vast_api_key" "?raw:$HOME/.cache/huggingface/token"
}

CLEANUPS=()
on_exit() {
    local rc=$? c
    for c in ${CLEANUPS[@]+"${CLEANUPS[@]}"}; do eval "$c" >/dev/null 2>&1 || true; done
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

check_disk() {
    local p gb
    for p in "$WORK_ROOT" "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /)"; do
        [ -e "$p" ] || p=/
        gb=$(( $(df -Pk "$p" | awk 'NR == 2 { print $4 }') / 1048576 ))
        (( gb >= MIN_FREE_GB )) || die "chỉ còn ${gb} GB trống ở $p, cần >= ${MIN_FREE_GB} GB; dọn trước khi đóng gói"
    done
}

# Package GHCR mới luôn private và API không đổi được: chủ dự án bật Public trên web. Push khi chưa public = máy đích không kéo được.
pkg_visibility() { gh api "/users/$OWNER/packages/container/$1" --jq .visibility 2>/dev/null || echo "không-đọc-được"; }
require_visibility() {   # require_visibility public|private PKG...
    local want="$1" p v bad=(); shift
    need_cmds gh
    for p in "$@"; do v="$(pkg_visibility "$p")"; [ "$v" = "$want" ] || bad+=("$p=$v"); done
    [ ${#bad[@]} -eq 0 ] && return 0
    echo "publish: package phải $want nhưng: ${bad[*]}" >&2
    if [ "$want" = public ]; then
        echo "publish: bật Public tại https://github.com/users/$OWNER/packages/container/<tên>/settings rồi chạy lại; KHÔNG push gì" >&2
    else
        echo "publish: package phải giữ PRIVATE; KHÔNG push" >&2
    fi
    exit 5
}
require_public() { require_visibility public "$@"; }

tmp_docker_config() {   # một DOCKER_CONFIG tạm cho cả lần chạy; logout + xoá khi thoát
    [ -z "${DOCKER_CONFIG_TMP:-}" ] || return 0
    local ctx ucfg="${DOCKER_CONFIG:-$HOME/.docker}"
    ctx="$(docker context show 2>/dev/null || echo default)"
    DOCKER_CONFIG_TMP="$(mktemp -d)"; chmod 700 "$DOCKER_CONFIG_TMP"
    # mục auths giả: docker không tự dò credential helper hệ thống, token ở lại trong thư mục tạm
    (umask 077; printf '{"auths":{"secai.invalid":{}}}\n' >"$DOCKER_CONFIG_TMP/config.json")
    if [ "$ctx" != default ] && [ -d "$ucfg/contexts" ]; then ln -s "$ucfg/contexts" "$DOCKER_CONFIG_TMP/contexts"; export DOCKER_CONTEXT="$ctx"; fi
    export DOCKER_CONFIG="$DOCKER_CONFIG_TMP"
    CLEANUPS+=("docker logout ghcr.io; docker logout; rm -rf '$DOCKER_CONFIG_TMP'")
}
ghcr_login() {
    need_cmds gh
    tmp_docker_config
    gh auth token | docker login ghcr.io -u "$OWNER" --password-stdin >/dev/null 2>&1 || die "docker login ghcr.io thất bại"
}
hub_login() {   # token đi thẳng từ tệp vào stdin, không qua biến/argv
    tmp_docker_config
    docker login -u "$HUB_USER" --password-stdin <"$HUB_TOKEN_FILE" >/dev/null 2>&1 \
        || die "docker login Docker Hub ($HUB_USER) thất bại: token sai/hết hạn/thiếu quyền ghi"
}

local_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null; }

take_lock() {   # một lần phát hành tại một thời điểm (cùng WORK, cùng tag cục bộ, cùng smoke)
    mkdir -p "$WORK_ROOT"
    exec 8>"$WORK_ROOT/publish.lock"
    flock -n 8 || die "một publish.sh khác đang chạy ($WORK_ROOT/publish.lock)"
}

# tag_absent REF [hub]: 0 nếu tag CHƯA có; tag đã có => die (phiên bản bất biến); không kiểm được => die (fail closed).
# registry.py: 3 = manifest 404 (đã có quyền đọc). GHCR public: package chưa từng có trả 403 khi xin token ẩn danh (rc 1),
# cũng nghĩa là chưa có tag. Docker Hub (đã kiểm repo private đọc được bằng token): chỉ 3 mới là "chưa có".
tag_absent() {
    local rc=0
    if [ "${2:-}" = hub ]; then regq_hub digest "$1" >/dev/null 2>&1 || rc=$?; else regq digest "$1" >/dev/null 2>&1 || rc=$?; fi
    case "$rc:${2:-}" in
        0:*) die "$1 đã có trên registry (phiên bản bất biến)" ;;
        3:*|1:) return 0 ;;
        *) die "không kiểm được $1 trên registry (rc=$rc); thử lại sau, KHÔNG push" ;;
    esac
}

# push_ref REF BASE_REPO: push, đối chiếu digest push in ra với .Id cục bộ (containerd store: .Id == digest manifest).
# NotFound content digest (blob nền thiếu trong content store) -> seed blob từ Docker Hub bằng seed_blob.py rồi thử lại một lần.
push_ref() {   # push_ref REF SEED_SRC_REPO [auth]
    local ref="$1" srcrepo="$2" mode="${3:-}" out d want try dst ok=0 seeded=" " msg
    want="$(local_id "$ref")" || die "không có image cục bộ $ref"
    for try in 1 2 3 4 5 6 7 8 9 10 11 12; do
        if out="$(docker push "$ref" 2>&1)"; then ok=1; break; fi
        d="$(grep -oE 'content digest sha256:[0-9a-f]{64}' <<<"$out" | head -n1 | awk '{ print $3 }')" || d=""
        if [ -n "$d" ] && [ -n "$srcrepo" ] && [ -f "$SEED_BLOB" ] && [[ "$seeded" != *" $d "* ]]; then
            log "blob nền $d thiếu trong content store: seed từ $srcrepo"
            dst="${ref#"$REGISTRY"/}"; dst="$OWNER/${dst%%[:@]*}"
            python3 -I "$SEED_BLOB" "$d" "$srcrepo" "$dst" >&2 || die "seed_blob $d thất bại"
            seeded+="$d "
            continue
        fi
        break
    done
    if [ "$ok" != 1 ]; then
        printf '%s\n' "$out" | tail -n 5 >&2
        # bỏ dòng tiến trình (12 hex:) và digest trước khi so, để mã hex không khớp nhầm
        msg="$(sed -E '/^[0-9a-f]{12}: /d; s/sha256:[0-9a-f]{64}//g' <<<"$out")"
        if grep -qiE 'quota|billing|payment required|spending limit|storage limit|\b402\b' <<<"$msg"; then
            echo "publish: registry từ chối $ref vì quota/billing: cần chủ dự án xử lý (gói miễn phí). KHÔNG thử lách." >&2
            [ -z "$WORK" ] || printf '%s QUOTA: %s\n' "$(date '+%F %T')" "$ref" >>"$WORK/publish.log"
            exit 7
        fi
        die "docker push $ref thất bại"
    fi
    d="$(grep -oE 'digest: sha256:[0-9a-f]{64}' <<<"$out" | tail -n1 | awk '{ print $2 }')" || d=""
    [ "$d" = "$want" ] || die "push $ref: digest in ra '$d' khác .Id cục bộ '$want'"
    verify_remote "$ref" "$want" "$mode"
}
verify_remote() {   # verify_remote REF DIGEST [hub]: đọc ẩn danh (GHCR public: chứng minh máy đích kéo được) hoặc bằng token Docker Hub
    local got i q=regq
    [ "${3:-}" != hub ] || q=regq_hub
    for i in 1 2 3 4 5; do got="$($q digest "$1" 2>/dev/null)" && break; sleep 3; done
    [ "$got" = "$2" ] || die "registry trả digest '$got' cho $1, mong đợi $2"
    log "đã push $1 ($2)"
}

# ───────────────────────────── gates (chép từ scripts/release-prod.sh, không chạm prod) ─────────────────────────────
gate_pytest() {
    local tree="$1" rc=0 last
    log "gate 1/3: pytest toàn bộ trên git archive $S8 (~15 phút)"
    [ -x "$PY" ] || die "không thấy python có pytest: $PY (PUBLISH_PYTHON)"
    if [ -f "$MAIN/.env" ]; then install -m 600 "$MAIN/.env" "$tree/.env"; CLEANUPS+=("rm -f '$tree/.env'"); fi
    (cd "$tree" && PYTHONPATH="$tree/src" "$PY" -m pytest -q -p no:cacheprovider) >"$WORK/pytest.log" 2>&1 || rc=$?
    rm -f "$tree/.env"
    last="$(grep -E '[0-9]+ (passed|failed|error)' "$WORK/pytest.log" | tail -n1)" || last=""
    echo "pytest: ${last:-<không có dòng tổng kết>} (rc=$rc)" >&2
    [ "$rc" = 0 ] && grep -q ' passed' <<<"$last" && ! grep -Eq ' (failed|error)' <<<"$last" \
        || die "pytest không xanh (xem $WORK/pytest.log); KHÔNG phát hành"
}

gate_build_gateway() {
    local tree="$1" ref="$2" cid
    log "gate 2/3: build FULL $ref"
    docker build -q --build-arg GIT_SHA="$S8" --build-arg GIT_BRANCH="$BRANCH" \
        --label org.opencontainers.image.source="$SOURCE_URL" --label org.opencontainers.image.revision="$SHA" \
        --label org.opencontainers.image.version="$VERSION" -t "$ref" "$tree" >/dev/null || die "build gateway lỗi"
    docker run --rm --network none --entrypoint python "$ref" \
        -c "import secai_gateway.unified_app, secai_gateway.multilang_app, secai_gateway.translation.worker" >/dev/null \
        || die "image gateway import app lỗi"
    rm -rf "$WORK/src-fresh" "$WORK/imgsrc"; mkdir -p "$WORK/src-fresh"
    git -C "$REPO" archive "$SHA" src | tar -x -C "$WORK/src-fresh"   # cây mới, không phải cây vừa chạy pytest
    cid="$(docker create "$ref")"; docker cp "$cid:/app/src" "$WORK/imgsrc" >/dev/null; docker rm "$cid" >/dev/null
    diff -rq -x __pycache__ "$WORK/imgsrc" "$WORK/src-fresh/src" >/dev/null || die "/app/src của image khác git archive $S8"
}

gate_smoke() {
    local ref="$1" name port h i data="$DATA_SRC" args=()
    log "gate 3/3: smoke container (chỉ 127.0.0.1, không mount dữ liệu ghi của prod)"
    name="secai-stack-gateway-smoke-$(date +%Y%m%d)"
    if docker inspect "$name" >/dev/null 2>&1; then
        [ "$(docker inspect -f '{{index .Config.Labels "secai.publish"}}' "$name")" = 1 ] || die "đã có container $name không do publish.sh tạo; không đụng"
        docker rm -f "$name" >/dev/null
    fi
    [ -f "$DOCKER_DIR/.env" ] || die "không thấy $DOCKER_DIR/.env cho smoke (SECAI_DOCKER_DIR)"
    port="$(python3 -I -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")"
    CLEANUPS+=("docker rm -f '$name'")
    [ ! -d "$BASE/HO-SO-GOC-NGHIEM-THU-AI" ] || args+=(-v "$BASE/HO-SO-GOC-NGHIEM-THU-AI:/srv/showcase/hosogoc:ro")
    docker run -d --name "$name" --label secai.publish=1 --network secai-docker_secai-net \
        --add-host host.docker.internal:host-gateway --env-file "$DOCKER_DIR/.env" -p "127.0.0.1:$port:8131" \
        -e QDRANT_URL=http://qdrant:6333 -e NEO4J_URI=bolt://neo4j:7687 -e REDIS_URL=redis://redis:6379 \
        -e GATEWAY_DB_PATH=/app/data/gateway.db -e SESSION_DB_PATH=/app/data/sessions.db -e REVIEW_DB_PATH=/app/data/reviews.db \
        -e REPORT_DB_PATH=/app/data/reports.db -e SESSION_STORE_BACKEND=sqlite \
        -e TRANSLATION_UPSTREAM_URL=http://host.docker.internal:18185 -e REVIEW_ENABLED=false -e REWARD_ENABLED=false \
        -e EXPLOITDB_ROOT=/srv/exploitdb -e POC_CATALOG_DB=/srv/poc/catalog.db -e POC_ARTIFACT_STORE=/srv/poc-store \
        -e POC_SOURCES_ROOT=/srv/poc-sources -v "$data/exploitdb:/srv/exploitdb:ro" -v "$data/poc-sources:/srv/poc-sources:ro" \
        ${args[@]+"${args[@]}"} "$ref" uvicorn secai_gateway.unified_app:app --host 0.0.0.0 --port 8131 >/dev/null \
        || die "không chạy được smoke $name"
    h=""
    for i in $(seq 1 45); do h="$(curl -fsS -m 5 "http://127.0.0.1:$port/health" 2>/dev/null)" && [ -n "$h" ] && break; h=""; sleep 2; done
    [ -n "$h" ] || { docker logs --tail 30 "$name" >&2 || true; die "smoke: /health không lên"; }
    printf '%s\n' "$h" >"$WORK/health-smoke.json"
    curl -fsS -m 5 http://127.0.0.1:8131/health >"$WORK/health-prod.json" 2>/dev/null || : >"$WORK/health-prod.json"
    python3 -I - "$WORK/health-smoke.json" "$WORK/health-prod.json" "$S8" <<'PY' || die "smoke: /health sai (xem $WORK/health-smoke.json)"
import json, sys
new = json.load(open(sys.argv[1]))
try: old = json.load(open(sys.argv[2]))
except Exception: old = {}
assert new.get("git_sha") == sys.argv[3], f"git_sha={new.get('git_sha')} != {sys.argv[3]}"
def deps(h): return {k: v.get("ok") for k, v in (h.get("serving") or {}).items() if isinstance(v, dict)} | {k: h[k] for k in ("redis",) if k in h}
bad = [k for k, v in deps(old).items() if v in (True, "ok") and deps(new).get(k) not in (True, "ok")]
assert not bad, f"dep OK ở prod nhưng hỏng ở smoke: {bad}"
PY
    curl -fsS -m 10 -o /dev/null "http://127.0.0.1:$port/ai/v3/translate/languages" || die "smoke: /ai/v3/translate/languages không 200"
    docker rm -f "$name" >/dev/null
}

build_vm() {
    local ref="$1" ctx="$WORK/vm-ctx"
    log "build image VM $ref (git archive pentestcode + stack/images/pentestcode-vm)"
    rm -rf "$ctx"; mkdir -p "$ctx"
    git -C "$REPO" archive "$SHA" pentestcode stack/images/pentestcode-vm | tar -x -C "$ctx"
    docker build -q -f "$ctx/stack/images/pentestcode-vm/Dockerfile" --build-arg GIT_SHA="$SHA" \
        --label org.opencontainers.image.source="$SOURCE_URL" --label org.opencontainers.image.version="$VERSION" \
        -t "$ref" "$ctx" >"$WORK/vm-build.log" 2>&1 || { tail -n 30 "$WORK/vm-build.log" >&2; die "build VM lỗi"; }
    [ "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$ref")" = "$SHA" ] \
        || die "image VM không mang revision $SHA"
    docker run --rm --network none --entrypoint sh "$ref" -c \
        'command -v bun nuclei httpx python3 >/dev/null && test -f /opt/vm/supervisor.py && test -d /opt/pentestcode/node_modules' \
        || die "image VM thiếu công cụ cơ bản"
    rm -rf "$ctx"
}

# base_images TREE -> "KEY<TAB>REF" theo mặc định ${KEY:-ref} trong compose.yml của cây phát hành.
base_images() {
    python3 -I - "$1/stack/compose.yml" "${BASE_KEYS[*]}" <<'PY'
import re, sys
t = open(sys.argv[1]).read()
for k in sys.argv[2].split():
    m = re.search(r"\$\{" + k + r":-([^}]+)\}", t)
    if not m: sys.exit("compose.yml không có ${%s:-...}" % k)
    print(k + "\t" + m.group(1))
PY
}

latest_data_version() {   # repo dữ liệu private: đọc bằng token Docker Hub
    regq_hub hub-tags "$DATA_NS_REPO" 2>/dev/null | grep -E "$DATA_RE" | sort -V | tail -n1
}

write_manifest() {   # write_manifest OUT (đọc IMAGES_TSV, DATA_VERSION, DATA_DIGEST qua môi trường, không ghép vào mã)
    IMAGES_TSV="$IMAGES_TSV" M_VERSION="$VERSION" M_SHA="$SHA" M_SOURCE="$SOURCE_URL" M_REGISTRY="$REGISTRY" \
    M_DATA_IMAGE="$DATA_REPO" M_DATA_VERSION="$DATA_VERSION" M_DATA_DIGEST="$DATA_DIGEST" python3 -I - "$1" <<'PY'
import json, os, sys, time
e = os.environ
imgs = {}
for line in e["IMAGES_TSV"].strip().splitlines():
    k, ref, dg = line.split("\t")
    imgs[k] = {"ref": ref, "digest": dg}
m = {"schema": 1, "version": e["M_VERSION"], "git_sha": e["M_SHA"], "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
     "source": e["M_SOURCE"], "registry": e["M_REGISTRY"], "profiles": ["exploit"], "images": imgs,
     "data": {"image": e["M_DATA_IMAGE"], "default_version": e["M_DATA_VERSION"], "digest": e["M_DATA_DIGEST"]}}
with open(sys.argv[1], "w") as f:
    json.dump(m, f, indent=1, sort_keys=True); f.write("\n")
PY
}

check_manifest() {   # cùng các kiểm của stack/secai (man_images): bundle hỏng thì không dựng
    python3 -I - "$1" "$VERSION" "$2" <<'PY' || die "secai-stack.json không hợp lệ; KHÔNG dựng bundle"
import json, re, sys
m = json.load(open(sys.argv[1])); dry = sys.argv[3] == "1"
keys = ["GATEWAY_IMAGE", "VM_IMAGE", "QDRANT_IMAGE", "NEO4J_IMAGE", "REDIS_IMAGE", "ES_IMAGE"]
assert m.get("schema") == 1 and m.get("version") == sys.argv[2], "schema/version"
assert sorted(m["images"]) == sorted(keys), "images keys"
for k in keys:
    e = m["images"][k]
    assert re.fullmatch(r"[a-z0-9][a-z0-9._/:-]*", e["ref"]) and "@" not in e["ref"] and ":latest" not in e["ref"], k
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", e["digest"]), k
dv, di = m["data"]["default_version"], m["data"]["image"]
assert (dry and dv == "") or re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[0-9]{1,2})?", dv), "data.default_version"
assert (dry and di == "") or re.fullmatch(r"docker\.io/[a-z0-9][a-z0-9_.-]*/secai-data", di), "data.image"
PY
}

scan_tree() {   # scan_tree DIR NAME [scanner args...]: secret thật + luật; chỉ in luật + vị trí, không bao giờ giá trị
    local dir="$1" name="$2" rc=0 spec ks=(); shift 2
    while IFS= read -r spec; do [ -z "$spec" ] || ks+=(--known-secrets "$spec"); done < <(known_secret_specs)
    log "quét secret: $name"
    python3 -I "$SCANNER" --summary-json "$WORK/scan-$name.json" ${ks[@]+"${ks[@]}"} "$@" "$dir" >"$WORK/scan-$name.txt" || rc=$?
    case "$rc" in
        0) log "quét secret $name: sạch (luật + secret thật)" ;;
        1) head -n 50 "$WORK/scan-$name.txt" >&2; die "quét secret $name: có phát hiện (luật + vị trí ở $WORK/scan-$name.txt); KHÔNG push" ;;
        3) grep -E $'^FINDING\tknown-secret\t' "$WORK/scan-$name.txt" | head -n 50 >&2
           echo "publish: !!! $name chứa SECRET THẬT của máy này (nguồn + vị trí ở trên, $WORK/scan-$name.txt). DỪNG, báo người phụ trách; không tự loại trừ." >&2
           exit 6 ;;
        *) die "quét secret $name lỗi (rc=$rc); KHÔNG push" ;;
    esac
}

cmd_publish() {
    local ref=origin/main data_version="" stable=0 dry=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --ref) [ $# -ge 2 ] || die "--ref cần giá trị"; ref="$2"; shift 2 ;;
            --data-version) [ $# -ge 2 ] || die "--data-version cần giá trị"; data_version="$2"; shift 2 ;;
            --stable) stable=1; shift ;;
            --dry-run) dry=1; shift ;;
            *) die "tham số lạ: $1" ;;
        esac
    done
    need_cmds git docker python3 curl tar diff flock
    take_lock
    [ "${PUBLISH_NO_FETCH:-0}" = 1 ] || git -C "$REPO" fetch -q origin || die "git fetch origin lỗi; không phát hành từ ref có thể đã cũ"
    SHA="$(git -C "$REPO" rev-parse --verify "$ref^{commit}")" || die "ref không tồn tại: $ref"
    S8="${SHA:0:8}"
    VERSION="$(date +%Y.%m.%d)-$S8"
    [[ "$VERSION" =~ $VERSION_RE ]] || die "version lạ: $VERSION"
    BRANCH=main
    [ "$SHA" = "$(git -C "$REPO" rev-parse --verify -q origin/main 2>/dev/null)" ] \
        || { BRANCH="$(printf '%s' "${ref#origin/}" | tr -c 'A-Za-z0-9._-' '-')"; echo "publish: CẢNH BÁO: $ref ($S8) không phải origin/main" >&2; }
    mkdir -p "$WORK_ROOT"; WORK="$WORK_ROOT/stack-$VERSION"; mkdir -p "$WORK"; chmod 700 "$WORK"
    log "phát hành $VERSION từ $ref ($SHA: $(git -C "$REPO" log -1 --format='%s' "$SHA"))$([ "$dry" = 0 ] || echo ' (dry-run)')"
    check_disk
    tag_absent "$REGISTRY/$PKG_STACK:$VERSION"   # cả dry-run: không dựng đè tag cục bộ của bản đã phát hành
    [ "$dry" = 1 ] || require_public "$PKG_GATEWAY" "$PKG_VM" "$PKG_STACK"   # dừng sớm, trước 15 phút pytest

    local tree="$WORK/tree"
    rm -rf "$tree"; mkdir -p "$tree"
    git -C "$REPO" archive "$SHA" | tar -x -C "$tree"
    local gw="$REGISTRY/$PKG_GATEWAY:$VERSION" vm="$REGISTRY/$PKG_VM:$VERSION" bundle="$REGISTRY/$PKG_STACK:$VERSION"
    gate_pytest "$tree"
    gate_build_gateway "$tree" "$gw"
    gate_smoke "$gw"
    build_vm "$vm"

    log "digest image nền (registry gốc, ẩn danh)"
    local k r d
    IMAGES_TSV="GATEWAY_IMAGE	$REGISTRY/$PKG_GATEWAY:$VERSION	$(local_id "$gw")
VM_IMAGE	$REGISTRY/$PKG_VM:$VERSION	$(local_id "$vm")"
    local bi; bi="$(base_images "$tree")" || die "compose.yml của $S8 thiếu mặc định \${KEY:-...} cho image nền"
    while IFS=$'\t' read -r k r; do
        d="$(regq digest "$r")" || die "không lấy được digest của $r"
        IMAGES_TSV+=$'\n'"$k"$'\t'"$r"$'\t'"$d"
    done <<<"$bi"
    DATA_VERSION=""; DATA_DIGEST=""
    if [ -n "$DATA_REPO" ] && data_repo_ok && { [ -z "$HUB_TOKEN_FILE" ] || hub_token_ok; } && [ -n "$HUB_TOKEN_FILE" ]; then
        [ -n "$data_version" ] || data_version="$(latest_data_version)" || data_version=""
        DATA_VERSION="$data_version"
        if [ -n "$DATA_VERSION" ]; then
            [[ "$DATA_VERSION" =~ $DATA_RE ]] || die "--data-version '$DATA_VERSION' không hợp lệ"
            DATA_DIGEST="$(regq_hub digest "$DATA_REPO:$DATA_VERSION")" || {
                [ "$dry" = 1 ] || die "không thấy $DATA_REPO:$DATA_VERSION trên Docker Hub (publish-data trước)"; DATA_DIGEST=""; }
        fi
    fi
    if [ -z "$DATA_DIGEST" ] && [ -n "$DATA_REPO" ] && [[ "$DATA_REPO" =~ $DATA_REPO_RE ]] && [[ "$data_version" =~ $DATA_RE ]] \
        && [ -z "$HUB_TOKEN_FILE" ]; then
        # Repo dữ liệu private mà chưa có token đọc: chỉ nhận khi người phát hành ghi rõ --data-version; digest để trống.
        echo "publish: CẢNH BÁO: chưa kiểm được $DATA_REPO:$data_version (repo private, không có SECAI_DOCKERHUB_TOKEN_FILE); manifest ghi bản dữ liệu mặc định không kèm digest" >&2
        DATA_VERSION="$data_version"
    elif [ -z "$DATA_VERSION" ] || [ -z "$DATA_DIGEST" ]; then
        [ "$dry" = 1 ] || die "cần SECAI_DATA_REPO, SECAI_DOCKERHUB_TOKEN_FILE và một bản dữ liệu đã có trên Docker Hub (publish-data trước hoặc --data-version)"
        echo "publish: (dry-run) chưa xác định bản dữ liệu mặc định (SECAI_DATA_REPO/token/tag)" >&2
        DATA_VERSION=""; DATA_DIGEST=""; [ -n "$DATA_REPO" ] && [[ "$DATA_REPO" =~ $DATA_REPO_RE ]] || DATA_REPO=""
    fi

    log "bundle $bundle"
    local bdir="$WORK/bundle"
    rm -rf "$bdir"; mkdir -p "$bdir"
    git -C "$REPO" archive "$SHA" stack | tar -x -C "$bdir"
    write_manifest "$bdir/stack/secai-stack.json"
    check_manifest "$bdir/stack/secai-stack.json" "$dry"
    scan_tree "$bdir/stack" bundle   # bundle là image public: mọi luật đều chặn, không allowlist
    printf 'FROM scratch\nCOPY stack /stack\n' >"$WORK/Dockerfile.bundle"
    docker build -q -f "$WORK/Dockerfile.bundle" --label org.opencontainers.image.source="$SOURCE_URL" \
        --label org.opencontainers.image.revision="$SHA" --label org.opencontainers.image.version="$VERSION" \
        --label org.opencontainers.image.title="$PKG_STACK" -t "$bundle" "$bdir" >/dev/null || die "build bundle lỗi"
    cp "$bdir/stack/secai-stack.json" "$WORK/secai-stack.json"
    rm -rf "$tree"

    if [ "$dry" = 1 ]; then
        log "DRY-RUN xong: không login, không push. Manifest: $WORK/secai-stack.json"
        cat "$WORK/secai-stack.json"
        return 0
    fi
    require_public "$PKG_GATEWAY" "$PKG_VM" "$PKG_STACK"
    tag_absent "$REGISTRY/$PKG_GATEWAY:$VERSION"; tag_absent "$REGISTRY/$PKG_VM:$VERSION"; tag_absent "$bundle"
    ghcr_login
    push_ref "$gw" library/python
    push_ref "$vm" oven/bun
    push_ref "$bundle" ""
    [ "$stable" = 0 ] || promote_to_stable "$VERSION"
    log "XONG: $VERSION (gateway $(local_id "$gw"), vm $(local_id "$vm"), bundle $(local_id "$bundle")); ghi ở $WORK"
    cat "$WORK/secai-stack.json"
}

promote_to_stable() {
    local v="$1" want
    want="$(regq digest "$REGISTRY/$PKG_STACK:$v")" || die "không đọc được $REGISTRY/$PKG_STACK:$v trên GHCR"
    # kéo đúng digest trên GHCR (không tin tag cục bộ có thể đã bị dựng lại)
    docker pull -q "$REGISTRY/$PKG_STACK@$want" >/dev/null || die "không kéo được $REGISTRY/$PKG_STACK@$want"
    docker tag "$REGISTRY/$PKG_STACK@$want" "$REGISTRY/$PKG_STACK:stable"
    [ "$(local_id "$REGISTRY/$PKG_STACK:stable")" = "$want" ] || die "tag stable cục bộ không trỏ đúng $want"
    ghcr_login
    push_ref "$REGISTRY/$PKG_STACK:stable" ""
    log "stable -> $v ($want)"
}

cmd_promote() {
    local v="${1:-}"
    [[ "$v" =~ $VERSION_RE ]] || die "dùng: publish.sh promote YYYY.MM.DD-<sha8>"
    need_cmds docker python3 flock
    take_lock
    require_public "$PKG_STACK"
    promote_to_stable "$v"
}

cmd_publish_data() {
    local version="" src="$DATA_SRC" mcp="" frompkg="" pwfile="" keep=0 dry=0 qurl=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --version) version="$2"; shift 2 ;;
            --src-root) src="$2"; shift 2 ;;
            --mcp-dir) mcp="$2"; shift 2 ;;
            --from-pkg) frompkg="$2"; shift 2 ;;
            --qdrant-url) qurl="$2"; shift 2 ;;
            --neo4j-password-file) pwfile="$2"; shift 2 ;;
            --keep) keep=1; shift ;;
            --dry-run) dry=1; shift ;;
            *) die "tham số lạ: $1" ;;
        esac
    done
    need_cmds docker python3 zstd sha256sum du flock
    take_lock
    version="${version:-$(date +%Y.%m.%d)}"
    [[ "$version" =~ $DATA_RE ]] || die "version dữ liệu '$version' không hợp lệ (YYYY.MM.DD hoặc YYYY.MM.DD.N)"
    data_repo_ok
    local ref="$DATA_REPO:$version" hubok=1
    hub_token_ok || hubok=0
    [ "$dry" = 1 ] || [ "$hubok" = 1 ] || exit 5
    mkdir -p "$WORK_ROOT"; WORK="$WORK_ROOT/data-$version"; mkdir -p "$WORK"; chmod 700 "$WORK"
    log "phát hành dữ liệu $version$([ "$dry" = 0 ] || echo ' (dry-run)')"
    check_disk
    if [ "$hubok" = 1 ]; then
        [ "$dry" = 1 ] || require_hub_private
        tag_absent "$ref" hub
    else
        echo "publish: (dry-run) không có token Docker Hub: bỏ kiểm repo private và tag đã có" >&2
    fi
    local pkg qargs=()
    if [ -n "$frompkg" ]; then
        pkg="$(cd "$frompkg" && pwd)"
        [ -s "$pkg/SHA256SUMS" ] || die "$pkg không phải gói dữ liệu (thiếu SHA256SUMS)"
        # Snapshot là SST nén: quét thô không thấy hết payload. Bắt buộc quét payload qua API của collection nguồn
        # (số điểm phải khớp MANIFEST để chắc là cùng dữ liệu).
        [ -n "$qurl" ] || die "--from-pkg cần --qdrant-url <collection nguồn> để quét payload Qdrant (fail closed)"
        qargs=(--qdrant-url "$qurl" --qdrant-collection "${QDRANT_COLLECTION:-cve_kb_hybrid}")
    else
        local hh; hh="$(date +%H)"
        [ "$hh" != 03 ] && [ "$hh" != 04 ] || die "03:00-05:00: autoscan ghi Neo4j, export sẽ lệch số đếm; chạy giờ khác"
        if [ -n "$pwfile" ]; then
            [ -f "$pwfile" ] && [ "$(stat -c %a "$pwfile")" = 600 ] || die "--neo4j-password-file phải là tệp 0600"
            NEO4J_PASSWORD="$(tr -d '\r\n' <"$pwfile")"
        fi
        [ -n "${NEO4J_PASSWORD:-}" ] || die "cần NEO4J_PASSWORD (env) hoặc --neo4j-password-file để xuất Neo4j"
        export NEO4J_PASSWORD
        pkg="$WORK/pkg"
        [ ! -e "$pkg" ] || die "$pkg đã có (lần chạy dở?); xoá hoặc dùng --from-pkg $pkg --qdrant-url <collection nguồn>"
        log "pack.sh data $src -> $pkg (snapshot Qdrant tạo rồi xoá trên máy nguồn; Neo4j chỉ đọc)"
        bash "$HERE/pack.sh" data ${mcp:+--mcp-dir "$mcp"} "$src" "$pkg" >&2 || die "pack.sh data lỗi"
        unset NEO4J_PASSWORD
        qargs=(--qdrant-url "${QDRANT_URL:-http://127.0.0.1:6333}" --qdrant-collection "${QDRANT_COLLECTION:-cve_kb_hybrid}")
    fi
    (cd "$pkg" && sha256sum -c --strict --quiet SHA256SUMS) || die "SHA256SUMS của gói không khớp"
    scan_tree "$pkg" data --policy data --exclude .git ${qargs[@]+"${qargs[@]}"}
    python3 -I - "$WORK/scan-data.json" "$pkg/MANIFEST.md" <<'PY' || die "số điểm Qdrant đã quét payload khác MANIFEST: collection nguồn đã đổi so với gói; KHÔNG push"
import json, re, sys
got = json.load(open(sys.argv[1])).get("qdrant_points")
want = sum(int(n) for n in re.findall(r"- qdrant collection: \S+ \((\d+) points\)", open(sys.argv[2], encoding="utf-8").read()))
sys.exit(0 if got == want and want > 0 else 1)
PY
    # Mỗi nhóm COPY là một layer; giữ dưới 9,5 GB mỗi layer (registry thường giới hạn khoảng 10 GB).
    local grp sz
    for grp in qdrant neo4j "exploitdb.tar.zst poc.tar.zst poc-sources.tar.zst exploitdb-mcp-server.tar.zst"; do
        sz=$(cd "$pkg" && du -scb $grp | tail -n1 | cut -f1)
        (( sz < 9500000000 )) || die "layer '$grp' ${sz} byte > 9,5 GB"
    done
    cat >"$WORK/Dockerfile.data" <<'EOF'
FROM scratch
COPY qdrant /secai-data/qdrant
COPY neo4j /secai-data/neo4j
COPY exploitdb.tar.zst poc.tar.zst poc-sources.tar.zst exploitdb-mcp-server.tar.zst /secai-data/
COPY MANIFEST.md SHA256SUMS /secai-data/
EOF
    log "build $ref"
    docker build -q -f "$WORK/Dockerfile.data" --label org.opencontainers.image.source="$SOURCE_URL" \
        --label org.opencontainers.image.version="$version" --label org.opencontainers.image.title=secai-data \
        -t "$ref" "$pkg" >/dev/null || die "build image dữ liệu lỗi"
    cp "$pkg/MANIFEST.md" "$WORK/MANIFEST.md"
    if [ "$dry" = 1 ]; then
        log "DRY-RUN xong: image $ref dựng cục bộ ($(local_id "$ref")), không push"
        return 0
    fi
    require_hub_private
    tag_absent "$ref" hub
    hub_login
    push_ref "$ref" "" hub
    python3 -I - "$WORK/scan-data.json" "$version" "$(local_id "$ref")" "$WORK/data-release.json" <<'PY'
import json, sys
scan = json.load(open(sys.argv[1]))
rec = {"version": sys.argv[2], "digest": sys.argv[3], "secret_scan": {
    "policy": scan.get("policy"), "corpus": scan.get("corpus"), "corpus_counts": scan.get("corpus_counts"),
    "findings": len(scan.get("findings", [])), "known_secret_hits": scan.get("known_secret_hits"),
    "scanned_files": scan.get("scanned_files"), "scanned_bytes": scan.get("scanned_bytes"), "qdrant_points": scan.get("qdrant_points")}}
json.dump(rec, open(sys.argv[4], "w"), ensure_ascii=False, indent=1)
PY
    if [ "$keep" = 0 ]; then
        [ -n "$frompkg" ] || rm -rf "$pkg"
        docker image rm "$ref" >/dev/null 2>&1 || true
    fi
    log "XONG: $ref ($(cat "$WORK/data-release.json"))"
    local n; n="$(regq_hub hub-tags "$DATA_NS_REPO" 2>/dev/null | grep -cE "$DATA_RE")" || n=0
    if (( n > DATA_KEEP )); then
        echo "publish: CẢNH BÁO: $DATA_NS_REPO có $n bản dữ liệu (> $DATA_KEEP); Docker Hub có thể bóp băng thông. Dọn: stack/publish.sh prune-data --keep $DATA_KEEP --yes (giữ bản mà bundle stable đang trỏ tới)" >&2
    fi
}

# stable_data_version: data.default_version trong bundle ghcr .../secai-stack:stable (public, kéo ẩn danh). In rỗng nếu chưa
# có tag stable; không đọc được => trả 1 (fail closed).
stable_data_version() {
    local rc=0 cid tmp v
    regq digest "$REGISTRY/$PKG_STACK:stable" >/dev/null 2>&1 || rc=$?
    case "$rc" in 0) ;; 1|3) return 0 ;; *) return 1 ;; esac
    docker pull -q "$REGISTRY/$PKG_STACK:stable" >/dev/null 2>&1 || return 1
    tmp="$(mktemp -d)"; CLEANUPS+=("rm -rf '$tmp'")
    cid="$(docker create "$REGISTRY/$PKG_STACK:stable" /none 2>/dev/null)" || return 1
    docker cp "$cid:/stack/secai-stack.json" "$tmp/m.json" >/dev/null 2>&1 || { docker rm -f "$cid" >/dev/null 2>&1; return 1; }
    docker rm -f "$cid" >/dev/null 2>&1 || true
    v="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["default_version"])' "$tmp/m.json")" || return 1
    printf '%s' "$v"
}

cmd_prune_data() {
    local keep="$DATA_KEEP" yes=0 tags del t keepv="" sv
    while [ $# -gt 0 ]; do
        case "$1" in
            --keep) keep="$2"; shift 2 ;;
            --protect) keepv+=" $2"; shift 2 ;;
            --yes) yes=1; shift ;;
            *) die "tham số lạ: $1" ;;
        esac
    done
    [[ "$keep" =~ ^[1-9][0-9]*$ ]] || die "--keep phải là số >= 1"
    need_cmds python3 flock
    take_lock
    data_repo_ok
    hub_token_ok || exit 5
    tags="$(regq_hub hub-tags "$DATA_NS_REPO")" || die "không đọc được tag của $DATA_NS_REPO"
    sv="$(stable_data_version)" || die "không đọc được bản dữ liệu mà bundle stable dùng; KHÔNG xoá gì"
    [ -z "$sv" ] || { keepv+=" $sv"; log "giữ $sv (bundle stable dùng)"; }
    del="$(grep -E "$DATA_RE" <<<"$tags" | sort -V | head -n -"$keep")" || del=""
    for t in $keepv; do del="$(grep -vxF "$t" <<<"$del" || true)"; done
    if [ -z "${del//[[:space:]]/}" ]; then log "không có tag dữ liệu nào cần xoá (giữ $keep${keepv:+, và$keepv})"; return 0; fi
    echo "Sẽ xoá khỏi $DATA_NS_REPO (giữ $keep bản mới nhất):" >&2; printf '  %s\n' $del >&2
    if [ "$yes" != 1 ]; then echo "publish: chưa xoá gì; thêm --yes để xoá (token cần quyền Delete)" >&2; return 0; fi
    for t in $del; do [ -n "$t" ] || continue; regq_hub hub-delete-tag "$DATA_NS_REPO" "$t" || die "xoá tag $t lỗi (token thiếu quyền Delete?)"; log "đã xoá $DATA_NS_REPO:$t"; done
}

case "${1:-}" in
    publish) shift; cmd_publish "$@" ;;
    publish-data) shift; cmd_publish_data "$@" ;;
    prune-data) shift; cmd_prune_data "$@" ;;
    promote) shift; cmd_promote "$@" ;;
    scan) shift; [ $# -gt 0 ] || die "dùng: publish.sh scan PATH..."; python3 -I "$SCANNER" "$@" ;;
    *) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
