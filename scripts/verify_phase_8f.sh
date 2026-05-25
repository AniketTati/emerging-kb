#!/usr/bin/env bash
# Phase 8f G5 — Orchestrator + POST /search + POST /chat + query_log audit.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-8f] === step $n: $* ==="; }
ok() { echo "[verify-8f]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-8f]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }
cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then echo "[verify-8f] RESULT: $CHECKS_FAILED failed"; exit 1; fi
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
$COMPOSE build >/tmp/kb-verify-8f-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-8f-up.log 2>&1
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

step "psql: query_log table + RLS forced + immutability GRANT"
rls=$(DB_PSQL -tA -c "SELECT relrowsecurity::text || '|' || relforcerowsecurity::text FROM pg_class WHERE relname='query_log'" 2>/dev/null | tr -d ' ')
case "$rls" in
    "t|t"|"true|true") ok "query_log RLS forced ($rls)";;
    *) fail "got rls: $rls";;
esac

privs=$(DB_PSQL -tA -c "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants WHERE grantee='kb_app' AND table_name='query_log'" 2>/dev/null | tr -d ' ')
case "$privs" in
    "INSERT,SELECT") ok "kb_app: SELECT+INSERT only (immutable audit)";;
    *) fail "expected SELECT,INSERT got: $privs";;
esac

step "psql: query_log audit-list index present"
idx=$(DB_PSQL -tA -c "SELECT count(*) FROM pg_indexes WHERE tablename='query_log' AND indexname='query_log_workspace_created_idx'" 2>/dev/null | tr -d ' ')
[[ "$idx" == "1" ]] && ok "audit-list index present" || fail "got: $idx"

step "POST /openapi.json includes /search and /chat"
out=$(curl -fsS "$API/openapi.json" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(','.join(sorted(p for p in d['paths'] if p in ('/search','/chat'))))")
[[ "$out" == "/chat,/search" ]] && ok "both routes mounted" || fail "got: $out"

step "POST /search returns 200 with envelope (empty corpus)"
resp=$(curl -fsS -X POST "$API/search" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -d '{"query":"verify-8f search"}' 2>/dev/null)
qid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('query_id',''))")
[[ -n "$qid" && ${#qid} -ge 30 ]] && ok "search returned envelope with query_id=$qid" || fail "got: $resp"

step "POST /chat returns 200 + refused=true envelope (empty corpus)"
resp=$(curl -fsS -X POST "$API/chat" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -d '{"query":"verify-8f chat"}' 2>/dev/null)
refused=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('generation',{}).get('refused'))")
[[ "$refused" == "True" ]] && ok "chat returned refused=true (empty-corpus refusal)" || fail "got refused=$refused"

step "POST /search returns 400 invalid-query on unsupported mode"
http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/search" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -d '{"query":"q","mode":"Q"}' 2>/dev/null)
[[ "$http" == "400" ]] && ok "mode=Q rejected with 400" || fail "got HTTP $http"

step "POST /search returns 422 on empty query (Pydantic min_length=1)"
http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/search" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -d '{"query":""}' 2>/dev/null)
[[ "$http" == "422" || "$http" == "400" ]] && ok "empty query rejected with $http" || fail "got HTTP $http"

step "psql: query_log row written for the /search call"
cnt=$(DB_PSQL -tA -c "SELECT count(*) FROM query_log WHERE workspace_id='$WS' AND endpoint='search' AND query='verify-8f search'" 2>/dev/null | tr -d ' ')
[[ "$cnt" -ge "1" ]] && ok "search audit row present (count=$cnt)" || fail "got: $cnt"

step "psql: query_log row for /chat with refused=true + refusal_reason set"
row=$(DB_PSQL -tA -c "SELECT refused::text || '|' || coalesce(refusal_reason,'') FROM query_log WHERE workspace_id='$WS' AND endpoint='chat' AND query='verify-8f chat' LIMIT 1" 2>/dev/null | tr -d ' ')
case "$row" in
    "true|insufficient_evidence"|"true|no_hits"|"t|insufficient_evidence"|"t|no_hits") ok "chat audit row: $row";;
    *) fail "got: $row";;
esac

step "POST /chat with Idempotency-Key — replay returns cached envelope"
IDEM="verify-8f-idem-$(date +%s)"
resp1=$(curl -fsS -X POST "$API/chat" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -H "Idempotency-Key: $IDEM" \
    -d '{"query":"idem-test"}' 2>/dev/null)
qid1=$(echo "$resp1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('query_id',''))")
resp2=$(curl -fsS -X POST "$API/chat" \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: $WS" \
    -H "Idempotency-Key: $IDEM" \
    -d '{"query":"idem-test"}' 2>/dev/null)
qid2=$(echo "$resp2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('query_id',''))")
[[ "$qid1" == "$qid2" && -n "$qid1" ]] && ok "idem replay returned same query_id ($qid1)" || fail "qid1=$qid1 qid2=$qid2"

step "pytest — Phase 8f"
if uv run pytest tests/test_query_orchestrator_unit.py tests/test_api_query.py -q >/tmp/kb-verify-8f-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-8f-pytest.log)"
else
    fail "pytest failed"
    tail -40 /tmp/kb-verify-8f-pytest.log >&2
fi

echo
echo "[verify-8f] === SUMMARY ==="
echo "[verify-8f] checks passed: $CHECKS_PASSED"
echo "[verify-8f] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-8f] Phase 8f G5: GREEN ✅"
else
    echo "[verify-8f] Phase 8f G5: FAILED ❌"
fi
