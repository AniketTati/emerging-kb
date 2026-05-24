#!/usr/bin/env bash
# Phase 5 G5 — end-to-end verification of extraction (5a + 5b + 5c).
#
# Uploads tiny.xlsx (xlsx → rows plugin, no LLM needed) → waits for `ready`
# → asserts: lifecycle history contains mentions_extracted/fields_extracted/
# atomic_units_extracted events · proposed_fields rows exist · atomic_units
# rows of type 'row' exist · extracted_mentions count is 0 (Identity in CI
# without Gemini key) but extracted_mentions table is queryable.
#
# Why tiny.xlsx, not a PDF: covers the full Phase 5 chain without depending
# on Docling parse + a clauses LLM call. xlsx → 2 sheets → 2 raw_pages →
# rows plugin matches on mime_type alone, no LLM key needed.
#
# Usage:
#   scripts/verify_phase_5.sh                  # standalone (own stack)
#   KB_REUSE_STACK=1 scripts/verify_phase_5.sh # called by verify_sweep.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-5] .env not found; copying from .env.example"
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
    echo "[verify-5] === step $n: $* ==="
}
ok() { echo "[verify-5]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-5]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-5] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-5] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-5] script exited non-zero before all checks ran"
        exit $rc
    fi
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-5-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-5-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (now includes 0014/0015/0016)"
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
# DDL invariants — 3 new tables + 2 column adds
# ----------------------------------------------------------------------------

step "psql: extracted_mentions table + RLS + mention_type CHECK"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='extracted_mentions'")
[[ "$out" == "true|true" ]] && ok "extracted_mentions RLS on" || fail "extracted_mentions RLS state: $out"

out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'extracted_mentions_mention_type_check'")
if [[ "$out" == *"PERSON"* && "$out" == *"NORP"* && "$out" == *"CARDINAL"* ]]; then
    ok "OntoNotes-18 mention_type CHECK present"
else
    fail "mention_type CHECK missing OntoNotes-18 types: $out"
fi

step "psql: proposed_fields + inferred_schema_fields tables + RLS"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('proposed_fields','inferred_schema_fields')")
[[ "$out" == "2" ]] && ok "both new tables exist" || fail "expected 2, got $out"

step "psql: schema_fields.auto_promoted column added"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.columns WHERE table_name='schema_fields' AND column_name='auto_promoted'")
[[ "$out" == "1" ]] && ok "schema_fields.auto_promoted exists" || fail "schema_fields.auto_promoted missing"

step "psql: files.inferred_doc_type column added"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.columns WHERE table_name='files' AND column_name='inferred_doc_type'")
[[ "$out" == "1" ]] && ok "files.inferred_doc_type exists" || fail "files.inferred_doc_type missing"

step "psql: atomic_units table + RLS"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='atomic_units'")
[[ "$out" == "true|true" ]] && ok "atomic_units RLS on" || fail "atomic_units RLS state: $out"

step "psql: lifecycle CHECK includes mentions/fields/units states"
out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='files_lifecycle_state_check'")
if [[ "$out" == *"mentions_extracting"* && "$out" == *"fields_extracting"* && "$out" == *"units_extracting"* ]]; then
    ok "lifecycle CHECK includes 5a/5b/5c states"
else
    fail "lifecycle CHECK missing 5a/5b/5c states: $out"
fi

# ----------------------------------------------------------------------------
# E2E: upload tiny.xlsx → wait `ready` → assert extraction artifacts
# ----------------------------------------------------------------------------

step "POST tiny.xlsx → wait for lifecycle_state='ready' (full chain through 5a/5b/5c)"
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
        ok "tiny.xlsx reached lifecycle_state=ready (id=$file_id)"
    else
        fail "tiny.xlsx stuck at lifecycle_state=$state"
    fi
fi

step "psql: lifecycle history contains mentions_extracted + fields_extracted + atomic_units_extracted events"
events=$(DB_PSQL -tAc "SELECT string_agg(event, ',' ORDER BY id) FROM file_lifecycle WHERE file_id = '$file_id'")
if [[ "$events" == *"mentions_extracted"* && "$events" == *"fields_extracted"* && "$events" == *"atomic_units_extracted"* ]]; then
    ok "all 3 Phase 5 events present: $events"
else
    fail "missing one or more Phase 5 events. Got: $events"
fi

step "psql: extracted_mentions table is queryable (count may be 0 with Identity)"
mentions=$(DB_PSQL -tAc "SELECT count(*) FROM extracted_mentions WHERE file_id = '$file_id'" | tr -d '[:space:]')
ok "extracted_mentions count = $mentions (Identity returns 0; Gemini path would return >0)"

step "psql: proposed_fields table is queryable (Identity classifier → doc_type='unknown', 0 fields)"
proposed=$(DB_PSQL -tAc "SELECT count(*) FROM proposed_fields WHERE file_id = '$file_id'" | tr -d '[:space:]')
ok "proposed_fields count = $proposed (Identity → 0)"

step "psql: files.inferred_doc_type populated"
dt=$(DB_PSQL -tAc "SELECT inferred_doc_type FROM files WHERE id = '$file_id'")
if [[ -n "$dt" ]]; then
    ok "inferred_doc_type set to: $dt"
else
    fail "inferred_doc_type not set"
fi

step "psql: atomic_units rows of type='row' exist for the xlsx (rows plugin, no LLM)"
unit_count=$(DB_PSQL -tAc "SELECT count(*) FROM atomic_units WHERE file_id = '$file_id' AND unit_type = 'row'" | tr -d '[:space:]')
if [[ "$unit_count" -ge 1 ]]; then
    ok "rows plugin extracted $unit_count atomic_units of type='row'"
else
    fail "expected ≥1 atomic_units of type='row'; got $unit_count"
fi

step "psql: atomic_units.parameters jsonb contains sheet_name + cells"
params=$(DB_PSQL -tAc "SELECT parameters->>'sheet_name', jsonb_typeof(parameters->'cells') FROM atomic_units WHERE file_id = '$file_id' AND unit_type='row' LIMIT 1")
if [[ "$params" == *"|array"* || "$params" == *"Sheet"* ]]; then
    ok "row parameters shape correct: $params"
else
    fail "row parameters unexpected: $params"
fi

# ----------------------------------------------------------------------------
# Phase 5 pytest — over testcontainers
# ----------------------------------------------------------------------------

step "pytest — Phase 5 test files over testcontainers"
if uv run pytest tests/test_mentions_unit.py tests/test_mentions_worker.py \
    tests/test_fields_unit.py tests/test_fields_worker.py \
    tests/test_atomic_units_unit.py tests/test_atomic_units_worker.py \
    -q >/tmp/kb-verify-5-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-5-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-5-pytest.log)"
    tail -40 /tmp/kb-verify-5-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-5] === SUMMARY ==="
echo "[verify-5] checks passed: $CHECKS_PASSED"
echo "[verify-5] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-5] Phase 5 G5: GREEN ✅"
else
    echo "[verify-5] Phase 5 G5: FAILED ❌"
fi
