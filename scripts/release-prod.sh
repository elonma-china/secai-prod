#!/usr/bin/env bash
# Release origin/main lên prod (3 container cùng 1 image): secai-docker-gateway (:8131),
# secai-translation-gateway, secai-translation-worker. Chạy SAU khi merge PR.
#
#   scripts/release-prod.sh plan                # mặc định: chỉ in kế hoạch + diff so với prod, không đổi gì
#   scripts/release-prod.sh gates               # plan + test + build image + smoke; KHÔNG đổi prod
#   scripts/release-prod.sh run --yes           # gates + chuyển prod (tự rollback nếu health sai)
#   scripts/release-prod.sh rollback --yes      # quay về image trước của lần release gần nhất
#   scripts/release-prod.sh gc [--yes]          # xoá image/container rollback+smoke cũ hơn 3 ngày
#
# Nguyên tắc (xem CLAUDE.md): image bất biến tag main-<sha8>-<ngày>, build từ `git archive origin/main`
# (không mount src sống), không docker run/rm tay vào container prod, lock dùng chung với các script
# release khác, rollback = đổi lại tag image + compose/profile đã sao lưu (image cũ giữ >= 3 ngày).
set -euo pipefail
# Mọi file script tạo (bản sao compose, inspect/health/state, profile, log) có thể chứa secret: chỉ chủ sở hữu đọc.
umask 077

MODE=${1:-plan}; shift || true
YES=0; for a in "$@"; do [ "$a" = "--yes" ] && YES=1; done

DOCKER_DIR=${SECAI_DOCKER_DIR:-/home/ict/AIEngines/secai/secai-docker}
COMPOSE=$DOCKER_DIR/docker-compose.yml
RELEASES=${SECAI_RELEASES_DIR:-/home/ict/secai-releases}
REPO=${SECAI_REPO:-$(git worktree list --porcelain | sed -n '1s/^worktree //p')}
IMG=secai-agent-gateway
TRANS_PROJECT=secai-translation-release
KEEP_DAYS=3
TODAY=$(date +%Y%m%d)
LATEST_PTR=$RELEASES/LATEST-PROD

die() { echo "release-prod: $*" >&2; exit 1; }
log() { echo ">> $*" >&2; }
need_yes() { [ "$YES" = 1 ] || die "cần --yes để thực hiện (đang ở chế độ xem trước)"; }
# Tạo/làm rỗng file mode 600 trước khi ghi: umask chỉ áp cho file mới, file cùng tên còn từ lần
# chạy trước (cùng ngày, cùng sha) giữ mode cũ.
priv() { local f; for f in "$@"; do : > "$f"; chmod 600 "$f"; done; }

prod_tag() { docker inspect "$1" --format '{{.Config.Image}}' 2>/dev/null; }
tag_sha() { sed -E 's/^.*:main-([0-9a-f]{8})-.*$/\1/' <<<"$1"; }

# ---------------------------------------------------------------- gc
gc() {
    local cutoff; cutoff=$(date -d "$KEEP_DAYS days ago" +%s)
    local keep; keep=$(for c in $(docker ps -aq); do docker inspect "$c" --format '{{.Config.Image}}'; done | sort -u)
    local prev=""; [ -f "$LATEST_PTR" ] && prev=$(grep '^PREV_TAG=' "$(cat "$LATEST_PTR")/state.env" 2>/dev/null | cut -d= -f2- || true)
    local n=0
    # container rollback/smoke đã dừng
    for c in $(docker ps -a --filter status=exited --filter status=created --format '{{.Names}}' | grep -E -- '-(rollback|smoke)-' || true); do
        local t; t=$(date -d "$(docker inspect "$c" --format '{{.Created}}')" +%s)
        [ "$t" -lt "$cutoff" ] || continue
        n=$((n+1)); echo "container $c (tạo $(date -d @"$t" +%F))"
        [ "$YES" = 1 ] && docker rm "$c" >/dev/null
    done
    # image main-*/smoke-* không còn container dùng, quá hạn, không phải bản rollback của lần release gần nhất
    for r in $(docker images "$IMG" --format '{{.Repository}}:{{.Tag}}' | grep -E ":(main|smoke)-" || true); do
        [ "$r" = "$prev" ] && continue
        grep -qxF "$r" <<<"$keep" && continue
        local t; t=$(date -d "$(docker image inspect "$r" --format '{{.Created}}')" +%s)
        [ "$t" -lt "$cutoff" ] || continue
        n=$((n+1)); echo "image $r (tạo $(date -d @"$t" +%F))"
        [ "$YES" = 1 ] && docker rmi "$r" >/dev/null
    done
    [ "$YES" = 1 ] && echo "gc: đã xoá $n mục (> $KEEP_DAYS ngày)" || echo "gc dry-run: $n mục sẽ bị xoá (thêm --yes)"
}
if [ "$MODE" = gc ]; then gc; exit 0; fi

