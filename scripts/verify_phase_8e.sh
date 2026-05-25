#!/usr/bin/env bash
# Phase 8e G5 — Astute generation (Gemini + Identity).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-8e] === step $n: $* ==="; }
ok() { echo "[verify-8e]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-8e]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-8e] RESULT: $CHECKS_FAILED failed"; exit 1; fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-8e-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-8e-up.log 2>&1
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

step "worker imports kb.query.generate"
out=$($COMPOSE exec -T worker python -c "
from kb.query.generate import (
    Generator, GeminiGenerator, IdentityGenerator, make_generator,
    GenerationResult, Citation, _parse_result, _build_user_prompt,
)
print('OK')
" 2>/dev/null || echo "")
[[ "$out" == "OK" ]] && ok "generate module imports cleanly" || fail "import failed: $out"

step "no kb.query leak into kb.api/*"
leak=$(grep -r "from kb.query\|import kb.query" src/kb/api/ --exclude=query.py 2>/dev/null || true)
[[ -z "$leak" ]] && ok "no leak" || fail "leak:\n$leak"

step "Identity stub: templated echo with citations from hits"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.generate import IdentityGenerator
from kb.query.rrf import Hit
g = IdentityGenerator()
hits = [Hit(id=f'h{i}', kind='chunk', score=0.5, snippet=f's{i}', metadata={'file_id':'f1'}) for i in range(3)]
out = asyncio.run(g.generate('q?', hits))
print(out.refused, len(out.citations), 'identity-stub' in out.answer, 'hits: 3' in out.answer)
" 2>/dev/null || echo "")
[[ "$out" == "False 3 True True" ]] && ok "Identity stub shape OK" || fail "got: $out"

step "Identity force_refuse → insufficient_evidence"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.generate import IdentityGenerator
from kb.query.rrf import Hit
g = IdentityGenerator()
out = asyncio.run(g.generate('q', [Hit(id='1',kind='chunk',score=0,snippet='s',metadata={})], force_refuse=True))
print(out.refused, out.refusal_reason)
" 2>/dev/null || echo "")
[[ "$out" == "True insufficient_evidence" ]] && ok "force_refuse OK" || fail "got: $out"

step "Identity empty hits → no_hits"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.generate import IdentityGenerator
g = IdentityGenerator()
out = asyncio.run(g.generate('q', []))
print(out.refused, out.refusal_reason)
" 2>/dev/null || echo "")
[[ "$out" == "True no_hits" ]] && ok "no_hits refusal OK" || fail "got: $out"

step "Gemini empty hits → no_hits (refusal, not crash)"
out=$($COMPOSE exec -T worker python -c "
import asyncio
from kb.query.generate import GeminiGenerator
g = GeminiGenerator(api_key='fake')
out = asyncio.run(g.generate('q', []))
print(out.refused, out.refusal_reason)
" 2>/dev/null || echo "")
[[ "$out" == "True no_hits" ]] && ok "Gemini no_hits OK" || fail "got: $out"

step "parser fail-safes: bad JSON / non-dict / missing answer"
out=$($COMPOSE exec -T worker python -c "
from kb.query.generate import _parse_result
from kb.query.rrf import Hit
hits = [Hit(id='1', kind='chunk', score=0.5, snippet='s', metadata={})]
a = _parse_result('not json', hits, 'm').refusal_reason
b = _parse_result('[1,2]', hits, 'm').refusal_reason
c = _parse_result('{\"foo\":1}', hits, 'm').refusal_reason
d = _parse_result('{\"answer\":\"\"}', hits, 'm').refusal_reason
print(a, b, c, d)
" 2>/dev/null || echo "")
[[ "$out" == "parse_error parse_error parse_error parse_error" ]] && ok "parser fail-safes OK" || fail "got: $out"

step "factory: explicit gemini without key raises ValueError"
err=$($COMPOSE exec -T -e KB_GEMINI_API_KEY= -e KB_QUERY_LLM=gemini worker python -c "
from kb.query.generate import make_generator
try: make_generator()
except ValueError as e: print('ValueError:', str(e)[:60])
" 2>/dev/null || echo "")
[[ "$err" == ValueError:* ]] && ok "loud-fail on missing key" || fail "got: $err"

step "factory: anthropic → Identity (decision #14, Wave A defer)"
out=$($COMPOSE exec -T -e KB_QUERY_LLM=anthropic worker python -c "
from kb.query.generate import make_generator, IdentityGenerator
g = make_generator()
print('identity' if isinstance(g, IdentityGenerator) else 'other')
" 2>/dev/null || echo "")
[[ "$out" == "identity" ]] && ok "anthropic → Identity (Wave A)" || fail "got: $out"

step "factory: auto without Gemini key → Identity"
out=$($COMPOSE exec -T -e KB_GEMINI_API_KEY= -e KB_QUERY_LLM=auto worker python -c "
from kb.query.generate import make_generator, IdentityGenerator
g = make_generator()
print('identity' if isinstance(g, IdentityGenerator) else 'other')
" 2>/dev/null || echo "")
[[ "$out" == "identity" ]] && ok "auto → Identity" || fail "got: $out"

step "pytest — Phase 8e"
if uv run pytest tests/test_query_generate_unit.py -q >/tmp/kb-verify-8e-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-8e-pytest.log)"
else
    fail "pytest failed"
    tail -40 /tmp/kb-verify-8e-pytest.log >&2
fi

echo
echo "[verify-8e] === SUMMARY ==="
echo "[verify-8e] checks passed: $CHECKS_PASSED"
echo "[verify-8e] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-8e] Phase 8e G5: GREEN ✅"
else
    echo "[verify-8e] Phase 8e G5: FAILED ❌"
fi
