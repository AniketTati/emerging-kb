#!/usr/bin/env bash
# Phase 8a G5 — query rewriter (Step-Back + HyDE + Query2Doc).
#
# Pure module phase — no migration, no HTTP surface, no DB writes. Verify
# scope is: module imports cleanly in the worker container, no leak into
# kb.api/*, Identity fallback works, factory selector matrix passes, and
# the Phase 8a pytest suite is green.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-8a] .env not found; copying from .env.example"
    cp .env.example .env
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE="docker compose"

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-8a] === step $n: $* ==="
}
ok() { echo "[verify-8a]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-8a]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-8a] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-8a] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-8a] script exited non-zero before all checks ran"
        exit $rc
    fi
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-8a-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-8a-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (no new migration in 8a)"
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
# Module-level invariants
# ----------------------------------------------------------------------------

step "worker container imports kb.query.rewriter"
import_ok=$($COMPOSE exec -T worker python -c "
from kb.query.rewriter import (
    Rewrites, IdentityQueryRewriter, GeminiQueryRewriter, AnthropicQueryRewriter,
    make_query_rewriter, _parse_rewrites,
)
print('OK')
" 2>/dev/null || echo "")
[[ "$import_ok" == "OK" ]] && ok "kb.query.rewriter imports cleanly" || fail "rewriter import failed"

step "decision #10 enforced: kb.query.rewriter NOT mounted on any kb.api router"
leak=$(grep -r "from kb.query\|import kb.query" src/kb/api/ --exclude=query.py 2>/dev/null || true)
if [[ -z "$leak" ]]; then
    ok "no leak of kb.query into kb.api.* (8f owns HTTP surface)"
else
    fail "kb.query leaked into kb.api.*:\n$leak"
fi

step "Identity rewriter returns the original for all 3 slots"
out=$($COMPOSE exec -T worker python -c "
import asyncio, os
os.environ['KB_QUERY_LLM'] = 'identity'
from kb.query.rewriter import make_query_rewriter
r = make_query_rewriter()
result = asyncio.run(r.rewrite('foundation issues'))
print(result.original, '|', result.step_back, '|', result.hyde, '|', result.query2doc)
" 2>/dev/null || echo "")
if [[ "$out" == "foundation issues | foundation issues | foundation issues | foundation issues" ]]; then
    ok "Identity rewriter passthrough OK"
else
    fail "Identity rewriter unexpected output: $out"
fi

step "factory selector: explicit gemini without key raises ValueError"
err=$($COMPOSE exec -T -e KB_GEMINI_API_KEY= -e KB_QUERY_LLM=gemini worker python -c "
from kb.query.rewriter import make_query_rewriter
try:
    make_query_rewriter()
    print('NO_ERROR')
except ValueError as e:
    print('ValueError:', str(e)[:60])
" 2>/dev/null || echo "")
if [[ "$err" == ValueError:* && "$err" == *"KB_GEMINI_API_KEY"* ]]; then
    ok "explicit gemini-without-key raises ValueError loud-fail"
else
    fail "expected ValueError; got: $err"
fi

# ----------------------------------------------------------------------------
# Phase 8a pytest
# ----------------------------------------------------------------------------

step "pytest — Phase 8a test file over testcontainers"
if uv run pytest tests/test_query_rewriter_unit.py -q >/tmp/kb-verify-8a-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-8a-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-8a-pytest.log)"
    tail -40 /tmp/kb-verify-8a-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-8a] === SUMMARY ==="
echo "[verify-8a] checks passed: $CHECKS_PASSED"
echo "[verify-8a] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-8a] Phase 8a G5: GREEN ✅"
else
    echo "[verify-8a] Phase 8a G5: FAILED ❌"
fi
