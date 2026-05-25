#!/usr/bin/env bash
# Phase 10b G5 — Next.js Chat UI + UI-driven pipeline integration test.
#
# Steps:
#   1. docker compose up backend (api on :8000)
#   2. ui: vitest unit tests (17 total: 10 upload + 7 chat)
#   3. ui: npm run build
#   4. ui: visual playwright (upload.spec.ts + chat.spec.ts) — no backend dep
#   5. ui: PIPELINE playwright (pipeline.spec.ts) with RUN_PIPELINE_TEST=1
#      → real UI drops a file, SSE reaches ready, chat sends a query,
#        assistant turn renders (refusal or grounded — either proves the
#        whole pipeline ran end-to-end through the UI).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-10b] === step $n: $* ==="; }
ok() { echo "[verify-10b]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-10b]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-10b] RESULT: $CHECKS_FAILED failed"; exit 1; fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up (backend)"
$COMPOSE build >/tmp/kb-verify-10b-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-10b-up.log 2>&1
ok "stack starting"

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

step "ui: vitest unit tests (upload + chat helpers)"
pushd ui >/dev/null
if [[ ! -d node_modules ]]; then npm install >/tmp/kb-verify-10b-npm.log 2>&1; fi
if npm test >/tmp/kb-verify-10b-vitest.log 2>&1; then
    grep "Tests" /tmp/kb-verify-10b-vitest.log | tail -1 | sed 's/^/[verify-10b]   /'
    ok "vitest GREEN"
else
    fail "vitest failed"
    tail -40 /tmp/kb-verify-10b-vitest.log >&2
fi
popd >/dev/null

step "ui: next build"
pushd ui >/dev/null
if npm run build >/tmp/kb-verify-10b-build.log 2>&1; then
    grep -E "Route|Compiled" /tmp/kb-verify-10b-build.log | head -8 | sed 's/^/[verify-10b]   /'
    ok "next build GREEN"
else
    fail "next build failed"
    tail -40 /tmp/kb-verify-10b-build.log >&2
fi
popd >/dev/null

step "ui: install chromium for playwright (idempotent)"
pushd ui >/dev/null
npx playwright install chromium >/tmp/kb-verify-10b-pw-install.log 2>&1
ok "playwright chromium ready"
popd >/dev/null

step "ui: visual playwright (upload.spec.ts + chat.spec.ts)"
pushd ui >/dev/null
mkdir -p tests/artifacts
if npx playwright test tests/upload.spec.ts tests/chat.spec.ts --project=chromium \
        >/tmp/kb-verify-10b-pw-visual.log 2>&1; then
    grep -E "passed" /tmp/kb-verify-10b-pw-visual.log | tail -1 | sed 's/^/[verify-10b]   /'
    ok "visual playwright GREEN"
else
    fail "visual playwright failed"
    tail -40 /tmp/kb-verify-10b-pw-visual.log >&2
fi
popd >/dev/null

step "ui: PIPELINE — drop file via UI → SSE ready → chat query → assistant turn"
pushd ui >/dev/null
if RUN_PIPELINE_TEST=1 npx playwright test tests/pipeline.spec.ts --project=chromium \
        >/tmp/kb-verify-10b-pw-pipeline.log 2>&1; then
    grep -E "passed|failed" /tmp/kb-verify-10b-pw-pipeline.log | tail -3 | sed 's/^/[verify-10b]   /'
    ok "PIPELINE E2E GREEN — UI drove the backend end-to-end"
else
    fail "pipeline test failed"
    tail -80 /tmp/kb-verify-10b-pw-pipeline.log >&2
fi
popd >/dev/null

step "ui: pipeline screenshot saved"
if [[ -f ui/tests/artifacts/pipeline-final.png ]]; then
    bytes=$(stat -f%z ui/tests/artifacts/pipeline-final.png 2>/dev/null || stat -c%s ui/tests/artifacts/pipeline-final.png)
    (( bytes > 1000 )) && ok "pipeline screenshot saved ($bytes bytes)" || fail "screenshot too small: $bytes"
else
    fail "pipeline screenshot missing"
fi

echo
echo "[verify-10b] === SUMMARY ==="
echo "[verify-10b] checks passed: $CHECKS_PASSED"
echo "[verify-10b] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-10b] Phase 10b G5: GREEN ✅"
else
    echo "[verify-10b] Phase 10b G5: FAILED ❌"
fi
