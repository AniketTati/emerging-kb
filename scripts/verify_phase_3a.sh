#!/usr/bin/env bash
# Phase 3a G5 — end-to-end verification.
#
# Two stacks (same pattern as 0/1a/1b/1c/2a/2b):
#   1. docker-compose smoke — confirms 0009_chunks.sql applied, chained
#      chunk_file task ran, lifecycle reached 'chunked' for all 3 mimes.
#   2. pytest over testcontainers (Phase 3a test files only).
#
# Phase 3a's added surface: ALTER files CHECK + CREATE TABLE chunks + RLS
# + chunk_file Procrastinate task + chained-defer from parse_file's success
# path. NO new HTTP endpoints (lifecycle_state enum widens only).

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-3a] .env not found; copying from .env.example"
    cp .env.example .env
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE="docker compose"
DB_PSQL() {
    $COMPOSE exec -T db psql -U "${KB_POSTGRES_USER:-kb}" -d "${KB_POSTGRES_DB:-kb}" "$@"
}

WS_A="11111111-1111-1111-1111-111111111111"

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-3a] === step $n: $* ==="
}

ok() {
    echo "[verify-3a]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-3a]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-3a] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-3a] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-3a] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose
# ----------------------------------------------------------------------------

step "compose build + up"
$COMPOSE build >/tmp/kb-verify-3a-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-3a-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0"
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
(( migrate_ok == 1 )) && ok "migrate exited 0" || fail "migrate did not exit cleanly within 120s"

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

# ---------------------------------------------------------------------------
# DDL invariants — 0009_chunks.sql applied
# ---------------------------------------------------------------------------

step "psql: chunks table exists with workspace_id + RLS forced"
exists=$(DB_PSQL -tA -c "SELECT relrowsecurity::text || '|' || relforcerowsecurity::text FROM pg_class WHERE relname='chunks';" | tr -d '[:space:]')
[[ "$exists" == "true|true" ]] && ok "chunks: relrowsecurity=true + relforcerowsecurity=true" || fail "chunks RLS state wrong: '$exists'"

step "psql: chunks UNIQUE (file_id, chunk_index) constraint present"
cnt=$(DB_PSQL -tA -c "SELECT count(*) FROM pg_constraint WHERE conrelid='chunks'::regclass AND contype='u';" | tr -d '[:space:]')
[[ "$cnt" -ge 1 ]] && ok "UNIQUE (file_id, chunk_index) constraint exists ($cnt)" || fail "no UNIQUE constraint on chunks"

step "psql: kb_app cannot UPDATE or DELETE on chunks (REVOKE check)"
upd=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunks','UPDATE')::text;" | tr -d '[:space:]')
del=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunks','DELETE')::text;" | tr -d '[:space:]')
sel=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunks','SELECT')::text;" | tr -d '[:space:]')
ins=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunks','INSERT')::text;" | tr -d '[:space:]')
if [[ "$upd" == "false" && "$del" == "false" && "$sel" == "true" && "$ins" == "true" ]]; then
    ok "kb_app grants: SELECT=true INSERT=true UPDATE=false DELETE=false"
else
    fail "kb_app grants wrong: SELECT=$sel INSERT=$ins UPDATE=$upd DELETE=$del"
fi

step "psql: files.lifecycle_state CHECK includes 'chunked'"
chk=$(DB_PSQL -tA -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='files'::regclass AND conname='files_lifecycle_state_check';")
if [[ "$chk" == *"chunked"* ]]; then
    ok "lifecycle_state CHECK widened to include 'chunked'"
else
    fail "CHECK doesn't include 'chunked': $chk"
fi

# ---------------------------------------------------------------------------
# E2E: PDF (Docling) → parsed → chunked
# ---------------------------------------------------------------------------

step "curl: POST tiny.pdf → 201"
pdf_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.pdf;type=application/pdf")
pdf_id=$(echo "$pdf_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
[[ -n "$pdf_id" ]] && ok "tiny.pdf uploaded id=$pdf_id" || { fail "POST tiny.pdf failed: $pdf_resp"; pdf_id=""; }

step "wait for tiny.pdf to reach lifecycle_state='chunked' (≤6 min for first-time Docling)"
chunked=0
for _ in $(seq 1 180); do
    if [[ -z "$pdf_id" ]]; then break; fi
    s=$(curl -sS "http://localhost:8000/files/$pdf_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3b chained contextualize_file may race past 'chunked' to
    # 'contextualized' before this loop polls. Any post-chunked state counts.
    if [[ "$s" == "chunked" || "$s" == "contextualized" || "$s" == "ready" ]]; then chunked=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( chunked == 1 )) && ok "tiny.pdf parsed + chunked end-to-end" || fail "tiny.pdf didn't reach chunked (last state: $s)"

step "psql: chunks rows present for tiny.pdf"
chunk_count=$(DB_PSQL -tA -c "SELECT count(*) FROM chunks WHERE file_id = '$pdf_id';" | tr -d '[:space:]')
[[ "$chunk_count" -ge 1 ]] && ok "$chunk_count chunk(s) for tiny.pdf" || fail "no chunks for tiny.pdf"

step "psql: chunks.source_page_numbers non-empty + token_count > 0"
sql="SELECT min(array_length(source_page_numbers,1)), min(token_count) FROM chunks WHERE file_id = '$pdf_id';"
result=$(DB_PSQL -tA -c "$sql" | tr -d '[:space:]')
# Expect e.g. '1|17' — both >= 1
min_pages=$(echo "$result" | cut -d'|' -f1)
min_tokens=$(echo "$result" | cut -d'|' -f2)
if [[ "$min_pages" -ge 1 && "$min_tokens" -ge 1 ]]; then
    ok "chunks have source_pages + tokens populated (min_pages=$min_pages, min_tokens=$min_tokens)"
else
    fail "chunks malformed: min_pages=$min_pages min_tokens=$min_tokens"
fi

step "psql: lifecycle history shows queued → parsing → parsed → chunked"
events=$(DB_PSQL -tA -c "SELECT string_agg(to_state, ',' ORDER BY created_at) FROM file_lifecycle WHERE file_id = '$pdf_id';")
if [[ "$events" == *"queued,parsing,parsed,chunked"* ]]; then
    ok "lifecycle progression queued→parsing→parsed→chunked observed"
else
    fail "unexpected lifecycle: $events"
fi

# ---------------------------------------------------------------------------
# E2E: xlsx → parsed → chunked
# ---------------------------------------------------------------------------

XLSX_MIME="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

step "curl: POST tiny.xlsx → 201"
xlsx_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.xlsx;type=${XLSX_MIME}")
xlsx_id=$(echo "$xlsx_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
[[ -n "$xlsx_id" ]] && ok "tiny.xlsx uploaded id=$xlsx_id" || { fail "POST tiny.xlsx failed: $xlsx_resp"; xlsx_id=""; }

step "wait for tiny.xlsx to reach lifecycle_state='chunked'"
chunked=0
for _ in $(seq 1 60); do
    if [[ -z "$xlsx_id" ]]; then break; fi
    s=$(curl -sS "http://localhost:8000/files/$xlsx_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3b chained contextualize_file may race past 'chunked' to
    # 'contextualized' before this loop polls. Any post-chunked state counts.
    if [[ "$s" == "chunked" || "$s" == "contextualized" || "$s" == "ready" ]]; then chunked=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( chunked == 1 )) && ok "tiny.xlsx parsed + chunked" || fail "tiny.xlsx didn't reach chunked (last state: $s)"

# ---------------------------------------------------------------------------
# E2E: email → parsed → chunked
# ---------------------------------------------------------------------------

step "curl: POST tiny.eml → 201"
eml_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.eml;type=message/rfc822")
eml_id=$(echo "$eml_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
[[ -n "$eml_id" ]] && ok "tiny.eml uploaded id=$eml_id" || { fail "POST tiny.eml failed: $eml_resp"; eml_id=""; }

step "wait for tiny.eml to reach lifecycle_state='chunked'"
chunked=0
for _ in $(seq 1 60); do
    if [[ -z "$eml_id" ]]; then break; fi
    s=$(curl -sS "http://localhost:8000/files/$eml_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3b chained contextualize_file may race past 'chunked' to
    # 'contextualized' before this loop polls. Any post-chunked state counts.
    if [[ "$s" == "chunked" || "$s" == "contextualized" || "$s" == "ready" ]]; then chunked=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( chunked == 1 )) && ok "tiny.eml parsed + chunked" || fail "tiny.eml didn't reach chunked (last state: $s)"

# ---------------------------------------------------------------------------
# Idempotency — re-deferring chunk_file is a no-op
# ---------------------------------------------------------------------------

step "psql: re-defer chunk_file → no duplicate chunking_done event"
# Force a manual chunk_file defer via Procrastinate's CLI inside the worker.
$COMPOSE exec -T worker procrastinate \
    --app=kb.workers.app.app defer kb.workers.tasks.chunk_file \
    "{\"file_id\":\"$pdf_id\"}" >/tmp/kb-verify-3a-defer.log 2>&1 || true
sleep 8  # let worker pick it up
events=$(DB_PSQL -tA -c "SELECT count(*) FROM file_lifecycle WHERE file_id='$pdf_id' AND event='chunking_done';" | tr -d '[:space:]')
[[ "$events" == "1" ]] && ok "exactly one chunking_done event (idempotent re-run)" || fail "expected 1 chunking_done; got $events"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 3a test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 3a test files over testcontainers"
phase_3a_tests=(
    tests/test_chunking_unit.py
    tests/test_chunking_worker.py
)
if uv run pytest "${phase_3a_tests[@]}" -q >/tmp/kb-verify-3a-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-3a-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-3a-pytest.log)"
    tail -30 /tmp/kb-verify-3a-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-3a] === SUMMARY ==="
echo "[verify-3a] checks passed: $CHECKS_PASSED"
echo "[verify-3a] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-3a] Phase 3a G5: GREEN ✅"
else
    echo "[verify-3a] Phase 3a G5: FAILED ❌"
fi
