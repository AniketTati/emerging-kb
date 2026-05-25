#!/usr/bin/env bash
# Phase 9 G5 — SSE upload status + GET /audit + chat replay SSE.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-9] === step $n: $* ==="; }
ok() { echo "[verify-9]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-9]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-9] RESULT: $CHECKS_FAILED failed"; exit 1; fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

WS=$(uuidgen | tr 'A-Z' 'a-z')
API="http://localhost:${KB_API_PORT:-8000}"

DB_PSQL() {
    $COMPOSE exec -T db psql -U "${KB_POSTGRES_USER:-kb}" -d "${KB_POSTGRES_DB:-kb}" "$@"
}

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-9-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-9-up.log 2>&1
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

step "openapi includes /audit + /upload/{file_id}/status + /chat/{query_id}/stream"
out=$(curl -fsS "$API/openapi.json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
need = ['/audit', '/upload/{file_id}/status', '/chat/{query_id}/stream']
print(','.join(p for p in need if p in d['paths']))
")
expected="/audit,/upload/{file_id}/status,/chat/{query_id}/stream"
[[ "$out" == "$expected" ]] && ok "all 3 routes mounted" || fail "got: $out"

step "GET /audit returns empty list on empty workspace"
resp=$(curl -fsS "$API/audit" -H "X-Test-Workspace: $WS" 2>/dev/null)
items=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['items']), d['next_cursor'])")
[[ "$items" == "0 None" ]] && ok "empty workspace = empty list" || fail "got: $items"

step "GET /audit rejects oversize limit"
http=$(curl -s -o /dev/null -w "%{http_code}" "$API/audit?limit=300" -H "X-Test-Workspace: $WS")
[[ "$http" == "400" || "$http" == "422" ]] && ok "oversize limit rejected with $http" || fail "got HTTP $http"

step "POST /chat then GET /audit lists the row"
resp=$(curl -fsS -X POST "$API/chat" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -d '{"query":"verify-9 chat-audit"}' 2>/dev/null)
qid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['query_id'])")
[[ -n "$qid" && ${#qid} -ge 30 ]] && ok "chat returned query_id=$qid" || fail "got: $resp"

resp=$(curl -fsS "$API/audit" -H "X-Test-Workspace: $WS")
audit_qid=$(echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['items'][0]['id'] if d['items'] else '')
")
[[ "$audit_qid" == "$qid" ]] && ok "audit lists the chat query_id" || fail "expected $qid got $audit_qid"

step "GET /chat/:qid/stream returns 200 text/event-stream"
ct=$(curl -fsS -D - "$API/chat/$qid/stream" -H "X-Test-Workspace: $WS" -o /dev/null 2>/dev/null | grep -i "content-type" | head -1)
echo "$ct" | grep -qi "text/event-stream" && ok "Content-Type is event-stream" || fail "got: $ct"

step "GET /chat/:qid/stream emits done event"
body=$(curl -fsS "$API/chat/$qid/stream" -H "X-Test-Workspace: $WS" 2>/dev/null)
echo "$body" | grep -q "^event: done" && ok "saw 'event: done'" || fail "no done event; body excerpt: ${body:0:200}"

step "GET /chat/:qid/stream 404 on unknown query_id"
http=$(curl -s -o /dev/null -w "%{http_code}" "$API/chat/00000000-0000-0000-0000-000000000000/stream" -H "X-Test-Workspace: $WS")
[[ "$http" == "404" ]] && ok "404 on unknown query_id" || fail "got HTTP $http"

step "POST tiny.pdf → SSE /upload/:id/status streams lifecycle to ready"
upload_resp=$(curl -sS -X POST "$API/files" \
    -H "X-Test-Workspace: $WS" \
    -H "Idempotency-Key: verify9-upload-$(date +%s)" \
    -F file=@tests/fixtures/tiny.pdf 2>/dev/null)
file_id=$(echo "$upload_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))")
[[ -n "$file_id" ]] && ok "uploaded file_id=$file_id" || fail "got: $upload_resp"

# Wait until file reaches ready (worker processes the pipeline)
for _ in $(seq 1 90); do
    state=$(DB_PSQL -tA -c "SELECT lifecycle_state FROM files WHERE id='$file_id'" 2>/dev/null | tr -d ' ')
    if [[ "$state" == "ready" || "$state" == "failed" ]]; then break; fi
    sleep 2
done
[[ "$state" == "ready" ]] && ok "file reached ready (worker chain end-to-end)" || fail "got state: $state"

# Now SSE — since file is already at terminal state, stream emits all events + done
sse_body=$(curl -fsS --max-time 30 "$API/upload/$file_id/status" -H "X-Test-Workspace: $WS" 2>/dev/null)
lifecycle_count=$(echo "$sse_body" | grep -c "^event: lifecycle" || true)
done_seen=$(echo "$sse_body" | grep -c "^event: done" || true)
[[ "$lifecycle_count" -ge "3" && "$done_seen" -ge "1" ]] && \
    ok "SSE: $lifecycle_count lifecycle events + done" || \
    fail "lifecycle=$lifecycle_count done=$done_seen; body: ${sse_body:0:300}"

step "GET /upload/:id/status 404 on unknown file_id"
http=$(curl -s -o /dev/null -w "%{http_code}" "$API/upload/00000000-0000-0000-0000-000000000000/status" -H "X-Test-Workspace: $WS")
[[ "$http" == "404" ]] && ok "404 on unknown file_id" || fail "got HTTP $http"

step "pytest — Phase 9"
if uv run pytest tests/test_audit_unit.py tests/test_sse_unit.py -q >/tmp/kb-verify-9-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-9-pytest.log)"
else
    fail "pytest failed"
    tail -40 /tmp/kb-verify-9-pytest.log >&2
fi

echo
echo "[verify-9] === SUMMARY ==="
echo "[verify-9] checks passed: $CHECKS_PASSED"
echo "[verify-9] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-9] Phase 9 G5: GREEN ✅"
else
    echo "[verify-9] Phase 9 G5: FAILED ❌"
fi
