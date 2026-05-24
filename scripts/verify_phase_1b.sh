#!/usr/bin/env bash
# Phase 1b G5 — end-to-end verification.
#
# Two stacks, same pattern as verify_phase_0.sh and verify_phase_1a.sh:
#   1. docker-compose smoke (proves the runnable stack with 0006 applied).
#   2. pytest over testcontainers (proves application logic — Phase 1b test
#      files only; Phase 0+1a verify scripts cover their own surfaces).
#
# Phase 1b's added surface = schema_versions table + schemas.current_version_id
# pointer + 3 new endpoints (list / read+diff / rollback) + 2 mutated endpoints
# (POST + PUT now write version rows in-tx). RLS + idempotency replay tested
# against the new surface too.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-1b] .env not found; copying from .env.example"
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
    echo "[verify-1b] === step $n: $* ==="
}

ok() {
    echo "[verify-1b]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-1b]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-1b] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-1b] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-1b] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-1b-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-1b-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (now includes 0006)"
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
# DDL invariants — 0006 applied correctly
# ---------------------------------------------------------------------------

step "psql: schema_versions table exists with UNIQUE (schema_id, version_number)"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name='schema_versions'")
[[ "$out" == "1" ]] && ok "schema_versions table exists" || fail "schema_versions table missing"

out=$(DB_PSQL -tAc "SELECT conname FROM pg_constraint WHERE conrelid='schema_versions'::regclass AND contype='u'")
if [[ -n "$out" ]]; then
    ok "schema_versions has at least one UNIQUE constraint ($out)"
else
    fail "schema_versions missing UNIQUE constraint"
fi

step "psql: RLS enabled+forced on schema_versions"
out=$(DB_PSQL -tAc "SELECT relrowsecurity::text, relforcerowsecurity::text FROM pg_class WHERE relname='schema_versions'")
if [[ "$out" == "true|true" ]]; then
    ok "schema_versions RLS enabled + forced"
else
    fail "expected true|true — got: $out"
fi

step "psql: kb_app has SELECT+INSERT but NOT UPDATE/DELETE on schema_versions (decision #10)"
out=$(DB_PSQL -tAc "SELECT array_agg(privilege_type ORDER BY privilege_type) FROM information_schema.role_table_grants WHERE grantee='kb_app' AND table_name='schema_versions'")
# Expect exactly {INSERT,SELECT}, no UPDATE or DELETE.
if [[ "$out" == "{INSERT,SELECT}" ]]; then
    ok "schema_versions grants restricted to SELECT+INSERT (immutability enforced)"
else
    fail "expected {INSERT,SELECT}, got: $out"
fi

step "psql: schemas.current_version_id FK exists with ON DELETE SET NULL (decision #11)"
out=$(DB_PSQL -tAc "SELECT confdeltype FROM pg_constraint WHERE conname LIKE 'schemas_current_version_id_fkey%'")
# confdeltype 'n' = SET NULL.
if [[ "$out" == "n" ]]; then
    ok "schemas.current_version_id ON DELETE SET NULL"
else
    fail "expected confdeltype='n' (SET NULL), got: '$out'"
fi

# ---------------------------------------------------------------------------
# HTTP behavior — happy paths
# ---------------------------------------------------------------------------

step "curl: POST /schemas returns current_version=1 (workspace A)"
post_a=$(curl -sS -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-1b-a","description":"v1"}')
id_a=$(echo "$post_a" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
cv=$(echo "$post_a" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('current_version',''))")
if [[ -n "$id_a" && "$cv" == "1" ]]; then
    ok "schema id=$id_a current_version=1"
else
    fail "POST didn't return id+current_version=1: $post_a"
fi

step "curl: PUT /schemas/:id bumps current_version to 2"
put_a=$(curl -sS -X PUT "http://localhost:8000/schemas/$id_a" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-1b-a","description":"v2"}')
cv=$(echo "$put_a" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('current_version',''))")
[[ "$cv" == "2" ]] && ok "PUT bumped current_version to 2" || fail "expected 2 got: $cv ($put_a)"

step "curl: GET /schemas/:id/versions lists v2,v1 newest-first"
list=$(curl -sS "http://localhost:8000/schemas/$id_a/versions" -H "X-Test-Workspace: $WS_A")
versions=$(echo "$list" | python3 -c "import sys,json; print(','.join(str(i['version']) for i in json.loads(sys.stdin.read())['items']))")
[[ "$versions" == "2,1" ]] && ok "version list = [v2, v1]" || fail "expected '2,1' got: '$versions'"

step "curl: GET /schemas/:id/versions/2 has diff_from_prior with changed description"
v2=$(curl -sS "http://localhost:8000/schemas/$id_a/versions/2" -H "X-Test-Workspace: $WS_A")
diff_changed=$(echo "$v2" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())['diff_from_prior']
ch = d['changed']
if len(ch) == 1 and ch[0]['path'] == 'description' and ch[0]['old'] == 'v1' and ch[0]['new'] == 'v2':
    print('ok')
else:
    print('mismatch:', d)
")
[[ "$diff_changed" == "ok" ]] && ok "v2 diff_from_prior matches §3.6 format" || fail "diff mismatch: $diff_changed"

step "curl: GET /schemas/:id/versions/1 has diff_from_prior=null"
v1=$(curl -sS "http://localhost:8000/schemas/$id_a/versions/1" -H "X-Test-Workspace: $WS_A")
is_null=$(echo "$v1" | python3 -c "import sys,json; print('null' if json.loads(sys.stdin.read())['diff_from_prior'] is None else 'NOT_NULL')")
[[ "$is_null" == "null" ]] && ok "v1 diff_from_prior is null (no prior)" || fail "v1 should have null diff"

# ---------------------------------------------------------------------------
# Rollback semantics — clone-forward + noop + Idempotency replay
# ---------------------------------------------------------------------------

step "curl: POST rollback to v1 creates v3 with v1's body (clone-forward)"
rb=$(curl -sS -X POST "http://localhost:8000/schemas/$id_a/versions/1/rollback" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{}')
cv=$(echo "$rb" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('current_version',''))")
desc=$(echo "$rb" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('description',''))")
if [[ "$cv" == "3" && "$desc" == "v1" ]]; then
    ok "rollback produced v3 with v1's description"
else
    fail "expected current_version=3 + description='v1'; got cv=$cv desc='$desc'"
fi

step "curl: GET v3 has kind='rollback' and parent_version=2"
v3=$(curl -sS "http://localhost:8000/schemas/$id_a/versions/3" -H "X-Test-Workspace: $WS_A")
combo=$(echo "$v3" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d['kind']+':'+str(d['parent_version']))")
[[ "$combo" == "rollback:2" ]] && ok "v3 kind=rollback, parent=2" || fail "expected 'rollback:2' got '$combo'"

step "curl: rollback to current (v3) returns 409 rollback-noop"
http=$(curl -sS -o /tmp/kb-verify-1b-noop.json -w "%{http_code}" -X POST "http://localhost:8000/schemas/$id_a/versions/3/rollback" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{}')
slug=$(python3 -c "import sys,json; print(json.load(open('/tmp/kb-verify-1b-noop.json')).get('type',''))")
if [[ "$http" == "409" && "$slug" == *"rollback-noop" ]]; then
    ok "409 rollback-noop on same-as-current"
else
    fail "expected 409 rollback-noop; got http=$http slug=$slug"
fi

step "curl: rollback Idempotency-Key replay does NOT write a new version"
# Setup: PUT one more time so we have v4, then rollback to v1 with key K. Replay.
curl -sS -X PUT "http://localhost:8000/schemas/$id_a" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-1b-a","description":"v4"}' >/dev/null
key=$(uuidgen)
rb1=$(curl -sS -X POST "http://localhost:8000/schemas/$id_a/versions/1/rollback" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $key" -d '{}')
rb2=$(curl -sS -X POST "http://localhost:8000/schemas/$id_a/versions/1/rollback" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $key" -d '{}')
# Compare semantically (PG jsonb key order isn't preserved across replay).
match=$(python3 -c "
import json, sys
print('match' if json.loads(sys.argv[1]) == json.loads(sys.argv[2]) else 'mismatch')
" "$rb1" "$rb2")
total=$(DB_PSQL -tAc "SELECT count(*) FROM schema_versions WHERE schema_id = '$id_a'")
if [[ "$match" == "match" && "$total" == "5" ]]; then
    ok "replay returned cached body and did not write v6 (count stayed at 5)"
else
    fail "match=$match total=$total (expected match=match total=5: v1,v2,v3 rollback, v4 put, v5 rollback)"
fi

# ---------------------------------------------------------------------------
# RLS isolation across workspaces
# ---------------------------------------------------------------------------

step "curl: GET versions as workspace B for A's schema → 404 (NOT 403)"
http=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:8000/schemas/$id_a/versions" -H "X-Test-Workspace: $WS_B")
[[ "$http" == "404" ]] && ok "RLS leaks via 404 (existence not revealed)" || fail "expected 404 got $http"

step "curl: rollback as workspace B → 404"
http=$(curl -sS -o /dev/null -w "%{http_code}" -X POST "http://localhost:8000/schemas/$id_a/versions/1/rollback" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS_B" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{}')
[[ "$http" == "404" ]] && ok "rollback as B isolated" || fail "expected 404 got $http"

# ---------------------------------------------------------------------------
# OpenAPI exposure
# ---------------------------------------------------------------------------

step "curl: /openapi.json includes the 3 new version endpoints"
paths=$(curl -sS http://localhost:8000/openapi.json | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
ps = data['paths']
ok = (
    '/schemas/{schema_id}/versions' in ps
    and 'get' in ps['/schemas/{schema_id}/versions']
    and '/schemas/{schema_id}/versions/{version}' in ps
    and '/schemas/{schema_id}/versions/{version}/rollback' in ps
    and 'post' in ps['/schemas/{schema_id}/versions/{version}/rollback']
)
print('ok' if ok else 'no')
")
[[ "$paths" == "ok" ]] && ok "openapi has all 3 version endpoints" || fail "openapi missing version paths"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 1b test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 1b test files over testcontainers"
phase_1b_tests=(
    tests/test_schema_versions.py
    tests/test_schemas_crud.py
    tests/test_idempotency.py
)
if uv run pytest "${phase_1b_tests[@]}" -q >/tmp/kb-verify-1b-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-1b-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-1b-pytest.log)"
    tail -30 /tmp/kb-verify-1b-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-1b] === SUMMARY ==="
echo "[verify-1b] checks passed: $CHECKS_PASSED"
echo "[verify-1b] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-1b] Phase 1b G5: GREEN ✅"
else
    echo "[verify-1b] Phase 1b G5: FAILED ❌"
fi
