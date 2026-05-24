#!/usr/bin/env bash
# Phase 6 G5 — schema-driven extraction + lineage paths.
#
# Uploads tiny.xlsx (no LLM dependency for the chain through rows plugin) →
# waits for `ready` → asserts: extracted_entities table is queryable +
# `entities_extracting → ready` transition recorded in file_lifecycle +
# ltree extension installed + schema_entities_extracted event present.
#
# With KB_GEMINI_API_KEY unset (CI / Identity path) the extracted_entities
# table will have 0 rows for a doc-type with no matching schema — that's
# correct per decision #4.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-6] .env not found; copying from .env.example"
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
    echo "[verify-6] === step $n: $* ==="
}
ok() { echo "[verify-6]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-6]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-6] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-6] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-6] script exited non-zero before all checks ran"
        exit $rc
    fi
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-6-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-6-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (now includes 0017)"
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

step "psql: extracted_entities table + RLS"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='extracted_entities'")
[[ "$out" == "true|true" ]] && ok "extracted_entities RLS on" || fail "extracted_entities RLS state: $out"

step "psql: extracted_entities columns (fields jsonb, citations jsonb, lineage_path ltree)"
out=$(DB_PSQL -tAc "SELECT string_agg(column_name||':'||data_type, ',' ORDER BY column_name) FROM information_schema.columns WHERE table_name='extracted_entities' AND column_name IN ('fields','citations','lineage_path','parent_entity_id','schema_entity_id')")
if [[ "$out" == *"fields:jsonb"* && "$out" == *"citations:jsonb"* && "$out" == *"lineage_path:USER-DEFINED"* ]]; then
    ok "extracted_entities columns shape correct"
else
    fail "expected fields jsonb + citations jsonb + lineage_path ltree; got: $out"
fi

step "psql: ltree GiST index on lineage_path"
out=$(DB_PSQL -tAc "SELECT indexdef FROM pg_indexes WHERE indexname='extracted_entities_lineage_gist_idx'")
if [[ "$out" == *"USING gist"* && "$out" == *"lineage_path"* ]]; then
    ok "ltree GiST index present"
else
    fail "GiST index missing or wrong: $out"
fi

step "psql: lifecycle CHECK includes entities_extracting"
out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='files_lifecycle_state_check'")
if [[ "$out" == *"entities_extracting"* ]]; then
    ok "lifecycle CHECK includes entities_extracting"
else
    fail "lifecycle CHECK missing entities_extracting: $out"
fi

# ----------------------------------------------------------------------------
# E2E: tiny.xlsx through the full chain (5a → 5b → 5c → 6 → ready)
# ----------------------------------------------------------------------------

step "POST tiny.xlsx → wait for lifecycle_state='ready' (full chain through Phase 6)"
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
        ok "tiny.xlsx reached lifecycle_state=ready through Phase 6 (id=$file_id)"
    else
        fail "tiny.xlsx stuck at lifecycle_state=$state"
    fi
fi

step "psql: lifecycle history shows units_extracting→entities_extracting→ready"
events=$(DB_PSQL -tAc "SELECT string_agg(to_state, ',' ORDER BY created_at) FROM file_lifecycle WHERE file_id = '$file_id'")
if [[ "$events" == *"units_extracting,entities_extracting,ready"* ]]; then
    ok "Phase 6 transition chain observed"
else
    fail "unexpected lifecycle chain: $events"
fi

step "psql: schema_entities_extracted event recorded"
ev_count=$(DB_PSQL -tAc "SELECT count(*) FROM file_lifecycle WHERE file_id = '$file_id' AND event = 'schema_entities_extracted'" | tr -d '[:space:]')
if [[ "$ev_count" == "1" ]]; then
    ok "schema_entities_extracted event present (count=1)"
else
    fail "expected exactly 1 schema_entities_extracted event; got $ev_count"
fi

step "psql: extracted_entities table queryable (0 rows expected — no schema for xlsx doc-type)"
# CI without Gemini key: no auto-promoted schema for the xlsx file's
# inferred_doc_type → decision #4 no-op → 0 entities. Just check the query works.
ent_count=$(DB_PSQL -tAc "SELECT count(*) FROM extracted_entities WHERE file_id = '$file_id'" | tr -d '[:space:]')
ok "extracted_entities count = $ent_count (Identity path with no matching schema = 0)"

# ----------------------------------------------------------------------------
# Phase 6 pytest
# ----------------------------------------------------------------------------

step "pytest — Phase 6 test files over testcontainers"
if uv run pytest tests/test_entities_unit.py tests/test_lineage_unit.py tests/test_entities_worker.py \
    -q >/tmp/kb-verify-6-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-6-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-6-pytest.log)"
    tail -40 /tmp/kb-verify-6-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-6] === SUMMARY ==="
echo "[verify-6] checks passed: $CHECKS_PASSED"
echo "[verify-6] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-6] Phase 6 G5: GREEN ✅"
else
    echo "[verify-6] Phase 6 G5: FAILED ❌"
fi
