#!/usr/bin/env bash
# Phase 2a G5 — end-to-end verification.
#
# Two stacks (same pattern as 0/1a/1b/1c):
#   1. docker-compose smoke (proves the runnable stack with 0008 applied,
#      worker container parses an upload to completion).
#   2. pytest over testcontainers (Phase 2a test files only).
#
# Phase 2a's added surface = 4 new tables + Procrastinate parse_file task +
# 5 endpoints + Docling integration + MinIO bytes storage.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-2a] .env not found; copying from .env.example"
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
WS_B="22222222-2222-2222-2222-222222222222"

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-2a] === step $n: $* ==="
}

ok() {
    echo "[verify-2a]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-2a]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-2a] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-2a] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-2a] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose (full stack including worker container)
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-2a-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-2a-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (now includes 0008)"
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
else
    ok "(reuse-stack) skipping compose build/up + migrate + api-healthy wait"
fi

# ---------------------------------------------------------------------------
# DDL invariants — 0008 applied correctly
# ---------------------------------------------------------------------------

step "psql: 4 new tables exist (files, file_lifecycle, raw_pages, parse_artifacts)"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('files','file_lifecycle','raw_pages','parse_artifacts')")
[[ "$out" == "4" ]] && ok "4 new tables exist" || fail "expected 4 tables; got $out"

step "psql: RLS forced on all 4 new tables"
out=$(DB_PSQL -tAc "SELECT count(*) FROM pg_class WHERE relname IN ('files','file_lifecycle','raw_pages','parse_artifacts') AND relrowsecurity AND relforcerowsecurity")
[[ "$out" == "4" ]] && ok "RLS forced on all 4 tables" || fail "expected 4; got $out"

step "psql: kb_app has only SELECT+INSERT on file_lifecycle + raw_pages (immutability)"
out_fl=$(DB_PSQL -tAc "SELECT array_agg(privilege_type ORDER BY privilege_type) FROM information_schema.role_table_grants WHERE grantee='kb_app' AND table_name='file_lifecycle'")
out_rp=$(DB_PSQL -tAc "SELECT array_agg(privilege_type ORDER BY privilege_type) FROM information_schema.role_table_grants WHERE grantee='kb_app' AND table_name='raw_pages'")
if [[ "$out_fl" == "{INSERT,SELECT}" && "$out_rp" == "{INSERT,SELECT}" ]]; then
    ok "file_lifecycle + raw_pages immutability enforced via GRANT"
else
    fail "expected {INSERT,SELECT} on both; got file_lifecycle=$out_fl raw_pages=$out_rp"
fi

step "psql: files content-hash partial unique index exists"
out=$(DB_PSQL -tAc "SELECT indexdef FROM pg_indexes WHERE indexname='files_workspace_sha_active_idx'")
if [[ "$out" == *"WHERE (lifecycle_state <> 'deleted'"* ]]; then
    ok "content-hash dedup index has correct partial predicate"
else
    fail "missing or wrong predicate: $out"
fi

# ---------------------------------------------------------------------------
# Full E2E: upload → worker parses → raw_pages populated
# ---------------------------------------------------------------------------

step "curl: POST /files with tiny.pdf returns 201 + lifecycle_state='queued'"
FILE_ID=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.pdf;type=application/pdf" \
    | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('id','') if d.get('lifecycle_state')=='queued' else '')")
[[ -n "$FILE_ID" ]] && ok "queued file id=$FILE_ID" || fail "POST failed to return queued file id"

