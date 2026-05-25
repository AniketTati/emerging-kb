#!/usr/bin/env bash
# Phase 8c G5 — reranker (Cohere + mxbai + Identity).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-8c] === step $n: $* ==="; }
ok() { echo "[verify-8c]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-8c]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-8c] RESULT: $CHECKS_FAILED failed"; exit 1; fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-8c-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-8c-up.log 2>&1
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

step "worker imports kb.query.rerank"
out=$($COMPOSE exec -T worker python -c "
from kb.query.rerank import Reranker, IdentityReranker, CohereReranker, MxBaiReranker, make_reranker
print('OK')
" 2>/dev/null || echo "")
[[ "$out" == "OK" ]] && ok "rerank module imports cleanly" || fail "import failed"

step "no kb.query leak into kb.api/*"
leak=$(grep -r "from kb.query\|import kb.query" src/kb/api/ --exclude=query.py 2>/dev/null || true)
[[ -z "$leak" ]] && ok "no leak" || fail "leak:\n$leak"

step "Identity passthrough preserves order + truncates to top_k"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.rerank import IdentityReranker
from kb.query.rrf import Hit
r = IdentityReranker()
hits = [Hit(id=c, kind='chunk', score=0, snippet='s') for c in 'abcde']
out = asyncio.run(r.rerank('q', hits, top_k=3))
print(''.join(h.id for h in out))
" 2>/dev/null || echo "")
[[ "$out" == "abc" ]] && ok "Identity passthrough OK" || fail "got $out"

step "factory: explicit cohere without key raises ValueError"
err=$($COMPOSE exec -T -e KB_COHERE_API_KEY= -e KB_RERANKER=cohere worker python -c "
from kb.query.rerank import make_reranker
try: make_reranker()
except ValueError as e: print('ValueError:', str(e)[:50])
" 2>/dev/null || echo "")
[[ "$err" == ValueError:* ]] && ok "loud-fail on missing key" || fail "got: $err"

step "auto without Cohere key falls back to Identity (NOT mxbai — opt-in)"
out=$($COMPOSE exec -T -e KB_COHERE_API_KEY= -e KB_RERANKER=auto worker python -c "
from kb.query.rerank import make_reranker, IdentityReranker, MxBaiReranker
r = make_reranker()
print('identity' if isinstance(r, IdentityReranker) else ('mxbai' if isinstance(r, MxBaiReranker) else 'other'))
" 2>/dev/null || echo "")
[[ "$out" == "identity" ]] && ok "auto → Identity (mxbai opt-in)" || fail "got $out"

step "pytest — Phase 8c"
if uv run pytest tests/test_query_rerank_unit.py -q >/tmp/kb-verify-8c-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-8c-pytest.log)"
else
    fail "pytest failed"
    tail -40 /tmp/kb-verify-8c-pytest.log >&2
fi

echo
echo "[verify-8c] === SUMMARY ==="
echo "[verify-8c] checks passed: $CHECKS_PASSED"
echo "[verify-8c] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-8c] Phase 8c G5: GREEN ✅"
else
    echo "[verify-8c] Phase 8c G5: FAILED ❌"
fi
