#!/usr/bin/env bash
# Phase 0 G5 — end-to-end verification.
#
# Two stacks are exercised:
#   1. The docker-compose stack (proves deployability + the G1 G5 acceptance list).
#   2. The pytest suite over testcontainers (proves application logic).
#
# Both must pass for G5 green. Idempotent; cleans up at the end.
#
# Usage:
#   scripts/verify_phase_0.sh
#
# Env:
#   KB_VERIFY_KEEP_STACK=1   skip the final teardown (handy for debugging)

set -euo pipefail

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [[ ! -f .env ]]; then
    echo "[verify] .env not found; copying from .env.example"
    cp .env.example .env
fi

# .env should now exist; load it for psql / curl steps below.
set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE="docker compose"
# Run psql inside the db container — no local psql install required.
DB_PSQL() {
    $COMPOSE exec -T db psql -U "${KB_POSTGRES_USER:-kb}" -d "${KB_POSTGRES_DB:-kb}" "$@"
}

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify] === step $n: $* ==="
}

ok() {
    echo "[verify]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker-compose smoke
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "docker compose build"
$COMPOSE build >/tmp/kb-verify-build.log 2>&1
ok "compose build clean"

step "docker compose up -d"
$COMPOSE up -d >/tmp/kb-verify-up.log 2>&1
ok "compose up -d returned"

step "wait for migrate container to exit 0"
# `migrate` is a one-shot; depends_on chains api/worker behind its successful exit.
migrate_ok=0
for _ in $(seq 1 60); do
    raw=$($COMPOSE ps -a --format json migrate 2>/dev/null || echo '')
    parsed=$(python3 -c "
import sys, json
text = sys.stdin.read().strip()
if not text:
    print('', '')
    sys.exit()
# compose ps emits either a JSON object or NDJSON; handle both.
first = text.splitlines()[0]
try:
    data = json.loads(first)
except Exception:
    print('', '')
    sys.exit()
print(data.get('State', ''), data.get('ExitCode', ''))
" <<<"$raw" 2>/dev/null || echo "")
    if [[ "$parsed" == "exited 0" ]]; then
        migrate_ok=1; break
    fi
    sleep 2
done
if (( migrate_ok == 1 )); then
    ok "migrate exited 0"
else
    fail "migrate did not exit cleanly within 120s (last seen: ${parsed:-unknown})"
fi

step "wait for db, minio, api healthy"
for svc in db minio api; do
    for _ in $(seq 1 30); do
        h=$($COMPOSE ps --format json $svc 2>/dev/null | python3 -c "import sys,json
try:
    data=json.loads(sys.stdin.read() or '{}')
    print(data.get('Health',''))
except Exception: print('')" 2>/dev/null || echo "")
        if [[ "$h" == "healthy" ]]; then break; fi
        sleep 2
    done
    if [[ "$h" == "healthy" ]]; then
        ok "$svc healthy"
    else
        fail "$svc not healthy after 60s (state: $h)"
    fi
done
else
    ok "(reuse-stack) skipping compose build/up + migrate + api-healthy wait"
fi

step "psql: extensions installed (vector + pg_search + ltree)"
exts=$(DB_PSQL -tAc \
    "SELECT string_agg(extname, ',' ORDER BY extname) FROM pg_extension WHERE extname IN ('vector','pg_search','ltree')")
if [[ "$exts" == "ltree,pg_search,vector" ]]; then
    ok "extensions present: $exts"
else
    fail "expected ltree,pg_search,vector — got: $exts"
fi

step "psql: lifecycle tables present"
tables=$(DB_PSQL -tAc \
    "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('schema_migrations','audit_log','idempotency_keys')")
if [[ "$tables" == "audit_log,idempotency_keys,schema_migrations" ]]; then
    ok "lifecycle tables present: $tables"
else
    fail "expected audit_log + idempotency_keys + schema_migrations — got: $tables"
fi

step "psql: audit_log is partitioned with initial partitions"
parts=$(DB_PSQL -tAc \
    "SELECT string_agg(inhrelid::regclass::text, ',' ORDER BY inhrelid::regclass::text) FROM pg_inherits WHERE inhparent='audit_log'::regclass")
if [[ "$parts" == "audit_log_2026_05,audit_log_2026_06" ]]; then
    ok "audit_log partitions: $parts"
else
    fail "expected audit_log_2026_05,audit_log_2026_06 — got: $parts"
fi

step "psql: RLS enabled on workspace-scoped tables"
# pg_class.relrowsecurity is a boolean — psql -tAc prints "true"/"false".
rls=$(DB_PSQL -tAc \
    "SELECT string_agg(relname || '=' || relrowsecurity::text, ',' ORDER BY relname) FROM pg_class WHERE relname IN ('audit_log','idempotency_keys','schema_migrations')")
expected="audit_log=true,idempotency_keys=true,schema_migrations=false"
if [[ "$rls" == "$expected" ]]; then
    ok "RLS state correct: $rls"
else
    fail "expected $expected — got: $rls"
fi

step "psql: kb_app role exists with LOGIN"
canlogin=$(DB_PSQL -tAc \
    "SELECT rolcanlogin::text FROM pg_roles WHERE rolname='kb_app'")
if [[ "$canlogin" == "true" ]]; then
    ok "kb_app role exists with LOGIN"
else
    fail "kb_app role missing or no LOGIN (rolcanlogin: $canlogin)"
fi

step "curl: /health returns 200 with documented shape"
body=$(curl -fsS http://localhost:8000/health)
status=$(echo "$body" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('status',''))")
if [[ "$status" == "ok" ]]; then
    ok "/health body: $body"
else
    fail "/health returned unexpected body: $body"
fi

step "curl: /ready returns 200 with all checks ok"
# Don't pass -f: we want to inspect the body even when status is 503.
http_code=$(curl -sS -o /tmp/kb-verify-ready.json -w "%{http_code}" http://localhost:8000/ready)
body=$(cat /tmp/kb-verify-ready.json)
if [[ "$http_code" == "200" ]]; then
    ok "/ready body: $body"
else
    fail "/ready returned $http_code: $body"
fi

step "curl: response carries X-Request-Id header"
rid=$(curl -fsS -D - -o /dev/null http://localhost:8000/health | tr -d '\r' | awk -F': ' 'tolower($1)=="x-request-id" {print $2}')
if [[ -n "$rid" ]]; then
    ok "X-Request-Id: $rid"
else
    fail "X-Request-Id header missing"
fi

step "curl: /openapi.json paths contains /health and /ready"
paths=$(curl -fsS http://localhost:8000/openapi.json | python3 -c "import sys,json; print(','.join(sorted(json.loads(sys.stdin.read())['paths'].keys())))")
# "Contains" (not "equals") — later phases legitimately add /schemas, /chat, etc.
# Phase 0's responsibility is only that ITS paths are present and mounted via middleware.
if [[ ",$paths," == *",/health,"* && ",$paths," == *",/ready,"* ]]; then
    ok "openapi paths include /health + /ready: $paths"
else
    fail "expected /health and /ready in paths — got: $paths"
fi

# ----------------------------------------------------------------------------
# Stack 2: pytest over testcontainers
# ----------------------------------------------------------------------------

step "pytest (Phase 0 test files only, over testcontainers)"
# Each phase's verify runs ITS tests — later phases' in-progress red skeletons
# don't count against Phase 0's invariant check.
phase_0_tests=(
    tests/test_health.py
    tests/test_ready.py
    tests/test_migrations.py
    tests/test_rls.py
    tests/test_middleware.py
)
if uv run pytest "${phase_0_tests[@]}" -q >/tmp/kb-verify-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-pytest.log)"
    tail -30 /tmp/kb-verify-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Final summary
# ----------------------------------------------------------------------------

echo
echo "[verify] === SUMMARY ==="
echo "[verify] checks passed: $CHECKS_PASSED"
echo "[verify] checks failed: $CHECKS_FAILED"

if (( CHECKS_FAILED == 0 )); then
    echo "[verify] Phase 0 G5: GREEN ✅"
else
    echo "[verify] Phase 0 G5: FAILED ❌"
fi
