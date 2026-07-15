#!/usr/bin/env bash
# Pack the RUNTIME-required slice of secai-data (~8G, per the packaging drop-list) and upload
# to the private secai1999/secai-data HF dataset. Needs `hf auth login` beforehand.
#
# DROP (build-only / regenerable, NOT shipped): _state/ (rebuilt by restore.sh from the
# snapshot+dump), poc-sources/{advisory-database,trickest-cve,PoC-in-GitHub} (build-only —
# their bytes are never read at serve time, only used to populate catalog.db/store),
# images/*.tar.gz (target pulls qdrant/neo4j straight from Docker Hub).
#
# $2 (CATALOG_STAGE) must point at a catalog.db+store REGENERATED with the current code
# (python -m secai_gateway.poc.build_catalog, no --reset needed if upserting onto an existing
# DB) — NOT the live production copy at $1/poc/catalog.db, which may be running an older
# schema/dedup state and is actively open by the running secai-gateway.service.
set -euo pipefail
SECAI_DATA="${1:-/home/ict/AIEngines/secai-data}"
CATALOG_STAGE="${2:-/home/ict/AIEngines/secai-deploy-build/poc-stage}"
STAGE=/home/ict/AIEngines/secai-deploy-build/hf-upload
REVISION="${SECAI_DATA_REVISION:-v1.0.0}"

# Preflight: the regenerated catalog stage must exist. Build it first with:
#   POC_CATALOG_DB=$CATALOG_STAGE/catalog.db POC_ARTIFACT_STORE=$CATALOG_STAGE/store \
#   POC_SOURCES_ROOT=<secai-data>/poc-sources EXPLOITDB_ROOT=<secai-data>/exploitdb \
#     python -m secai_gateway.poc.build_catalog
# (run against a COPY of the live catalog.db+store so the running gateway is never touched).
if [ ! -f "$CATALOG_STAGE/catalog.db" ] || [ ! -d "$CATALOG_STAGE/store" ]; then
  echo "[!] CATALOG_STAGE ($CATALOG_STAGE) has no catalog.db+store — regenerate it first (see comment above)." >&2
  exit 1
fi

rm -rf "$STAGE" && mkdir -p "$STAGE/poc" "$STAGE/qdrant" "$STAGE/neo4j" "$STAGE/bm25"

echo "==> staging qdrant snapshot + neo4j dump (ship as-is, already near-incompressible/compact)"
cp "$SECAI_DATA/qdrant/cve_kb.snapshot" "$STAGE/qdrant/cve_kb.snapshot"
cp "$SECAI_DATA/neo4j/neo4j.dump"       "$STAGE/neo4j/neo4j.dump"

echo "==> staging regenerated catalog.db (from $CATALOG_STAGE — NOT the live production copy)"
# Checkpoint the WAL into the main db first so the copied file is self-contained (defense in
# depth — SQLite checkpoints on last-connection-close, but an interrupted build could leave a -wal).
python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" "$CATALOG_STAGE/catalog.db"
cp "$CATALOG_STAGE/catalog.db" "$STAGE/poc/catalog.db"
tar --zstd -cf "$STAGE/poc-store.tar.zst" -C "$CATALOG_STAGE/store" .

echo "==> packing bm25 (preserve the bm25_cve_kb/ subdir name — BM25_DIR expects it nested)"
tar --zstd -cf "$STAGE/bm25.tar.zst" -C "$SECAI_DATA/bm25" bm25_cve_kb

echo "==> packing exploitdb"
tar --zstd -cf "$STAGE/exploitdb.tar.zst" -C "$SECAI_DATA/exploitdb" .

echo "==> packing poc-sources RUNTIME subset only (metasploit/nuclei/vulhub; no .git)"
tar --zstd --exclude='.git' -cf "$STAGE/poc-sources-runtime.tar.zst" \
  -C "$SECAI_DATA/poc-sources" metasploit-framework nuclei-templates vulhub

echo "==> sizes"
du -sh "$STAGE"/* 2>/dev/null

echo "==> uploading to HF dataset secai1999/secai-data (revision $REVISION)"
hf upload secai1999/secai-data "$STAGE" --repo-type dataset --revision "$REVISION"
echo "DONE."
