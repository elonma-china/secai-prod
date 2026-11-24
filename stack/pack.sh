#!/usr/bin/env bash
# Đóng gói SecAI để mang sang máy khác. Không bao giờ `docker push` (in lệnh để chủ dự án chạy tay).
#   pack.sh image [git-ref]                       build image gateway từ `git archive` (mặc định HEAD)
#   pack.sh data [--mcp-dir D] [--neo4j-url URL | --neo4j-export DIR] <src_root> <out_dir>
# Biến môi trường (data): QDRANT_URL (mặc định http://127.0.0.1:6333), QDRANT_COLLECTION (cve_kb_hybrid), QDRANT_API_KEY,
#   NEO4J_URL (= --neo4j-url, mặc định http://127.0.0.1:7474), NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE (chỉ qua env).
# Nén bằng zstd -T0 (bắt buộc; restore-data.sh cũng cần zstd).
# Neo4j: mặc định chạy stack/neo4j-export.py (Cypher chỉ đọc, không dừng Neo4j) vào <out_dir>/neo4j; số đếm lệch trước/sau
#   (đồ thị bị ghi trong lúc xuất) thì pack thất bại. Tránh 03:00–05:00 (autoscan ghi Neo4j). --neo4j-export DIR chép một
#   bản export có sẵn (cần counts.json, schema.cypher, ≥1 nodes-*.header.csv).
set -euo pipefail

