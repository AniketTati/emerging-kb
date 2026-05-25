#!/usr/bin/env bash
# Phase 10a G5 — Next.js Upload UI.
#
# Boots the backend (docker-compose) + Next.js dev server, runs:
#   - Vitest unit tests for ui/lib/api.ts
#   - npm run build (TypeScript + Next compile clean)
#   - Playwright E2E against the running dev server, saving a screenshot
#     to ui/tests/artifacts/upload-empty.png
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-10a] === step $n: $* ==="; }
ok() { echo "[verify-10a]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-10a]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-10a] RESULT: $CHECKS_FAILED failed"; exit 1; fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up (backend for CORS + API endpoints)"
$COMPOSE build >/tmp/kb-verify-10a-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-10a-up.log 2>&1
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

step "node + npm available"
command -v node >/dev/null && ok "node $(node --version)" || fail "node not found"
command -v npm >/dev/null && ok "npm $(npm --version)" || fail "npm not found"

step "ui/ npm install (idempotent — uses lockfile if present)"
pushd ui >/dev/null
if [[ -d node_modules ]]; then
    ok "node_modules present — skipping install"
else
    npm install >/tmp/kb-verify-10a-npm.log 2>&1 && ok "npm install" || fail "npm install failed"
fi
popd >/dev/null

step "ui: vitest unit tests"
pushd ui >/dev/null
if npm test >/tmp/kb-verify-10a-vitest.log 2>&1; then
    grep "Tests" /tmp/kb-verify-10a-vitest.log | tail -1 | sed 's/^/[verify-10a]   /'
    ok "vitest GREEN"
else
    fail "vitest failed"
    tail -40 /tmp/kb-verify-10a-vitest.log >&2
fi
popd >/dev/null

step "ui: next build (TypeScript + ESLint clean)"
pushd ui >/dev/null
if npm run build >/tmp/kb-verify-10a-nextbuild.log 2>&1; then
    grep -E "Compiled|Route" /tmp/kb-verify-10a-nextbuild.log | head -8 | sed 's/^/[verify-10a]   /'
    ok "next build GREEN"
else
    fail "next build failed"
    tail -40 /tmp/kb-verify-10a-nextbuild.log >&2
fi
popd >/dev/null

step "ui: cors middleware present on backend (sanity probe)"
out=$(curl -sS -X OPTIONS http://localhost:8000/files \
    -H "Origin: http://localhost:3000" \
    -H "Access-Control-Request-Method: POST" \
    -D - -o /dev/null 2>/dev/null | grep -i "access-control-allow-origin" | head -1)
echo "$out" | grep -qi "http://localhost:3000" && ok "CORS allow-origin: http://localhost:3000" || fail "got: $out"

step "ui: install chromium for playwright (idempotent)"
pushd ui >/dev/null
if npx playwright install chromium >/tmp/kb-verify-10a-pw-install.log 2>&1; then
    ok "playwright chromium ready"
else
    fail "playwright install failed"
    tail -20 /tmp/kb-verify-10a-pw-install.log >&2
fi
popd >/dev/null

step "ui: playwright E2E (boots dev server + asserts page renders + screenshot)"
pushd ui >/dev/null
mkdir -p tests/artifacts
if npx playwright test --project=chromium >/tmp/kb-verify-10a-pw.log 2>&1; then
    grep -E "passed|failed" /tmp/kb-verify-10a-pw.log | tail -3 | sed 's/^/[verify-10a]   /'
    ok "playwright tests GREEN"
else
    fail "playwright tests failed"
    tail -50 /tmp/kb-verify-10a-pw.log >&2
fi
popd >/dev/null

step "ui: screenshot artifact saved"
if [[ -f ui/tests/artifacts/upload-empty.png ]]; then
    bytes=$(stat -f%z ui/tests/artifacts/upload-empty.png 2>/dev/null || stat -c%s ui/tests/artifacts/upload-empty.png)
    (( bytes > 1000 )) && ok "screenshot saved ($bytes bytes)" || fail "screenshot too small: $bytes bytes"
else
    fail "screenshot not saved"
fi

step "no leak: ui/ doesn't ship with backend tests"
leak=$(grep -rl "from kb\\.\|import kb\\." ui/ 2>/dev/null || true)
[[ -z "$leak" ]] && ok "no python backend imports in ui/" || fail "leak:\n$leak"

echo
echo "[verify-10a] === SUMMARY ==="
echo "[verify-10a] checks passed: $CHECKS_PASSED"
echo "[verify-10a] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-10a] Phase 10a G5: GREEN ✅"
else
    echo "[verify-10a] Phase 10a G5: FAILED ❌"
fi
