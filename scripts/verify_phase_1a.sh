#!/usr/bin/env bash
# Phase 1a G5 — end-to-end verification.
#
# Two stacks, same pattern as verify_phase_0.sh:
#   1. docker-compose smoke (proves the runnable stack with 0005_schemas.sql applied).
#   2. pytest over testcontainers (proves application logic).
#
# Phase 0 invariants stay covered by verify_phase_0.sh; this script ONLY
# asserts Phase 1a's added surface (schemas table + 5 endpoints + RLS isolation).

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-1a] .env not found; copying from .env.example"
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

# Two distinct workspace UUIDs for the RLS isolation check.
WS_A="11111111-1111-1111-1111-111111111111"
WS_B="22222222-2222-2222-2222-222222222222"

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-1a] === step $n: $* ==="
}

ok() {
    echo "[verify-1a]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-1a]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-1a] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-1a] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-1a] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose
# ----------------------------------------------------------------------------

step "compose build + up"
$COMPOSE build >/tmp/kb-verify-1a-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-1a-up.log 2>&1
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

step "psql: schemas table + partial unique index"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name='schemas'")
[[ "$out" == "1" ]] && ok "schemas table exists" || fail "schemas table missing"

out=$(DB_PSQL -tAc "SELECT indexdef FROM pg_indexes WHERE indexname='schemas_workspace_name_active_idx'")
if [[ "$out" == *"WHERE (lifecycle_state = 'active'"* ]]; then
    ok "partial unique index defined correctly"
else
    fail "partial unique index missing or wrong predicate: $out"
fi

step "psql: RLS enabled on schemas"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='schemas'")
if [[ "$out" == "true|true" ]]; then
    ok "schemas RLS enabled + forced"
else
    fail "expected true|true — got: $out"
fi

step "curl: POST /schemas creates a schema (workspace A)"
post_a=$(curl -sS -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-a","description":"verify-1a"}')
id_a=$(echo "$post_a" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
if [[ -n "$id_a" ]]; then
    ok "created schema id=$id_a"
else
    fail "POST didn't return an id: $post_a"
fi

step "curl: GET /schemas as workspace A lists it"
list_a=$(curl -sS http://localhost:8000/schemas -H "X-Test-Workspace: $WS_A")
total_a=$(echo "$list_a" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('total',0))")
if [[ "$total_a" == "1" ]]; then
    ok "workspace A lists 1 schema"
else
    fail "expected total=1, got: $list_a"
fi

step "curl: duplicate POST in workspace A returns 409"
http=$(curl -sS -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-a","description":""}')
[[ "$http" == "409" ]] && ok "409 on duplicate name" || fail "expected 409 got $http"

step "curl: GET /schemas as workspace B does NOT see A's schema"
list_b=$(curl -sS http://localhost:8000/schemas -H "X-Test-Workspace: $WS_B")
total_b=$(echo "$list_b" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('total',0))")
if [[ "$total_b" == "0" ]]; then
    ok "RLS isolates workspace B from A"
else
    fail "RLS leak: workspace B sees $total_b items"
fi

step "curl: POST same name in workspace B succeeds (independent namespace)"
http=$(curl -sS -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_B" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-a","description":""}')
[[ "$http" == "201" ]] && ok "B can use the same name as A" || fail "expected 201 got $http"

step "curl: Idempotency-Key replay returns cached 201"
# Body content must match, but key order may differ — PG `jsonb` doesn't preserve
# insertion order, so the replay reads keys back in storage order, not the
# original pydantic serialization order. Pytest compares parsed JSON; mirror that.
key=$(uuidgen)
r1=$(curl -sS -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $key" \
    -d '{"name":"replay-target","description":""}')
r2=$(curl -sS -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $key" \
    -d '{"name":"replay-target","description":""}')
match=$(python3 -c "
import json, sys
print('match' if json.loads(sys.argv[1]) == json.loads(sys.argv[2]) else 'mismatch')
" "$r1" "$r2")
if [[ "$match" == "match" ]]; then
    ok "second POST replayed cached response (semantic match)"
else
    fail "replay mismatch — r1=$r1 vs r2=$r2"
fi

step "curl: DELETE soft-deletes and row stays in DB"
http=$(curl -sS -o /dev/null -w "%{http_code}" -X DELETE "http://localhost:8000/schemas/$id_a" \
    -H "X-Test-Workspace: $WS_A")
[[ "$http" == "204" ]] && ok "DELETE returns 204" || fail "expected 204 got $http"

http=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:8000/schemas/$id_a" \
    -H "X-Test-Workspace: $WS_A")
[[ "$http" == "404" ]] && ok "GET after DELETE returns 404" || fail "expected 404 got $http"

# Verify row still exists with lifecycle_state='deleted' (soft delete proof).
state=$(DB_PSQL -tAc "SELECT lifecycle_state FROM schemas WHERE id = '$id_a'")
[[ "$state" == "deleted" ]] && ok "row remains in DB with lifecycle_state='deleted'" || fail "expected deleted, got: $state"

step "curl: /openapi.json paths include /schemas + GET/POST/PUT/DELETE methods"
paths=$(curl -sS http://localhost:8000/openapi.json | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
ps = data['paths']
have_list = '/schemas' in ps and 'post' in ps['/schemas'] and 'get' in ps['/schemas']
have_item = '/schemas/{schema_id}' in ps
print('ok' if (have_list and have_item) else 'no')
")
[[ "$paths" == "ok" ]] && ok "openapi has /schemas + /schemas/{schema_id}" || fail "openapi paths missing schema routes"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 1a test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 1a test files over testcontainers"
phase_1a_tests=(
    tests/test_schemas_crud.py
    tests/test_schemas_rls.py
    tests/test_idempotency.py
)
if uv run pytest "${phase_1a_tests[@]}" -q >/tmp/kb-verify-1a-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-1a-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-1a-pytest.log)"
    tail -30 /tmp/kb-verify-1a-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-1a] === SUMMARY ==="
echo "[verify-1a] checks passed: $CHECKS_PASSED"
echo "[verify-1a] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-1a] Phase 1a G5: GREEN ✅"
else
    echo "[verify-1a] Phase 1a G5: FAILED ❌"
fi
