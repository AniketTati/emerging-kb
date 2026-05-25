#!/usr/bin/env bash
# Phase 7 G5 — end-to-end verification of identity resolution.
#
# Uploads tiny.xlsx (no LLM needed for chain through rows plugin) → waits
# for `ready` → asserts: entities + mention_to_entity tables queryable +
# `entities_extracting → identity_resolving → ready` transition recorded +
# identities_resolved event present + HNSW index on entities.embedding.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-7] .env not found; copying from .env.example"
    cp .env.example .env
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE="docker compose"
WS_A="11111111-1111-1111-1111-111111111111"

DB_PSQL() {
    $COMPOSE exec -T db psql -U "${KB_POSTGRES_USER:-kb}" -d "${KB_POSTGRES_DB:-kb}" "$@"
}

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-7] === step $n: $* ==="
}
ok() { echo "[verify-7]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-7]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-7] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-7] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-7] script exited non-zero before all checks ran"
        exit $rc
    fi
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-7-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-7-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (now includes 0018)"
migrate_ok=0
for _ in $(seq 1 60); do
    raw=$($COMPOSE ps -a --format json migrate 2>/dev/null || echo '')
    parsed=$(python3 -c "
import sys, json
text = sys.stdin.read().strip()
if not text: print('', ''); sys.exit()
first = text.splitlines()[0]
try: data = json.loads(first)
except Exception: print('', ''); sys.exit()
print(data.get('State', ''), data.get('ExitCode', ''))
" <<<"$raw" 2>/dev/null || echo "")
    if [[ "$parsed" == "exited 0" ]]; then migrate_ok=1; break; fi
    sleep 2
done
(( migrate_ok == 1 )) && ok "migrate exited 0" || fail "migrate did not exit cleanly"

step "wait for api healthy"
for _ in $(seq 1 30); do
    h=$($COMPOSE ps --format json api 2>/dev/null | python3 -c "import sys,json
try:
    d=json.loads(sys.stdin.read() or '{}')
    print(d.get('Health',''))
except Exception: print('')" 2>/dev/null || echo "")
    if [[ "$h" == "healthy" ]]; then break; fi
    sleep 2
done
[[ "$h" == "healthy" ]] && ok "api healthy" || fail "api not healthy (state: $h)"
else
    ok "(reuse-stack) skipping compose build/up + migrate + api-healthy wait"
fi

# ----------------------------------------------------------------------------
# DDL invariants
# ----------------------------------------------------------------------------

step "psql: entities table + RLS"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='entities'")
[[ "$out" == "true|true" ]] && ok "entities RLS on" || fail "entities RLS state: $out"

step "psql: mention_to_entity table + RLS + resolved_method CHECK"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='mention_to_entity'")
[[ "$out" == "true|true" ]] && ok "mention_to_entity RLS on" || fail "mention_to_entity RLS state: $out"
out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'mention_to_entity_resolved_method_check'")
if [[ "$out" == *"deterministic"* && "$out" == *"embedding"* && "$out" == *"llm_judge"* && "$out" == *"identity"* ]]; then
    ok "resolved_method CHECK enforces 4 methods"
else
    fail "resolved_method CHECK missing one or more methods: $out"
fi

step "psql: UNIQUE index on (workspace_id, lower(canonical_name), entity_type)"
out=$(DB_PSQL -tAc "SELECT indexdef FROM pg_indexes WHERE indexname='entities_workspace_name_type_unique'")
if [[ "$out" == *"UNIQUE"* && "$out" == *"lower"* ]]; then
    ok "deterministic-match UNIQUE index present"
else
    fail "deterministic-match UNIQUE index missing or wrong: $out"
fi

step "psql: HNSW index on entities.embedding (halfvec_cosine_ops, partial)"
out=$(DB_PSQL -tAc "SELECT indexdef FROM pg_indexes WHERE indexname='entities_embedding_hnsw_idx'")
if [[ "$out" == *"USING hnsw"* && "$out" == *"halfvec_cosine_ops"* && "$out" == *"embedding IS NOT NULL"* ]]; then
    ok "HNSW partial index present with halfvec_cosine_ops"
else
    fail "HNSW index missing or wrong: $out"
fi

step "psql: lifecycle CHECK includes identity_resolving"
out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='files_lifecycle_state_check'")
if [[ "$out" == *"identity_resolving"* ]]; then
    ok "lifecycle CHECK includes identity_resolving"
else
    fail "lifecycle CHECK missing identity_resolving: $out"
fi

# ----------------------------------------------------------------------------
# E2E: tiny.xlsx through full chain to ready (through Phase 7)
# ----------------------------------------------------------------------------

step "POST tiny.xlsx → wait for lifecycle_state='ready' (full chain through Phase 7)"
upload_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
file_id=$(echo "$upload_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
if [[ -z "$file_id" ]]; then
    fail "POST /files did not return id: $upload_resp"
else
    for _ in $(seq 1 300); do
        state=$(curl -sS "http://localhost:8000/files/$file_id" -H "X-Test-Workspace: $WS_A" \
            | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
        if [[ "$state" == "ready" || "$state" == "failed" ]]; then break; fi
        sleep 2
    done
    if [[ "$state" == "ready" ]]; then
        ok "tiny.xlsx reached lifecycle_state=ready through Phase 7"
    else
        fail "tiny.xlsx stuck at lifecycle_state=$state"
    fi
fi

step "psql: lifecycle history shows entities_extracting→identity_resolving→ready"
events=$(DB_PSQL -tAc "SELECT string_agg(to_state, ',' ORDER BY created_at) FROM file_lifecycle WHERE file_id = '$file_id'")
if [[ "$events" == *"entities_extracting,identity_resolving,ready"* ]]; then
    ok "Phase 7 transition chain observed"
else
    fail "unexpected lifecycle chain: $events"
fi

step "psql: identities_resolved event recorded"
ev_count=$(DB_PSQL -tAc "SELECT count(*) FROM file_lifecycle WHERE file_id = '$file_id' AND event = 'identities_resolved'" | tr -d '[:space:]')
if [[ "$ev_count" == "1" ]]; then
    ok "identities_resolved event present (count=1)"
else
    fail "expected exactly 1 identities_resolved event; got $ev_count"
fi

step "psql: entities + mention_to_entity tables queryable (0 rows expected on xlsx — no mentions)"
ent_count=$(DB_PSQL -tAc "SELECT count(*) FROM entities WHERE workspace_id = '$WS_A'" | tr -d '[:space:]')
link_count=$(DB_PSQL -tAc "SELECT count(*) FROM mention_to_entity WHERE workspace_id = '$WS_A'" | tr -d '[:space:]')
ok "entities=$ent_count · mention_to_entity=$link_count (Identity path with no LLM keys + xlsx → no mentions = 0)"

# ----------------------------------------------------------------------------
# Stronger E2E: fabricate a mention + run identity resolution directly,
# verify the entity + link were written and lifecycle event fired.
# ----------------------------------------------------------------------------

step "fabricate: insert a file with extracted_mentions + force resolve via worker entrypoint"
# Use a fresh workspace for isolation from the prior tiny.xlsx test.
FAB_WS="55555555-5555-5555-5555-555555555555"
FAB_FID=$(uuidgen)
FAB_SHA=$(printf "%s" "fab-${FAB_FID}" | shasum -a 256 | awk '{print $1}')

# Seed file → raw_page → chunk → contextual_chunk → 2 mentions, all in 'identity_resolving' state.
DB_PSQL <<SQL >/dev/null
SELECT set_config('app.workspace_id', '${FAB_WS}', true);
INSERT INTO files (id, workspace_id, name, content_sha, object_key,
                   mime_type, size_bytes, lifecycle_state)
VALUES ('${FAB_FID}', '${FAB_WS}', 'fab.pdf', '${FAB_SHA}',
        'raw_files/${FAB_SHA}', 'application/pdf', 100, 'identity_resolving');
INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text,
                       layout_json, content_sha)
VALUES (gen_random_uuid(), '${FAB_FID}', '${FAB_WS}', 1, 'fab page', '{}'::jsonb, '${FAB_SHA}');
WITH c AS (
    INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text,
                        source_page_numbers, token_count, content_sha)
    VALUES (gen_random_uuid(), '${FAB_FID}', '${FAB_WS}', 0, 'fab', ARRAY[1], 5,
            substring('${FAB_SHA}fab' from 1 for 64))
    RETURNING id
), cc AS (
    INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id,
                                    contextual_prefix, contextual_text, model_id,
                                    prefix_token_count, cache_creation_input_tokens,
                                    cache_read_input_tokens)
    SELECT gen_random_uuid(), c.id, '${FAB_FID}', '${FAB_WS}', '', 'fab text',
           'identity', 0, 0, 0
    FROM c
    RETURNING id
)
INSERT INTO extracted_mentions (contextual_chunk_id, file_id, workspace_id,
                                 mention_text, mention_type, model_id)
SELECT cc.id, '${FAB_FID}', '${FAB_WS}', m.t, m.tp, 'identity'
FROM cc, (VALUES ('ACME Corp', 'ORG'), ('John Smith', 'PERSON')) AS m(t, tp);
SQL
ok "seeded file + 2 mentions in identity_resolving state"

step "run resolve_identities_file_impl via worker container python"
$COMPOSE exec -T worker python -c "
import asyncio, os
os.environ['KB_IDENTITY_JUDGE'] = 'identity'
from kb.config import get_settings
get_settings.cache_clear()
from kb.workers.tasks import resolve_identities_file_impl
asyncio.run(resolve_identities_file_impl('${FAB_FID}'))
print('OK')
" >/tmp/kb-verify-7-resolve.log 2>&1 && ok "resolve_identities_file_impl ran without error" || fail "resolver failed (see /tmp/kb-verify-7-resolve.log)"

step "psql: 2 entities created + 2 mention_to_entity rows + lifecycle event present"
ent_n=$(DB_PSQL -tAc "SELECT count(*) FROM entities WHERE workspace_id = '${FAB_WS}'" | tr -d '[:space:]')
link_n=$(DB_PSQL -tAc "SELECT count(*) FROM mention_to_entity WHERE workspace_id = '${FAB_WS}'" | tr -d '[:space:]')
state=$(DB_PSQL -tAc "SELECT lifecycle_state FROM files WHERE id = '${FAB_FID}'" | tr -d '[:space:]')
ev_n=$(DB_PSQL -tAc "SELECT count(*) FROM file_lifecycle WHERE file_id = '${FAB_FID}' AND event = 'identities_resolved'" | tr -d '[:space:]')
if [[ "$ent_n" == "2" && "$link_n" == "2" && "$state" == "ready" && "$ev_n" == "1" ]]; then
    ok "fab: 2 entities + 2 links + state=ready + 1 lifecycle event"
else
    fail "fab: entities=$ent_n links=$link_n state=$state events=$ev_n (expected 2/2/ready/1)"
fi

step "psql: cross-file deterministic match — second fab file reuses entities"
FAB_FID2=$(uuidgen)
FAB_SHA2=$(printf "%s" "fab2-${FAB_FID2}" | shasum -a 256 | awk '{print $1}')
DB_PSQL <<SQL >/dev/null
SELECT set_config('app.workspace_id', '${FAB_WS}', true);
INSERT INTO files (id, workspace_id, name, content_sha, object_key,
                   mime_type, size_bytes, lifecycle_state)
VALUES ('${FAB_FID2}', '${FAB_WS}', 'fab2.pdf', '${FAB_SHA2}',
        'raw_files/${FAB_SHA2}', 'application/pdf', 100, 'identity_resolving');
INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text,
                       layout_json, content_sha)
VALUES (gen_random_uuid(), '${FAB_FID2}', '${FAB_WS}', 1, 'fab2 page', '{}'::jsonb, '${FAB_SHA2}');
WITH c AS (
    INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text,
                        source_page_numbers, token_count, content_sha)
    VALUES (gen_random_uuid(), '${FAB_FID2}', '${FAB_WS}', 0, 'fab2', ARRAY[1], 5,
            substring('${FAB_SHA2}fab' from 1 for 64))
    RETURNING id
), cc AS (
    INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id,
                                    contextual_prefix, contextual_text, model_id,
                                    prefix_token_count, cache_creation_input_tokens,
                                    cache_read_input_tokens)
    SELECT gen_random_uuid(), c.id, '${FAB_FID2}', '${FAB_WS}', '', 'fab2 text',
           'identity', 0, 0, 0
    FROM c
    RETURNING id
)
INSERT INTO extracted_mentions (contextual_chunk_id, file_id, workspace_id,
                                 mention_text, mention_type, model_id)
