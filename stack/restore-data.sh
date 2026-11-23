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

# 1) Kiểm chứng trước khi đụng bất cứ thứ gì. Dòng SHA256SUMS phải đúng dạng `<64 hex>  <tệp>`; mỗi tệp cần có đúng một dòng như vậy.
[ -f "$PKG/SHA256SUMS" ] || die "missing SHA256SUMS in $PKG"
# awk: $1 dài 64 và toàn hex, tiếp theo đúng hai dấu cách, rồi tên tệp.
sums_ok='length($1)==64 && $1 !~ /[^0-9a-f]/ && substr($0,65,2)=="  " && length($0)>66'
if [ -s "$PKG/SHA256SUMS" ]; then
  bad=$(awk "!($sums_ok)" "$PKG/SHA256SUMS")
  [ -z "$bad" ] || die "SHA256SUMS has malformed line(s), nothing restored:
$bad"
  out=$(cd "$PKG" && sha256sum -c --strict SHA256SUMS 2>&1) || die "SHA256 verification failed, nothing restored:
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
  # -c --strict đã xác nhận mọi dòng đúng dạng và khớp (OK); ở đây chỉ cần tệp có dòng trong SHA256SUMS.
  awk -v f="$f" "($sums_ok) && substr(\$0,67)==f { ok=1 } END { exit !ok }" "$PKG/SHA256SUMS" || missing+=("$f (no valid SHA256 entry in SHA256SUMS)")
done
[ ${#missing[@]} -eq 0 ] || die "package incomplete, missing: ${missing[*]}"

# 2) Giải nén vào thư mục tạm cùng filesystem rồi mv: dở dang không để lại thư mục nửa vời; thư mục có sẵn thì giữ nguyên.
mkdir -p "$DATA_ROOT/_state/qdrant_storage" "$DATA_ROOT/_state/neo4j_data" "$DATA_ROOT/_state/neo4j_logs" "$DATA_ROOT/qdrant"
fresh=()
for a in "${ARCHIVES[@]}"; do
  if [ -e "$DATA_ROOT/$a" ]; then
    echo "==> $a: already present, skip"
    [ -n "$(ls -A "$DATA_ROOT/$a" 2>/dev/null)" ] || echo "WARNING: $DATA_ROOT/$a is empty (Docker may have auto-created it root-owned); remove it and rerun to restore $a" >&2
    continue
  fi
  tmp="$DATA_ROOT/.incoming.$a"
  rm -rf "$tmp"; mkdir "$tmp"
  echo "==> $a: extracting"
  zstd -dc "$PKG/$a.tar.zst" | tar -x --no-same-owner -C "$tmp"   # không giữ uid/gid của máy nguồn (kể cả khi chạy root)
  [ -d "$tmp/$a" ] || { rm -rf "$tmp"; die "$a.tar.zst has no top-level '$a/' directory"; }
  mv "$tmp/$a" "$DATA_ROOT/$a"; rmdir "$tmp"
  fresh+=("$a")
done
[ -d "$DATA_ROOT/poc/store" ] || { mkdir -p "$DATA_ROOT/poc/store"; fresh+=(poc-store-only); }

# Gateway (uid 10001, gid 999 + nhóm chung VM_SHARED_GID) ghi vào poc và poc/store; khớp quy tắc preflight của bootstrap.sh.
GW_UID=10001; GW_GID=999; SHARED_GID=${VM_SHARED_GID:-10100}
gateway_can_write() {  # đúng: chủ 10001, hoặc nhóm 999/shared có g+w, hoặc other-writable
  local uid gid mode
  read -r uid gid mode <<<"$(stat -L -c '%u %g %a' "$1")"
  [ "$uid" = "$GW_UID" ] && return 0
  { [ "$gid" = "$GW_GID" ] || [ "$gid" = "$SHARED_GID" ]; } && [ $(( 0$mode & 020 )) -ne 0 ] && return 0
  [ $(( 0$mode & 002 )) -ne 0 ]
}
fix_dirs=()   # chỉ sửa quyền thư mục do lần chạy này tạo; dữ liệu có sẵn không bị đổi chủ
for f in ${fresh[@]+"${fresh[@]}"}; do
  case "$f" in poc) fix_dirs=("$DATA_ROOT/poc") ;; poc-store-only) [ ${#fix_dirs[@]} -gt 0 ] || fix_dirs=("$DATA_ROOT/poc/store") ;; esac
done
for d in ${fix_dirs[@]+"${fix_dirs[@]}"}; do
  if [ "$(id -u)" = 0 ]; then
    chown -R "$GW_UID:$SHARED_GID" "$d"
    chmod -R u+rwX,g+rwX "$d"
  else
    chmod -R g+rwX "$d" 2>/dev/null || true
  fi
done
for d in "$DATA_ROOT/poc" "$DATA_ROOT/poc/store"; do
  gateway_can_write "$d" || echo "WARNING: $d is not writable by the gateway (uid $GW_UID); run: sudo chown -R $GW_UID:$SHARED_GID $d && sudo chmod -R g+rwX $d" >&2
done

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
