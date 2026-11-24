#!/usr/bin/env bash
# Đóng gói SecAI để mang sang máy khác. Không bao giờ `docker push` (in lệnh để chủ dự án chạy tay).
#   pack.sh image [git-ref]                       build image gateway từ `git archive` (mặc định HEAD)
#   pack.sh data [--mcp-dir D] [--neo4j-dir D | --neo4j-dump F] <src_root> <out_dir>
# Biến môi trường (data): QDRANT_URL (mặc định http://127.0.0.1:6333), QDRANT_COLLECTION (cve_kb_hybrid), QDRANT_API_KEY,
#   TMPDIR (nơi chép store Neo4j), NEO4J_STORE_DIR (= --neo4j-dir), NEO4J_IMAGE (neo4j:5.20-community).
# Nén bằng zstd -T0 (bắt buộc; restore-data.sh cũng cần zstd).
# Neo4j: --neo4j-dir là thư mục /data của Neo4j (có databases/, transactions/); script chép bản sao rồi dump trong container tạm,
#   không dừng/đụng Neo4j đang chạy. Chép từ store đang ghi có thể không nhất quán: dùng bản sao tĩnh hoặc store đã dừng.
#   Không truyền thì dùng <src_root>/neo4j/neo4j.dump nếu có (hoặc --neo4j-dump).
set -euo pipefail

usage() { echo "usage: $0 image [git-ref] | $0 data [--mcp-dir D] [--neo4j-dir D | --neo4j-dump F] <src_root> <out_dir>" >&2; exit 2; }
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
  local mcp="" ndir=${NEO4J_STORE_DIR:-} ndump="" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --mcp-dir) [ $# -ge 2 ] || usage; mcp=$2; shift 2 ;;
      --neo4j-dir) [ $# -ge 2 ] || usage; ndir=$2; shift 2 ;;
      --neo4j-dump) [ $# -ge 2 ] || usage; ndump=$2; shift 2 ;;
      -*) usage ;;
      *) args+=("$1"); shift ;;
    esac
  done
  [ ${#args[@]} -eq 2 ] || usage
  local src=${args[0]} out=${args[1]}
  local url=${QDRANT_URL:-http://127.0.0.1:6333} coll=${QDRANT_COLLECTION:-cve_kb_hybrid}
  local image=${NEO4J_IMAGE:-neo4j:5.20-community}
  [ -n "$mcp" ] || mcp="$src/../exploitdb-mcp-server"
  [ -n "$ndump" ] || [ -n "$ndir" ] || ndump="$src/neo4j/neo4j.dump"

  # Kiểm nguồn trước khi tạo gì (không để lại gói dở).
  command -v zstd >/dev/null || die "zstd is required (apt install zstd)"
  local missing=() p
  for p in "$src/exploitdb" "$src/poc/catalog.db" "$src/poc/store" "$src/poc-sources" "$mcp"; do [ -e "$p" ] || missing+=("$p"); done
  if [ -n "$ndir" ]; then [ -d "$ndir/databases" ] || missing+=("$ndir/databases"); else [ -f "$ndump" ] || missing+=("$ndump"); fi
  [ ${#missing[@]} -eq 0 ] || die "missing source: ${missing[*]}"
  src=$(cd "$src" && pwd)   # tar -C "$work" ... -C "$src": đường tương đối sẽ bị tính từ $work
  if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then die "out_dir not empty: $out"; fi
  [ ! -e "$out" ] && OUT_NEW=1 || OUT_NEW=0
  mkdir -p "$out/qdrant" "$out/neo4j"; OUT=$(cd "$out" && pwd); out=$OUT
  WORK=$(mktemp -d); work=$WORK     # mktemp theo TMPDIR; bản sao store Neo4j nằm ở đây
  QHDR=(); [ -z "${QDRANT_API_KEY:-}" ] || QHDR=(-H "api-key: $QDRANT_API_KEY")
  QBASE="$url/collections/$coll"; SNAP_NAME=""
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

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

  echo "==> neo4j: dump"
  if [ -n "$ndir" ]; then
    echo "WARNING: copying Neo4j store $ndir; if Neo4j is running on it the copy may be inconsistent (use a stopped copy or --neo4j-dump). Copy goes to \${TMPDIR:-/tmp}." >&2
    mkdir -p "$work/data"; cp -r "$ndir/databases" "$work/data/"; [ ! -d "$ndir/transactions" ] || cp -r "$ndir/transactions" "$work/data/"
    # --entrypoint bash: chạy root thật (entrypoint mặc định hạ quyền xuống neo4j); chown về user hiện tại để dọn được tạm.
    docker run --rm --entrypoint bash -v "$work/data:/data" -v "$out/neo4j:/dump" "$image" \
      -c "neo4j-admin database dump neo4j --to-path=/dump >/dev/null; rc=\$?; chown -R $(id -u):$(id -g) /dump /data; exit \$rc"
  else
    cp "$ndump" "$out/neo4j/neo4j.dump"
  fi
  [ -s "$out/neo4j/neo4j.dump" ] || die "neo4j.dump was not produced"

  echo "==> tar.zst"
  tar -C "$src" -cf - exploitdb | zstd -q -T0 -o "$out/exploitdb.tar.zst"
  local poc_x=(--no-anchored --exclude=.git --anchored) n
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
  tar -cf - -C "$work" poc/catalog.db -C "$src" poc/store | zstd -q -T0 -o "$out/poc.tar.zst"

  echo "==> MANIFEST.md + SHA256SUMS"
  {
    echo "# SecAI data package"
    echo
    echo "- created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "- source host: $(hostname)  (src_root: $src)"
    echo "- git sha: $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "- qdrant collection: $coll ($points points)"
    echo "- compression: zstd; restore with: DATA_ROOT=<dir> stack/restore-data.sh <this dir>"
    echo
    echo "| file | what | size |"
    echo "| --- | --- | --- |"
    for f in "qdrant/$coll.snapshot:Qdrant snapshot (recovered via API)" "neo4j/neo4j.dump:Neo4j database dump (neo4j-admin database load)" \
             "exploitdb.tar.zst:ExploitDB tree" "poc.tar.zst:PoC catalog.db + store" "poc-sources.tar.zst:PoC sources (nuclei templates etc.)" \
             "exploitdb-mcp-server.tar.zst:exploitdb-mcp-server tree (restored to \$DATA_ROOT/exploitdb-mcp-server)"; do
      echo "| ${f%%:*} | ${f#*:} | $(du -h "$out/${f%%:*}" | cut -f1) |"
    done
    echo
    echo "poc-sources.tar.zst omits (not read at runtime): .git at every depth; top-level ${POC_SOURCES_SKIP[*]}"
  } > "$out/MANIFEST.md"
  (cd "$out" && sha256sum qdrant/*.snapshot neo4j/neo4j.dump ./*.tar.zst MANIFEST.md | sed 's,  \./,  ,' > SHA256SUMS && sha256sum -c --quiet SHA256SUMS)
  echo "package ready: $out"
}

case "$mode" in
  image) pack_image "$@" ;;
  data) pack_data "$@" ;;
  *) usage ;;
esac
