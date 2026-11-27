#!/usr/bin/env bash
# Nạp gói dữ liệu SecAI (do `pack.sh data` tạo) vào $DATA_ROOT. Idempotent, không xoá/ghi đè dữ liệu có sẵn.
#   DATA_ROOT=/srv/secai-data ./restore-data.sh [--qdrant-url http://127.0.0.1:6333] <pkg_dir>
# Thứ tự: (1) kiểm SHA256SUMS + đủ tệp + mật khẩu Neo4j, chưa đụng DATA_ROOT; (2) giải nén thư mục còn thiếu;
# (3) Neo4j, chỉ khi store chưa có: neo4j-admin import CSV export, chạy container tạm secai-restore-neo4j-<pid> áp
# schema.cypher, so số đếm với counts.json; marker .secai-restore-started/.secai-restore-complete chặn chạy lại trên
# lần restore dở; (4) khôi phục snapshot Qdrant (qua API khi có --qdrant-url, nếu không in lệnh).
# NEO4J_PASSWORD: env, nếu không có thì dòng NEO4J_PASSWORD= cuối cùng trong .env cạnh script (mật khẩu stack sẽ dùng).
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
need=(neo4j/counts.json neo4j/schema.cypher)
nlabels=(); ntypes=()
for h in "$PKG"/neo4j/nodes-*.header.csv; do n=${h##*/nodes-}; nlabels+=("${n%.header.csv}"); done
for h in "$PKG"/neo4j/rels-*.header.csv; do n=${h##*/rels-}; ntypes+=("${n%.header.csv}"); done
[ ${#nlabels[@]} -gt 0 ] || need+=("neo4j/nodes-<Label>.header.csv")
for n in ${nlabels[@]+"${nlabels[@]}"}; do need+=("neo4j/nodes-$n.header.csv" "neo4j/nodes-$n.csv.gz"); done
for n in ${ntypes[@]+"${ntypes[@]}"}; do need+=("neo4j/rels-$n.header.csv" "neo4j/rels-$n.csv.gz"); done
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

ND="$DATA_ROOT/_state/neo4j_data"
if [ -e "$ND/.secai-restore-started" ] && [ ! -e "$ND/.secai-restore-complete" ]; then
  die "incomplete previous Neo4j restore: remove $ND and rerun"
fi
NEO4J_LOAD=0
if [ ! -f "$ND/databases/neo4j/neostore" ]; then
  NEO4J_LOAD=1
  if [ -z "${NEO4J_PASSWORD:-}" ] && [ -f "$(dirname "$0")/.env" ]; then
    NEO4J_PASSWORD=$(sed -n 's/^NEO4J_PASSWORD=//p' "$(dirname "$0")/.env" | tail -1)
    NEO4J_PASSWORD=${NEO4J_PASSWORD%$'\r'}
    case $NEO4J_PASSWORD in \"*\"|\'*\') NEO4J_PASSWORD=${NEO4J_PASSWORD:1:-1} ;; esac   # compose chỉ bỏ cặp nháy bao ngoài
  fi
  [ -n "${NEO4J_PASSWORD:-}" ] || die "NEO4J_PASSWORD is empty (set it in env or in $(dirname "$0")/.env); needed to restore Neo4j"
  export NEO4J_PASSWORD
fi

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
if [ "$NEO4J_LOAD" = 0 ]; then
  echo "==> neo4j: store already exists, skip load"
else
  touch "$ND/.secai-restore-started"
  imp=()
  for n in "${nlabels[@]}"; do imp+=("--nodes=$n=/import/nodes-$n.header.csv,/import/nodes-$n.csv.gz"); done
  for n in ${ntypes[@]+"${ntypes[@]}"}; do imp+=("--relationships=$n=/import/rels-$n.header.csv,/import/rels-$n.csv.gz"); done
  echo "==> neo4j: importing CSV export (offline, throwaway container)"
  # Tên tệp đi qua "$@" của bash -c, không ghép vào chuỗi lệnh.
  docker run --rm --entrypoint bash -v "$ND:/data" -v "$PKG/neo4j:/import:ro" "$NEO4J_IMAGE" -c \
    'neo4j-admin database import full neo4j --id-type=integer --multiline-fields=true "$@" && chown -R 7474:7474 /data' \
    bash "${imp[@]}"

  cname="secai-restore-neo4j-$$"
  trap 'docker rm -f "$cname" >/dev/null 2>&1 || true' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  echo "==> neo4j: applying schema + verifying counts ($cname)"
  NEO4J_AUTH="neo4j/$NEO4J_PASSWORD" docker run -d --name "$cname" -e NEO4J_AUTH -v "$ND:/data" \
    -v "$PKG/neo4j/schema.cypher:/schema.cypher:ro" "$NEO4J_IMAGE" >/dev/null
  # LANG: không có thì cypher-shell (Java 17, locale POSIX) đọc schema.cypher thành ASCII, hỏng ký tự ngoài ASCII.
  cy() { docker exec -e NEO4J_PASSWORD -e LANG=C.UTF-8 "$cname" cypher-shell -u neo4j "$@"; }
  deadline=$((SECONDS + 300))
  until cy 'RETURN 1' >/dev/null 2>&1; do
    up=$(docker inspect -f '{{.State.Running}}' "$cname" 2>/dev/null || true)
    if [ "$up" != true ] || [ "$SECONDS" -ge "$deadline" ]; then
      docker logs --tail 30 "$cname" >&2 || true
      die "neo4j: $cname not ready (running=$up, waited $((SECONDS + 300 - deadline)) s of 300)"
    fi
    sleep 2
  done
  cy -f /schema.cypher
  cy 'CALL db.awaitIndexes(900)'
  got_nodes=$(cy --format plain 'MATCH (n) RETURN labels(n)[0] AS k, count(*) AS c')
  got_rels=$(cy --format plain 'MATCH ()-[r]->() RETURN type(r) AS k, count(*) AS c')
  # In "N M" khi khớp; lệch thì in từng dòng khác biệt ra stderr và thoát 1.
  totals=$(python3 -I -c '
import csv, io, json, sys
want = json.load(open(sys.argv[1]))
def parse(t):
    rows = list(csv.reader(io.StringIO(t), skipinitialspace=True))[1:]
    return {r[0]: int(r[1]) for r in rows if r}
diff = []
for kind, text in (("nodes", sys.argv[2]), ("relationships", sys.argv[3])):
    w, g = want[kind], parse(text)
    diff += [f"{kind} {k}: expected {w.get(k, 0)}, restored {g.get(k, 0)}" for k in sorted(set(w) | set(g)) if w.get(k, 0) != g.get(k, 0)]
if diff:
    sys.exit("\n".join(diff))
print(sum(want["nodes"].values()), sum(want["relationships"].values()))
' "$PKG/neo4j/counts.json" "$got_nodes" "$got_rels") || die "neo4j: counts differ from counts.json; remove $ND and rerun"
  echo "==> neo4j: counts match (${totals% *} nodes, ${totals#* } relationships)"
  docker exec "$cname" touch /data/.secai-restore-complete   # /data đã thuộc uid 7474: ghi marker từ trong container
  docker stop -t 120 "$cname" >/dev/null
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