# ---------------------------------------------------------------- rollback
if [ "$MODE" = rollback ]; then
    need_yes
    [ -f "$LATEST_PTR" ] || die "chưa có release nào được ghi nhận ($LATEST_PTR)"
    REL=$(cat "$LATEST_PTR"); [ -f "$REL/state.env" ] || die "thiếu $REL/state.env"
    # shellcheck disable=SC1091
    . "$REL/state.env"
    exec 8>"$RELEASES/gateway-deploy.lock" 9>"$RELEASES/translation-deploy.lock"; flock -n 8 && flock -n 9 || die "đang có release khác giữ lock"
    # Không `cp -p`: bản sao lưu là 600, còn compose prod giữ mode của nó (gateway đọc nó qua mount ro).
    cp --preserve=timestamps "$COMPOSE_BAK" "$COMPOSE"
    (cd "$DOCKER_DIR" && docker compose up -d --no-deps --no-build gateway)
    docker compose -p "$TRANS_PROJECT" -f "$TRANS_PREV_PROFILE" up -d --no-deps --no-build gateway worker
    SECAI_RELEASES_DIR="$RELEASES" timeout 60 python3 -I "$(dirname "$0")/versions.py" record --service gateway --action rollback \
        --from "$NEW_TAG" --to "$PREV_TAG" --release-dir "$REL" || log "cảnh báo: không ghi được sổ phiên bản (versions.py record); rollback vẫn xong"
    echo "rollback xong → $PREV_TAG (kiểm /health 8131 và 18185)"; exit 0
fi

[ "$MODE" = plan ] || [ "$MODE" = gates ] || [ "$MODE" = run ] || die "usage: $0 plan|gates|run|rollback|gc [--yes]"
[ "$MODE" != run ] || need_yes

# ---------------------------------------------------------------- plan
git -C "$REPO" fetch -q origin
TARGET=$(git -C "$REPO" rev-parse origin/main); S8=${TARGET:0:8}
NEW_TAG=$IMG:main-$S8-$TODAY
CUR_TAG=$(prod_tag secai-docker-gateway) || die "không thấy container secai-docker-gateway"
CUR_S8=$(tag_sha "$CUR_TAG")
[[ "$CUR_S8" =~ ^[0-9a-f]{8}$ ]] || die "image prod '$CUR_TAG' không theo dạng main-<sha8>-<ngày>; release tay lần này"
for c in secai-translation-gateway secai-translation-worker; do
    [ "$(prod_tag $c)" = "$CUR_TAG" ] || die "$c không chạy cùng image $CUR_TAG (invariant 3 container = 1 image bị lệch); xử lý tay"
done
[ "$CUR_S8" != "$S8" ] || { echo "prod đã chạy origin/main ($S8); không có gì để release"; exit 0; }
git -C "$REPO" merge-base --is-ancestor "$CUR_S8" "$TARGET" || die "$CUR_S8 (prod) không phải tổ tiên của origin/main $S8 — prod có commit ngoài main; dừng"

