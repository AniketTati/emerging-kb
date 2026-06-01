#!/usr/bin/env bash
# Export the live demo state into a committable seed under demo-corpus/seed/.
#
#   ./scripts/seed_export.sh
#
# Produces:
#   demo-corpus/seed/kb_seed.dump  — pg_dump -Fc --data-only (knowledge only;
#                                    ephemeral/runtime tables excluded).
# Object blobs are NOT dumped — they're keyed by content sha and rebuilt from
# the committed source corpus at restore time (see bootstrap.sh / seed_minio.py).
#
# Restored on a fresh clone by scripts/bootstrap.sh (which calls seed_restore).
# The schema itself is NOT in the dump — migrations create it; this is data-only
# so it layers onto a freshly-migrated DB. Restore runs as the `kb` superuser,
# which bypasses RLS, with --disable-triggers so FK insert-order doesn't matter.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

DB_CONTAINER="${KB_DB_CONTAINER:-knowledgebaseservice-db-1}"
SEED_DIR="demo-corpus/seed"
mkdir -p "$SEED_DIR"

# Runtime / ephemeral tables: keep schema, drop rows. The knowledge tables
# (files, chunks, embeddings, entities, mentions, schemas, conflicts, chains,
# raptor, graph, …) are dumped in full.
EXCLUDES=(
  schema_migrations          # migrate ledger — owned by the migrate step, not seed data
  chat_sessions chat_turns query_log
  audit_log audit_log_2026_05 audit_log_2026_06 audit_queries
  eval_runs eval_run_results regression_set
  procrastinate_jobs procrastinate_events
  procrastinate_periodic_defers procrastinate_workers
  idempotency_keys
)
EXCLUDE_FLAGS=()
for t in "${EXCLUDES[@]}"; do EXCLUDE_FLAGS+=(--exclude-table-data="public.$t"); done

echo "[seed_export] pg_dump (data-only, custom format) -> $SEED_DIR/kb_seed.dump"
docker exec "$DB_CONTAINER" pg_dump -U kb -d kb -Fc --data-only --no-owner --no-privileges \
  "${EXCLUDE_FLAGS[@]}" > "$SEED_DIR/kb_seed.dump"

# NOTE: object blobs are NOT exported here. They are keyed by sha256(bytes), so
# bootstrap reconstructs them directly from the committed source corpus
# (demo-corpus/domains/finance) via scripts/seed_minio.py — no separate blob
# artifact, no dependence on MinIO's on-disk object format.
DUMP_SZ=$(du -h "$SEED_DIR/kb_seed.dump" | cut -f1)
echo "[seed_export] done: kb_seed.dump=$DUMP_SZ (blobs derive from source at restore time)"
