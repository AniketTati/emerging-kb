#!/usr/bin/env bash
# Wipe + reprocess all ingested files through the new pipeline.
#
# Use after a schema or chunker change that requires re-running every
# downstream phase (chunk → contextualize → embed → raptor → mentions →
# kv_tables → schema_entities → identities → triples → graph). Keeps
# raw_pages intact so we don't have to re-parse from raw bytes.
#
# Run from the repo root:
#   bash scripts/wipe_and_reprocess.sh
#
# Requires:
#   - docker compose db service running (kb @ port 5432)
#   - procrastinate worker running (scripts/dev_worker.sh)
#   - dev_env.sh sourced or KB_DATABASE_URL set

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ -z "${KB_POSTGRES_HOST:-}" ]]; then
    # shellcheck disable=SC1091
    source ./scripts/dev_env.sh
fi

echo "=== WIPE: truncating downstream tables ==="
docker exec -i knowledgebaseservice-db-1 psql -U kb -d kb << 'SQL'
-- Downstream of chunking. CASCADE so FKs unwind cleanly.
TRUNCATE chunks CASCADE;
TRUNCATE canonical_entities CASCADE;
TRUNCATE extracted_entities CASCADE;
DELETE FROM schema_fields WHERE auto_promoted = TRUE;
DELETE FROM schema_entities WHERE schema_id IN (
    SELECT id FROM schemas WHERE name LIKE 'auto:%'
);
DELETE FROM schema_relationships WHERE schema_id IN (
    SELECT id FROM schemas WHERE name LIKE 'auto:%'
);
DELETE FROM schemas WHERE name LIKE 'auto:%';
DELETE FROM proposed_fields;
DELETE FROM inferred_schema_fields;
-- Roll every non-deleted file back to 'parsed' so chunk_file_impl
-- picks them up. raw_pages stays — chunker reads from it. Skip
-- source_authority + doc_status — both are NOT NULL on the files
-- table after Wave A; preserving them across the wipe is harmless
-- since the source-authority + doc-status workers will overwrite if
-- needed.
UPDATE files
   SET lifecycle_state = 'parsed',
       inferred_doc_type = NULL
 WHERE lifecycle_state NOT IN ('deleted', 'failed', 'parsed');
SQL

echo "=== STATUS after wipe ==="
docker exec knowledgebaseservice-db-1 psql -U kb -d kb -c "
SELECT 'files (parsed)' AS metric, count(*)::text AS value FROM files WHERE lifecycle_state='parsed'
UNION ALL SELECT 'raw_pages', count(*)::text FROM raw_pages
UNION ALL SELECT 'chunks', count(*)::text FROM chunks
UNION ALL SELECT 'extracted_entities', count(*)::text FROM extracted_entities
UNION ALL SELECT 'canonical_entities', count(*)::text FROM canonical_entities
ORDER BY 1
"

echo
echo "=== REPROCESS: deferring chunk_file for every parsed file ==="
uv run python <<'PY'
import asyncio, os
import psycopg
from kb.workers.tasks import procrastinate_app

async def main():
    db_url = os.environ["KB_DATABASE_URL"]
    async with procrastinate_app.open_async():
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            cur = await conn.execute(
                "SELECT id::text FROM files WHERE lifecycle_state='parsed'",
            )
            file_ids = [r[0] for r in await cur.fetchall()]
        for fid in file_ids:
            await procrastinate_app.configure_task(
                name="chunk_file",
            ).defer_async(file_id=fid)
        print(f"  → deferred chunk_file for {len(file_ids)} files")

asyncio.run(main())
PY

echo
echo "=== DONE — watch worker log for progress; chain will run to 'ready' ==="
echo "  tail -f /tmp/worker.log"