echo "prod:   $CUR_TAG"; echo "target: $NEW_TAG"
echo "--- commit mới trên main kể từ prod"; git -C "$REPO" log --oneline "$CUR_S8..$TARGET"
echo "--- file đổi"; git -C "$REPO" diff --stat "$CUR_S8" "$TARGET" | tail -25
DEPS_CHANGED=0
git -C "$REPO" diff --quiet "$CUR_S8" "$TARGET" -- pyproject.toml uv.lock Dockerfile bin kpi_bench || DEPS_CHANGED=1
if [ "$DEPS_CHANGED" = 1 ]; then echo "build: FULL (pyproject/uv.lock/Dockerfile/bin/kpi_bench có đổi → cài lại deps, cần xem kỹ pip freeze)"
else echo "build: OVERLAY src/ lên $CUR_TAG (deps không đổi)"; fi
echo "đổi file cấu hình/env?"; git -C "$REPO" diff --name-only "$CUR_S8" "$TARGET" | grep -E '(^|/)(\.env|compose|docker-compose)|settings\.py$|config\.py$' || echo "  (không)"
[ "$MODE" != plan ] || { echo; echo "(plan) chạy 'gates' để test+build+smoke, 'run --yes' để release"; exit 0; }

# ---------------------------------------------------------------- gates
REL=$RELEASES/main-$S8-$TODAY; mkdir -p "$REL"; chmod 700 "$REL"
TREE=$REL/tree; rm -rf "$TREE"
# Cây build giữ quyền file như trong git (umask 022) để quyền file trong image không đổi; $REL đã 700.
(umask 022; mkdir -p "$TREE" && git -C "$REPO" archive "$TARGET" | tar -x -C "$TREE")