SELECT cc.id, '${FAB_FID2}', '${FAB_WS}', 'acme corp', 'ORG', 'identity'
FROM cc;
SQL

$COMPOSE exec -T worker python -c "
import asyncio, os
os.environ['KB_IDENTITY_JUDGE'] = 'identity'
from kb.config import get_settings
get_settings.cache_clear()
from kb.workers.tasks import resolve_identities_file_impl
asyncio.run(resolve_identities_file_impl('${FAB_FID2}'))
print('OK')
" >/tmp/kb-verify-7-resolve2.log 2>&1 || fail "second resolver failed"

# After 2nd file, entities count should STILL be 2 (no new entity for "acme corp"
# since case-insensitive match against existing "ACME Corp" entity)
ent_n2=$(DB_PSQL -tAc "SELECT count(*) FROM entities WHERE workspace_id = '${FAB_WS}'" | tr -d '[:space:]')
mc=$(DB_PSQL -tAc "SELECT mention_count FROM entities WHERE workspace_id = '${FAB_WS}' AND lower(canonical_name) = 'acme corp'" | tr -d '[:space:]')
if [[ "$ent_n2" == "2" ]]; then
    ok "deterministic cross-file collapse: entities still 2 (Acme reused), mention_count for ACME=$mc"
else
    fail "expected entities=2 after cross-file collapse; got $ent_n2"
fi

# ----------------------------------------------------------------------------
# Phase 7 pytest
# ----------------------------------------------------------------------------

step "pytest — Phase 7 test files over testcontainers"
if uv run pytest tests/test_identity_unit.py tests/test_identity_worker.py \
    tests/test_entities_worker.py -q >/tmp/kb-verify-7-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-7-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-7-pytest.log)"
    tail -40 /tmp/kb-verify-7-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-7] === SUMMARY ==="
echo "[verify-7] checks passed: $CHECKS_PASSED"
echo "[verify-7] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-7] Phase 7 G5: GREEN ✅"
else
    echo "[verify-7] Phase 7 G5: FAILED ❌"
fi
