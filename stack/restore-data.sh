#!/usr/bin/env bash
# Nạp gói dữ liệu SecAI (do `pack.sh data` tạo) vào $DATA_ROOT. Idempotent, không xoá/ghi đè dữ liệu có sẵn.
#   DATA_ROOT=/srv/secai-data ./restore-data.sh [--qdrant-url http://127.0.0.1:6333] <pkg_dir>
# Thứ tự: (1) kiểm SHA256SUMS + đủ tệp, chưa đụng DATA_ROOT; (2) giải nén thư mục còn thiếu; (3) nạp dump Neo4j
# nếu store chưa có; (4) khôi phục snapshot Qdrant (qua API khi có --qdrant-url, nếu không in lệnh).
# Cần: zstd, sha256sum, docker (chỉ bước Neo4j), curl (chỉ khi --qdrant-url). Không in secret.
set -euo pipefail

usage() { echo "usage: DATA_ROOT=<dir> $0 [--qdrant-url URL] <pkg_dir>" >&2; exit 2; }
die() { echo "restore-data: $*" >&2; exit 1; }

QDRANT_URL=""
PKG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --qdrant-url) [ $# -ge 2 ] || usage; QDRANT_URL=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [ -z "$PKG" ] || usage; PKG=$1; shift ;;
  esac
done
[ -n "$PKG" ] || usage
[ -n "${DATA_ROOT:-}" ] || die "set DATA_ROOT"
[ -d "$PKG" ] || die "pkg_dir not found: $PKG"
PKG=$(cd "$PKG" && pwd)
NEO4J_IMAGE=${NEO4J_IMAGE:-neo4j:5.20-community}
ARCHIVES=(exploitdb poc poc-sources exploitdb-mcp-server)   # mỗi tarball chứa thư mục cấp 1 cùng tên

command -v zstd >/dev/null || die "zstd is required (apt install zstd)"

# 1) Kiểm chứng trước khi đụng bất cứ thứ gì.
[ -f "$PKG/SHA256SUMS" ] || die "missing SHA256SUMS in $PKG"
if [ -s "$PKG/SHA256SUMS" ]; then
  out=$(cd "$PKG" && sha256sum -c SHA256SUMS 2>&1) || die "SHA256 verification failed, nothing restored:
$(printf '%s\n' "$out" | grep -v ': OK$')"
fi
shopt -s nullglob
snaps=("$PKG"/qdrant/*.snapshot)
need=("neo4j/neo4j.dump")
for a in "${ARCHIVES[@]}"; do need+=("$a.tar.zst"); done
missing=()
[ ${#snaps[@]} -gt 0 ] || missing+=("qdrant/<collection>.snapshot")
for s in "${snaps[@]}"; do need+=("qdrant/$(basename "$s")"); done
for f in "${need[@]}"; do
  [ -f "$PKG/$f" ] || { missing+=("$f"); continue; }
  grep -qF "  $f" "$PKG/SHA256SUMS" || missing+=("$f (not listed in SHA256SUMS)")
done
[ ${#missing[@]} -eq 0 ] || die "package incomplete, missing: ${missing[*]}"

# 2) Giải nén vào thư mục tạm cùng filesystem rồi mv: dở dang không để lại thư mục nửa vời; thư mục có sẵn thì giữ nguyên.
mkdir -p "$DATA_ROOT/_state/qdrant_storage" "$DATA_ROOT/_state/neo4j_data" "$DATA_ROOT/_state/neo4j_logs" "$DATA_ROOT/qdrant"
for a in "${ARCHIVES[@]}"; do
  if [ -e "$DATA_ROOT/$a" ]; then echo "==> $a: already present, skip"; continue; fi
  tmp="$DATA_ROOT/.incoming.$a"
  rm -rf "$tmp"; mkdir "$tmp"
  echo "==> $a: extracting"
  zstd -dc "$PKG/$a.tar.zst" | tar -x -C "$tmp"
  [ -d "$tmp/$a" ] || { rm -rf "$tmp"; die "$a.tar.zst has no top-level '$a/' directory"; }
  mv "$tmp/$a" "$DATA_ROOT/$a"; rmdir "$tmp"
done
mkdir -p "$DATA_ROOT/poc/store"

# 3) Neo4j: chỉ nạp khi store chưa có (nạp đè sẽ mất dữ liệu).
if [ -f "$DATA_ROOT/_state/neo4j_data/databases/neo4j/neostore" ]; then
  echo "==> neo4j: store already exists, skip load"
else
  echo "==> neo4j: loading dump (offline, throwaway container)"
  docker run --rm --entrypoint bash -v "$DATA_ROOT/_state/neo4j_data:/data" -v "$PKG/neo4j:/dump:ro" "$NEO4J_IMAGE" \
    -c "neo4j-admin database load neo4j --from-path=/dump --overwrite-destination=true && chown -R 7474:7474 /data"
fi

# 4) Qdrant: snapshot nằm trong $DATA_ROOT/qdrant (compose mount vào /qdrant/snapshots).
for s in "${snaps[@]}"; do
  name=$(basename "$s" .snapshot)
  [ -e "$DATA_ROOT/qdrant/$name.snapshot" ] || cp "$s" "$DATA_ROOT/qdrant/$name.snapshot"
  cmd=(curl -fsS -X PUT "${QDRANT_URL:-http://127.0.0.1:6333}/collections/$name/snapshots/recover" -H 'Content-Type: application/json'
       -d "{\"location\":\"file:///qdrant/snapshots/$name.snapshot\"}")
  if [ -z "$QDRANT_URL" ]; then
    echo "==> qdrant: after the stack is up, recover with (add -H 'api-key: ...' if Qdrant has a key):"
    echo "    curl -fsS -X PUT 'http://127.0.0.1:6333/collections/$name/snapshots/recover' -H 'Content-Type: application/json' -d '{\"location\":\"file:///qdrant/snapshots/$name.snapshot\"}'"
  elif curl -fsS -o /dev/null ${QDRANT_API_KEY:+-H "api-key: $QDRANT_API_KEY"} "$QDRANT_URL/collections/$name" 2>/dev/null; then
    echo "==> qdrant: collection $name already exists, skip recover"
  else
    echo "==> qdrant: recovering $name"
    "${cmd[@]}" ${QDRANT_API_KEY:+-H "api-key: $QDRANT_API_KEY"} >/dev/null
  fi
done
echo "DONE. DATA_ROOT=$DATA_ROOT"