log "gate 1/3: pytest toàn bộ trên cây origin/main ($S8)"
PY=$REPO/.venv/bin/python; [ -x "$PY" ] || die "không thấy $PY"
[ -f "$REPO/.env" ] && install -m 600 "$REPO/.env" "$TREE/.env"
(cd "$TREE" && PYTHONPATH="$TREE/src" "$PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -15 | tee "$REL/pytest.log") \
    || true
tail -1 "$REL/pytest.log" | grep -Eq ' passed' && ! tail -1 "$REL/pytest.log" | grep -Eq ' (failed|error)' || die "pytest không xanh (xem $REL/pytest.log) — KHÔNG release"
rm -f "$TREE/.env"

log "gate 2/3: build image $NEW_TAG"
if [ "$DEPS_CHANGED" = 1 ]; then
    docker build -q --build-arg GIT_SHA="$S8" --build-arg GIT_BRANCH=main -t "$NEW_TAG" "$TREE" >/dev/null
else
    cat > "$TREE/Dockerfile.overlay" <<EOF
FROM $CUR_TAG
RUN rm -rf /app/src
COPY --chown=app:app src /app/src
ENV GIT_SHA=$S8 GIT_BRANCH=main
LABEL org.opencontainers.image.revision=$S8
EOF
    docker build -q -f "$TREE/Dockerfile.overlay" -t "$NEW_TAG" "$TREE" >/dev/null
fi
docker run --rm --entrypoint python "$NEW_TAG" -c "import secai_gateway.unified_app, secai_gateway.multilang_app, secai_gateway.translation.worker" \
    || die "image mới import lỗi"
# nội dung /app/src của image == git archive
CID=$(docker create "$NEW_TAG"); rm -rf "$REL/imgsrc"; docker cp "$CID:/app/src" "$REL/imgsrc" >/dev/null; docker rm "$CID" >/dev/null
diff -rq -x __pycache__ "$REL/imgsrc" "$TREE/src" >/dev/null || die "/app/src của image khác git archive $S8"
if [ "$DEPS_CHANGED" = 1 ]; then
    diff <(docker run --rm --entrypoint pip "$CUR_TAG" freeze | sort) <(docker run --rm --entrypoint pip "$NEW_TAG" freeze | sort) > "$REL/pip-freeze.diff" || true
    echo "pip freeze khác prod: $(grep -c '^[<>]' "$REL/pip-freeze.diff") dòng ($REL/pip-freeze.diff)"
fi

log "gate 3/3: smoke container (không chạm dữ liệu prod, chỉ bind 127.0.0.1)"
SMOKE=secai-docker-gateway-smoke-$TODAY; docker rm -f "$SMOKE" >/dev/null 2>&1 || true
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")
trap 'docker rm -f "$SMOKE" >/dev/null 2>&1 || true' EXIT
D=${SECAI_DATA_ROOT:-/home/ict/AIEngines/secai/secai-data}
docker run -d --name "$SMOKE" --network secai-docker_secai-net --add-host host.docker.internal:host-gateway \
    --env-file "$DOCKER_DIR/.env" -p "127.0.0.1:$PORT:8131" \
    -e QDRANT_URL=http://qdrant:6333 -e NEO4J_URI=bolt://neo4j:7687 -e REDIS_URL=redis://redis:6379 \
    -e GATEWAY_DB_PATH=/app/data/gateway.db -e SESSION_DB_PATH=/app/data/sessions.db -e REVIEW_DB_PATH=/app/data/reviews.db \
    -e REPORT_DB_PATH=/app/data/reports.db -e SESSION_STORE_BACKEND=sqlite \
    -e TRANSLATION_UPSTREAM_URL=http://host.docker.internal:18185 -e REVIEW_ENABLED=false -e REWARD_ENABLED=false \
    -e EXPLOITDB_ROOT=/srv/exploitdb -e POC_CATALOG_DB=/srv/poc/catalog.db -e POC_ARTIFACT_STORE=/srv/poc-store -e POC_SOURCES_ROOT=/srv/poc-sources \
    -v "$D/exploitdb:/srv/exploitdb:ro" -v "$D/poc-sources:/srv/poc-sources:ro" \
    -v /home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI:/srv/showcase/hosogoc:ro \
    "$NEW_TAG" uvicorn secai_gateway.unified_app:app --host 0.0.0.0 --port 8131 >/dev/null
ok=0
for _ in $(seq 1 45); do
    h=$(curl -fsS -m 5 "http://127.0.0.1:$PORT/health" 2>/dev/null || true)
    [ -n "$h" ] && { ok=1; break; }; sleep 2
done
[ "$ok" = 1 ] || { docker logs --tail 30 "$SMOKE" >&2; die "smoke: /health không lên"; }
priv "$REL/health-smoke.json" "$REL/health-8131-before.json"
echo "$h" > "$REL/health-smoke.json"; curl -fsS -m 5 http://127.0.0.1:8131/health > "$REL/health-8131-before.json" 2>/dev/null || true
python3 - "$REL/health-smoke.json" "$REL/health-8131-before.json" "$S8" <<'PY' || die "smoke: /health sai (xem $REL/health-smoke.json)"
import json, sys
new = json.load(open(sys.argv[1]))
try: old = json.load(open(sys.argv[2]))
except Exception: old = {}
assert new.get("git_sha") == sys.argv[3], f"git_sha={new.get('git_sha')} != {sys.argv[3]}"
# dep nào đang OK ở prod mà smoke lại hỏng => regression (dep hỏng sẵn ở prod như LLM Vast thì bỏ qua)
def deps(h): return {k: v.get("ok") for k, v in (h.get("serving") or {}).items() if isinstance(v, dict)} | {k: h[k] for k in ("redis",) if k in h}
bad = [k for k, v in deps(old).items() if v in (True, "ok") and deps(new).get(k) not in (True, "ok")]
assert not bad, f"dep OK ở prod nhưng hỏng ở smoke: {bad}"
PY
curl -fsS -m 10 -o /dev/null "http://127.0.0.1:$PORT/ai/v3/translate/languages" || die "smoke: /ai/v3/translate/languages không 200"
docker rm -f "$SMOKE" >/dev/null; trap - EXIT
echo "GATES OK: $NEW_TAG (release dir $REL)"
[ "$MODE" = run ] || exit 0

# ---------------------------------------------------------------- switch
exec 8>"$RELEASES/gateway-deploy.lock" 9>"$RELEASES/translation-deploy.lock"
flock -n 8 && flock -n 9 || die "đang có release khác giữ lock"
TRANS_PREV=$(docker inspect secai-translation-gateway --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
[ -f "$TRANS_PREV" ] || die "không thấy profile translation hiện tại ($TRANS_PREV)"
COMPOSE_BAK=$REL/docker-compose.yml.bak-before-img$S8-$TODAY
install -p -m 600 "$COMPOSE" "$COMPOSE_BAK"
TRANS_NEW=$REL/translation-main.json
(umask 077; sed "s#$CUR_TAG#$NEW_TAG#g" "$TRANS_PREV" > "$TRANS_NEW")
grep -q "$NEW_TAG" "$TRANS_NEW" || die "profile translation không chứa $CUR_TAG để thay"
n=$(grep -c "image: $CUR_TAG\$" "$COMPOSE" || true); [ "$n" = 1 ] || die "compose có $n dòng 'image: $CUR_TAG' (cần đúng 1)"
priv "$REL/state.env"
printf 'PREV_TAG=%s\nNEW_TAG=%s\nCOMPOSE_BAK=%s\nTRANS_PREV_PROFILE=%s\nTRANS_NEW_PROFILE=%s\n' "$CUR_TAG" "$NEW_TAG" "$COMPOSE_BAK" "$TRANS_PREV" "$TRANS_NEW" > "$REL/state.env"
echo "$REL" > "$LATEST_PTR"
for c in secai-docker-gateway secai-translation-gateway secai-translation-worker; do
    priv "$REL/inspect-before-$c.json"; docker inspect $c > "$REL/inspect-before-$c.json"  # env container: có secret
done

wait_health() {  # url sha-expected(optional)
    for _ in $(seq 1 45); do
        h=$(curl -fsS -m 5 "$1" 2>/dev/null || true)
        if [ -n "$h" ] && { [ -z "${2:-}" ] || grep -q "\"git_sha\":\"$2\"" <<<"$h"; }; then echo "$h"; return 0; fi
        sleep 2
    done; return 1
}
revert() { log "HEALTH SAI → rollback tự động"; exec 8>&- 9>&-; "$0" rollback --yes || true; die "đã rollback về $CUR_TAG; xem log container"; }

log "switch gateway :8131 → $NEW_TAG"
sed -i "s#^\(\s*image: \)$CUR_TAG\$#\1$NEW_TAG#" "$COMPOSE"
(cd "$DOCKER_DIR" && docker compose up -d --no-deps --no-build gateway) || revert
priv "$REL/health-8131-after.json"
wait_health http://127.0.0.1:8131/health "$S8" > "$REL/health-8131-after.json" || revert

log "switch translation gateway+worker → $NEW_TAG"
docker compose -p "$TRANS_PROJECT" -f "$TRANS_NEW" up -d --no-deps --no-build gateway worker || revert
wait_health http://127.0.0.1:18185/health >/dev/null || revert
curl -fsS -m 10 -o /dev/null http://127.0.0.1:8131/ai/v3/translate/languages || revert

for c in secai-docker-gateway secai-translation-gateway secai-translation-worker; do
    [ "$(prod_tag $c)" = "$NEW_TAG" ] || revert
done
cat > "$REL/RELEASE-NOTES.md" <<EOF
# Release main-$S8 ($(date -Is))
- Trước: $CUR_TAG  → Sau: $NEW_TAG, cả 3 container (8131, translation gateway/worker).
- Commit: $(git -C "$REPO" log --oneline "$CUR_S8..$TARGET" | wc -l) mới. pytest: $(tail -1 "$REL/pytest.log")
- Rollback: \`scripts/release-prod.sh rollback --yes\` (compose bak: $COMPOSE_BAK; profile cũ: $TRANS_PREV)
- Image cũ giữ ≥ ${KEEP_DAYS} ngày rồi \`release-prod.sh gc --yes\` xoá.
EOF
SECAI_RELEASES_DIR="$RELEASES" timeout 60 python3 -I "$(dirname "$0")/versions.py" record --service gateway --action release \
    --from "$CUR_TAG" --to "$NEW_TAG" --release-dir "$REL" || log "cảnh báo: không ghi được sổ phiên bản (versions.py record); release vẫn thành công"
log "RELEASE OK $NEW_TAG — ghi chú: $REL/RELEASE-NOTES.md"
gc || true