step "wait for worker to parse the file (poll lifecycle_state)"
# Docling downloads model weights from HuggingFace on first use (~150 MB);
# in a fresh container this can take a few minutes. After the first parse,
# subsequent ones complete in seconds.
parsed=0
for _ in $(seq 1 120); do
    state=$(curl -sS "http://localhost:8000/files/$FILE_ID" -H "X-Test-Workspace: $WS_A" \
            | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3a chained chunk_file may race past 'parsed' to 'chunked' before
    # this loop polls. Any post-parse state counts as parse-success.
    if [[ "$state" == "parsed" || "$state" == "chunked" || "$state" == "contextualized" || "$state" == "embedded" || "$state" == "raptor_building" || "$state" == "mentions_extracting" || "$state" == "fields_extracting" || "$state" == "units_extracting" || "$state" == "ready" ]]; then parsed=1; break; fi
    if [[ "$state" == "failed" ]]; then break; fi
    sleep 5
done
(( parsed == 1 )) && ok "worker transitioned past 'parsing' to '$state'" || fail "did not parse within 600s (state: $state)"

step "curl: GET /files/:id/pages returns ≥1 page after parse"
total=$(curl -sS "http://localhost:8000/files/$FILE_ID/pages" -H "X-Test-Workspace: $WS_A" \
        | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('total',0))")
[[ "$total" -ge "1" ]] && ok "raw_pages total=$total" || fail "expected ≥1 page; got $total"

step "curl: GET /files/:id lifecycle history starts with queued→parsing→parsed"
# Phase 3a may append (parsed,chunked). Verify the PREFIX matches; Phase 3a's
# own verify script handles the post-parsed transitions.
lifecycle=$(curl -sS "http://localhost:8000/files/$FILE_ID" -H "X-Test-Workspace: $WS_A" \
            | python3 -c "
import sys, json
events = json.loads(sys.stdin.read())['lifecycle']
transitions = [(e['from_state'], e['to_state']) for e in events]
expected_prefix = [(None,'queued'), ('queued','parsing'), ('parsing','parsed')]
print('match' if transitions[:3] == expected_prefix else 'mismatch: ' + str(transitions))
")
[[ "$lifecycle" == "match" ]] && ok "lifecycle history starts with queued→parsing→parsed" || fail "$lifecycle"

# ---------------------------------------------------------------------------
# Content-hash dedup
# ---------------------------------------------------------------------------

step "curl: duplicate POST same content returns 200 with X-Dedup-Reason header"
# -D dumps response headers to file; -w gets status. One curl, two outputs.
http=$(curl -sS -D /tmp/kb-verify-2a-dedup-headers -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.pdf;type=application/pdf")
dedup_header=$(grep -i "^x-dedup-reason:" /tmp/kb-verify-2a-dedup-headers || echo "")
if [[ "$http" == "200" && "$dedup_header" == *"content-hash"* ]]; then
    ok "dedup returned 200 with X-Dedup-Reason: content-hash"
else
    fail "expected 200 + X-Dedup-Reason; got http=$http header=$dedup_header"
fi

# ---------------------------------------------------------------------------
# RLS isolation
# ---------------------------------------------------------------------------

step "curl: workspace B can't see workspace A's files → empty list"
total_b=$(curl -sS "http://localhost:8000/files" -H "X-Test-Workspace: $WS_B" \
          | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['total'])")
[[ "$total_b" == "0" ]] && ok "RLS isolates B from A (total=0)" || fail "RLS leak: B sees $total_b"

step "curl: workspace B GET /files/:id of A's file → 404"
http=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:8000/files/$FILE_ID" -H "X-Test-Workspace: $WS_B")
[[ "$http" == "404" ]] && ok "404 for B's view of A's file" || fail "expected 404 got $http"

# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

step "curl: POST .txt → 415 unsupported-media-type"
http=$(curl -sS -o /tmp/kb-verify-2a-415.json -w "%{http_code}" -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@README.md;type=text/plain")
slug=$(python3 -c "import sys,json; print(json.load(open('/tmp/kb-verify-2a-415.json')).get('type',''))" 2>/dev/null || echo "")
[[ "$http" == "415" && "$slug" == *"unsupported-media-type" ]] && ok "415 unsupported-media-type" || fail "expected 415 got http=$http slug=$slug"

# ---------------------------------------------------------------------------
# OpenAPI exposure
# ---------------------------------------------------------------------------

step "curl: /openapi.json includes all 5 file endpoints"
result=$(curl -sS http://localhost:8000/openapi.json | python3 -c "
import sys, json
ps = json.loads(sys.stdin.read())['paths']
required = [
    ('/files', 'post'),
    ('/files', 'get'),
    ('/files/{file_id}', 'get'),
    ('/files/{file_id}/pages', 'get'),
    ('/files/{file_id}', 'delete'),
]
missing = [(p, m) for p, m in required if p not in ps or m not in ps[p]]
print('ok' if not missing else 'missing: ' + str(missing))
")
[[ "$result" == "ok" ]] && ok "openapi has all 5 file endpoints" || fail "$result"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 2a test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 2a test files over testcontainers"
phase_2a_tests=(
    tests/test_files_crud.py
    tests/test_parse_dispatch.py
    tests/test_parse_pdf_docling.py
    tests/test_raw_pages.py
    tests/test_files_lifecycle.py
)
if uv run pytest "${phase_2a_tests[@]}" -q >/tmp/kb-verify-2a-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-2a-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-2a-pytest.log)"
    tail -30 /tmp/kb-verify-2a-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-2a] === SUMMARY ==="
echo "[verify-2a] checks passed: $CHECKS_PASSED"
echo "[verify-2a] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-2a] Phase 2a G5: GREEN ✅"
else
    echo "[verify-2a] Phase 2a G5: FAILED ❌"
fi