usage() { echo "usage: $0 image [git-ref] | $0 data [--mcp-dir D] [--neo4j-url URL | --neo4j-export DIR] <src_root> <out_dir>" >&2; exit 2; }
die() { echo "pack: $*" >&2; exit 1; }
REPO=$(cd "$(dirname "$0")/.." && pwd)
. "$REPO/stack/lib/poc-sources-excludes.sh"
[ $# -ge 1 ] || usage
mode=$1; shift

pack_image() {
  local ref=${1:-HEAD} sha tag
  sha=$(git -C "$REPO" rev-parse --short=8 "$ref^{commit}") || die "unknown git ref: $ref"
  tag="ghcr.io/elonma-china/secai-agent-gateway:main-$sha-$(date +%Y%m%d)"   # "main" là tên lịch sử, sha là của ref được build
  ctx=$(mktemp -d); trap 'rm -rf "$ctx"' EXIT
  git -C "$REPO" archive "$sha" | tar -x -C "$ctx"
  docker build -t "$tag" "$ctx"      # Dockerfile gốc repo, builder cũ (không BuildKit)
  echo "$tag"
  echo "Chưa push. Khi chủ dự án đồng ý, chạy tay: docker push $tag"
}

# Xoá snapshot tạo trên máy Qdrant (có thể nhiều GB, có thể là PROD): chạy cả khi lỗi/INT/TERM. Cố gắng hết sức, cảnh báo nếu hỏng.
delete_snapshot() {
  [ -n "$SNAP_NAME" ] || return 0
  curl -fsS -X DELETE ${QHDR[@]+"${QHDR[@]}"} -o /dev/null "$QBASE/snapshots/$SNAP_NAME" \
    || echo "WARNING: could not delete server-side snapshot $SNAP_NAME; remove it manually: curl -X DELETE $QBASE/snapshots/$SNAP_NAME" >&2
  SNAP_NAME=""
}
on_exit() {
  local rc=$?
  trap - EXIT
  delete_snapshot
  rm -rf "$WORK"
  if [ "$rc" -ne 0 ] && [ -n "${OUT:-}" ]; then   # không để gói dở làm lần sau chết vì "out_dir not empty"
    if [ "$OUT_NEW" = 1 ]; then rm -rf "$OUT"; else find "$OUT" -mindepth 1 -delete; fi
  fi
  exit "$rc"
}
SNAP_NAME=""; OUT=""; OUT_NEW=0; WORK=""; QHDR=(); QBASE=""

pack_data() {
  local mcp="" nurl=${NEO4J_URL:-http://127.0.0.1:7474} nexp="" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --mcp-dir) [ $# -ge 2 ] || usage; mcp=$2; shift 2 ;;
      --neo4j-url) [ $# -ge 2 ] || usage; nurl=$2; shift 2 ;;
      --neo4j-export) [ $# -ge 2 ] || usage; nexp=$2; shift 2 ;;
      -*) usage ;;
      *) args+=("$1"); shift ;;
    esac
  done
  [ ${#args[@]} -eq 2 ] || usage
  local src=${args[0]} out=${args[1]}
  local url=${QDRANT_URL:-http://127.0.0.1:6333} coll=${QDRANT_COLLECTION:-cve_kb_hybrid}
  [ -n "$mcp" ] || mcp="$src/../exploitdb-mcp-server"

  # Kiểm nguồn trước khi tạo gì (không để lại gói dở).
  command -v zstd >/dev/null || die "zstd is required (apt install zstd)"
  local missing=() p
  for p in "$src/exploitdb" "$src/poc/catalog.db" "$src/poc/store" "$src/poc-sources" "$mcp"; do [ -e "$p" ] || missing+=("$p"); done
  if [ -n "$nexp" ]; then
    for p in "$nexp/counts.json" "$nexp/schema.cypher"; do [ -f "$p" ] || missing+=("$p"); done
    compgen -G "$nexp/nodes-*.header.csv" >/dev/null || missing+=("$nexp/nodes-*.header.csv")
  fi
  [ ${#missing[@]} -eq 0 ] || die "missing source: ${missing[*]}"
  src=$(cd "$src" && pwd)   # tar -C "$work" ... -C "$src": đường tương đối sẽ bị tính từ $work
  if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then die "out_dir not empty: $out"; fi
  [ ! -e "$out" ] && OUT_NEW=1 || OUT_NEW=0
  mkdir -p "$out/qdrant" "$out/neo4j"; OUT=$(cd "$out" && pwd); out=$OUT
  WORK=$(mktemp -d); work=$WORK
  QHDR=(); [ -z "${QDRANT_API_KEY:-}" ] || QHDR=(-H "api-key: $QDRANT_API_KEY")
  QBASE="$url/collections/$coll"; SNAP_NAME=""
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  # Neo4j trước Qdrant: export lệch số đếm thì dừng sớm, chưa tạo snapshot trên máy Qdrant.
  if [ -n "$nexp" ]; then
    echo "==> neo4j: copy export $nexp"
    cp -- "$nexp"/counts.json "$nexp"/schema.cypher "$nexp"/nodes-* "$out/neo4j/"
    ! compgen -G "$nexp/rels-*" >/dev/null || cp -- "$nexp"/rels-* "$out/neo4j/"
  else
    echo "==> neo4j: export from $nurl (read-only)"
    NEO4J_URL=$nurl python3 -I "$REPO/stack/neo4j-export.py" "$out/neo4j" || die "neo4j export failed (exit $?)"
  fi
  [ -f "$out/neo4j/counts.json" ] || die "neo4j/counts.json was not produced"
  local ntot
  ntot=$(python3 -I -c 'import json,sys; c=json.load(open(sys.argv[1])); print(sum(c["nodes"].values()), "nodes,", sum(c["relationships"].values()), "relationships (exported", c.get("exported_at", "?") + ")")' \
    "$out/neo4j/counts.json") || die "neo4j/counts.json is not valid"

  echo "==> qdrant: snapshot $coll via API ($url)"
  local name points resp
  resp=$(curl -fsS -X POST ${QHDR[@]+"${QHDR[@]}"} "$QBASE/snapshots") || die "qdrant: creating snapshot failed (a server-side snapshot may be orphaned; list: curl $QBASE/snapshots)"
  if ! name=$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])' 2>/dev/null); then
    echo "qdrant: unexpected create-snapshot response; a snapshot may be an orphan on the server. Existing snapshots:" >&2
    curl -fsS ${QHDR[@]+"${QHDR[@]}"} "$QBASE/snapshots" >&2 || true
    die "remove the orphan with: curl -X DELETE $QBASE/snapshots/<name>"
  fi
  SNAP_NAME=$name
  curl -fsS ${QHDR[@]+"${QHDR[@]}"} -o "$out/qdrant/$coll.snapshot" "$QBASE/snapshots/$name"
  delete_snapshot      # giải phóng đĩa máy Qdrant ngay khi tải xong
  points=$(curl -fsS ${QHDR[@]+"${QHDR[@]}"} "$QBASE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])' || echo "?")

  echo "==> tar.zst"
  tar -C "$src" --exclude=.git -cf - exploitdb | zstd -q -T0 -o "$out/exploitdb.tar.zst"
  local poc_x=(--exclude=.git --anchored) n   # tar: opcode order matters; .git unanchored (any depth) first, then skip names anchored to poc-sources/
  for n in "${POC_SOURCES_SKIP[@]}"; do poc_x+=("--exclude=poc-sources/$n"); done
  tar -C "$src" "${poc_x[@]}" -cf - poc-sources | zstd -q -T0 -o "$out/poc-sources.tar.zst"
  local skipped; skipped=$(find "$mcp" -name .git -prune -o -type f \( -name '.env*' -o -name '*.pem' -o -name '*.key' \) -print)
  if [ -n "$skipped" ]; then
    echo "WARNING: excluded from exploitdb-mcp-server.tar.zst (.env*/*.pem/*.key; .git is always excluded) - $(printf '%s\n' "$skipped" | wc -l) file(s):" >&2
    printf '%s\n' "$skipped" | head -20 >&2
  fi
  tar -C "$(dirname "$mcp")" --exclude=.git --exclude='.env*' --exclude='*.pem' --exclude='*.key' --transform "s,^$(basename "$mcp"),exploitdb-mcp-server," -cf - "$(basename "$mcp")" | zstd -q -T0 -o "$out/exploitdb-mcp-server.tar.zst"
  # catalog.db đang được ghi (WAL): lấy bản nhất quán bằng sqlite backup API, không chép tệp thô.
  mkdir -p "$work/poc"
  python3 -c 'import sqlite3,sys; s=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
    "$src/poc/catalog.db" "$work/poc/catalog.db"
  tar --exclude=.git -cf - -C "$work" poc/catalog.db -C "$src" poc/store | zstd -q -T0 -o "$out/poc.tar.zst"

  echo "==> MANIFEST.md + SHA256SUMS"
  {
    echo "# SecAI data package"
    echo
    echo "- created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "- source host: $(hostname)  (src_root: $src)"
    echo "- git sha: $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "- qdrant collection: $coll ($points points)"
    echo "- neo4j: $ntot"
    echo "- compression: zstd; restore with: DATA_ROOT=<dir> stack/restore-data.sh <this dir>"
    echo
    echo "| file | what | size |"
    echo "| --- | --- | --- |"
    for f in "qdrant/$coll.snapshot:Qdrant snapshot (recovered via API)" "neo4j:Neo4j CSV export (neo4j-admin database import full + schema.cypher)" \
             "exploitdb.tar.zst:ExploitDB tree" "poc.tar.zst:PoC catalog.db + store" "poc-sources.tar.zst:PoC sources (nuclei templates etc.)" \
             "exploitdb-mcp-server.tar.zst:exploitdb-mcp-server tree (restored to \$DATA_ROOT/exploitdb-mcp-server)"; do
      echo "| ${f%%:*} | ${f#*:} | $(du -sh "$out/${f%%:*}" | cut -f1) |"
    done
    echo
    echo "all trees omit .git at every depth; poc-sources.tar.zst also omits top-level (not read at runtime) ${POC_SOURCES_SKIP[*]}"
  } > "$out/MANIFEST.md"
  (cd "$out" && sha256sum qdrant/*.snapshot neo4j/* ./*.tar.zst MANIFEST.md | sed 's,  \./,  ,' > SHA256SUMS && sha256sum -c --quiet SHA256SUMS)
  echo "package ready: $out"
}

case "$mode" in
  image) pack_image "$@" ;;
  data) pack_data "$@" ;;
  *) usage ;;
esac
