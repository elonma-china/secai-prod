#!/usr/bin/env bash
# Restore the SECAI datastores + flat-file data on this machine from what ./fetch-data.sh
# staged in ./data. Idempotent — safe to re-run.
#   1. extract the flat-file tarballs (bm25/exploitdb/poc-store/poc-sources)
#   2. offline-load neo4j.dump into a fresh neo4j volume (skipped if already loaded)
#   3. start qdrant + neo4j + redis
#   4. recover the Qdrant cve_kb collection from cve_kb.snapshot (skipped if already present)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f data/poc/catalog.db ]; then
  echo "[!] ./data is empty or incomplete — run ./fetch-data.sh first." >&2
  exit 1
fi

# neo4j bakes its password on FIRST init and compose interpolates ${NEO4J_PASSWORD} from ./.env.
# Without this, neo4j would boot with a blank password, crash-loop, and this script would still
# print DONE (step 3 only polled qdrant). Fail fast instead.
if [ ! -f .env ]; then
  echo "[!] .env not found — run: cp .env.example .env  and fill it in first." >&2
  exit 1
fi
# shellcheck disable=SC1091
NEO4J_PASSWORD="$(grep -E '^NEO4J_PASSWORD=' .env | tail -1 | cut -d= -f2- || true)"
if [ -z "${NEO4J_PASSWORD:-}" ] || [ "${NEO4J_PASSWORD}" = "change-me" ]; then
  echo "[!] NEO4J_PASSWORD is unset or still 'change-me' in .env — set a real password first." >&2
  echo "    (It is baked into neo4j on first init; changing it later needs wiping ./_state/neo4j_data.)" >&2
  exit 1
fi

echo "==> [1/4] extract flat-file tarballs"
mkdir -p data/bm25 data/exploitdb data/poc-store data/poc-sources
[ -f data/bm25.tar.zst ]                && tar --zstd -xf data/bm25.tar.zst                -C data/bm25
[ -f data/exploitdb.tar.zst ]           && tar --zstd -xf data/exploitdb.tar.zst           -C data/exploitdb
[ -f data/poc-store.tar.zst ]           && tar --zstd -xf data/poc-store.tar.zst           -C data/poc-store
[ -f data/poc-sources-runtime.tar.zst ] && tar --zstd -xf data/poc-sources-runtime.tar.zst -C data/poc-sources

# The gateway container runs as a non-root user (uid 10001), while these directories were
# extracted by whatever host user ran this script — open them up so that uid can read (and,
# for poc/, write the sqlite WAL sidecars it needs at query time).
chmod -R a+rX data/bm25 data/exploitdb data/poc-store data/poc-sources data/qdrant data/neo4j 2>/dev/null || true
chmod -R a+rwX data/poc

echo "==> [2/4] load neo4j dump (offline)"
mkdir -p _state/neo4j_data _state/neo4j_logs
if [ -f _state/neo4j_data/databases/neo4j/neostore ]; then
  echo "    neo4j store already exists → skip"
else
  docker run --rm \
    -v "$PWD/_state/neo4j_data:/data" \
    -v "$PWD/data/neo4j:/dump:ro" \
    --user root \
    neo4j:5.20-community \
    bash -c "neo4j-admin database load neo4j --from-path=/dump --overwrite-destination=true && chown -R 7474:7474 /data"
fi

echo "==> [3/4] start qdrant + neo4j + redis"
docker compose up -d qdrant neo4j redis

echo "    waiting for qdrant to be healthy..."
until curl -sf http://127.0.0.1:6333/readyz >/dev/null 2>&1; do sleep 2; done

echo "    waiting for neo4j to be healthy (catches a bad password / failed dump load early)..."
for i in $(seq 1 60); do
  cid="$(docker compose ps -q neo4j 2>/dev/null)"
  status="$([ -n "$cid" ] && docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo missing)"
  [ "$status" = "healthy" ] && break
  if [ "$i" = "60" ]; then
    echo "[!] neo4j did not become healthy. Check: docker compose logs neo4j" >&2
    echo "    (common cause: wrong NEO4J_PASSWORD, or the dump failed to load into ./_state/neo4j_data)" >&2
    exit 1
  fi
  sleep 3
done

echo "==> [4/4] recover qdrant collection cve_kb from snapshot"
if curl -sf http://127.0.0.1:6333/collections/cve_kb >/dev/null 2>&1; then
  echo "    collection cve_kb already present → skip recover"
else
  curl -sf -X PUT 'http://127.0.0.1:6333/collections/cve_kb/snapshots/recover' \
    -H 'Content-Type: application/json' \
    -d '{"location":"file:///qdrant/snapshots/cve_kb.snapshot"}' && echo
fi

echo
echo "DONE. Now run: docker compose up -d gateway"
echo "  Gateway : http://localhost:8131/health   (after 'docker compose up -d gateway')"
echo "  Qdrant  : http://127.0.0.1:6333 (loopback only) — collection cve_kb"
echo "  Neo4j   : bolt://neo4j:7687 (internal only) — neo4j / \$NEO4J_PASSWORD"
