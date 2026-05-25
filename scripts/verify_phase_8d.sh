#!/usr/bin/env bash
# Phase 8d G5 — CRAG relevance gate (Gemini + Identity).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-8d] === step $n: $* ==="; }
ok() { echo "[verify-8d]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-8d]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-8d] RESULT: $CHECKS_FAILED failed"; exit 1; fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-8d-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-8d-up.log 2>&1
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
(( migrate_ok == 1 )) && ok "migrate exited 0" || fail "migrate did not exit"

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
[[ "$h" == "healthy" ]] && ok "api healthy" || fail "api not healthy"
else
    ok "(reuse-stack) skipping setup"
fi

step "worker imports kb.query.crag"
out=$($COMPOSE exec -T worker python -c "
from kb.query.crag import CragGate, GeminiCragGate, IdentityCragGate, make_crag_gate, CRAG_THRESHOLD, _parse_score
print('OK', CRAG_THRESHOLD)
" 2>/dev/null || echo "")
[[ "$out" == "OK 0.5" ]] && ok "crag module imports cleanly + threshold = 0.5" || fail "import/threshold check failed: $out"

step "no kb.query leak into kb.api/*"
leak=$(grep -r "from kb.query\|import kb.query" src/kb/api/ --exclude=query.py 2>/dev/null || true)
[[ -z "$leak" ]] && ok "no leak" || fail "leak:\n$leak"

step "Identity always returns 1.0 (incl. empty hits — fail-safe pass)"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.crag import IdentityCragGate
from kb.query.rrf import Hit
g = IdentityCragGate()
a = asyncio.run(g.assess('q', [Hit(id='1', kind='chunk', score=0, snippet='s')]))
b = asyncio.run(g.assess('q', []))
print(a, b)
" 2>/dev/null || echo "")
[[ "$out" == "1.0 1.0" ]] && ok "Identity = 1.0 (with + without hits)" || fail "got: $out"

step "Gemini empty hits → 0.0 (decision #5, guaranteed refusal)"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.crag import GeminiCragGate
g = GeminiCragGate(api_key='fake')
out = asyncio.run(g.assess('q', []))
print(out)
" 2>/dev/null || echo "")
[[ "$out" == "0.0" ]] && ok "Gemini empty → 0.0" || fail "got: $out"

step "parser fail-safes: invalid JSON / missing key / clamp out-of-range"
out=$($COMPOSE exec -T worker python -c "
from kb.query.crag import _parse_score
print(_parse_score('not json'), _parse_score('{\"other\": 0.3}'), _parse_score('{\"avg_relevance\": 1.5}'), _parse_score('{\"avg_relevance\": -0.3}'))
" 2>/dev/null || echo "")
[[ "$out" == "1.0 1.0 1.0 0.0" ]] && ok "parser fail-safes OK" || fail "got: $out"

step "factory: explicit gemini without key raises ValueError"
err=$($COMPOSE exec -T -e KB_GEMINI_API_KEY= -e KB_QUERY_LLM=gemini worker python -c "
from kb.query.crag import make_crag_gate
try: make_crag_gate()
except ValueError as e: print('ValueError:', str(e)[:60])
" 2>/dev/null || echo "")
[[ "$err" == ValueError:* ]] && ok "loud-fail on missing key" || fail "got: $err"

step "factory: anthropic → Identity (decision #10, Wave A defer)"
out=$($COMPOSE exec -T -e KB_QUERY_LLM=anthropic worker python -c "
from kb.query.crag import make_crag_gate, IdentityCragGate
g = make_crag_gate()
print('identity' if isinstance(g, IdentityCragGate) else 'other')
" 2>/dev/null || echo "")
[[ "$out" == "identity" ]] && ok "anthropic → Identity (Wave A)" || fail "got: $out"

step "factory: auto without Gemini key → Identity"
out=$($COMPOSE exec -T -e KB_GEMINI_API_KEY= -e KB_QUERY_LLM=auto worker python -c "
from kb.query.crag import make_crag_gate, IdentityCragGate
g = make_crag_gate()
print('identity' if isinstance(g, IdentityCragGate) else 'other')
" 2>/dev/null || echo "")
[[ "$out" == "identity" ]] && ok "auto → Identity" || fail "got: $out"

step "pytest — Phase 8d"
if uv run pytest tests/test_query_crag_unit.py -q >/tmp/kb-verify-8d-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-8d-pytest.log)"
else
    fail "pytest failed"
    tail -40 /tmp/kb-verify-8d-pytest.log >&2
fi

echo
echo "[verify-8d] === SUMMARY ==="
echo "[verify-8d] checks passed: $CHECKS_PASSED"
echo "[verify-8d] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-8d] Phase 8d G5: GREEN ✅"
else
    echo "[verify-8d] Phase 8d G5: FAILED ❌"
fi
