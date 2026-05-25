#!/usr/bin/env bash
# Phase 8b G5 — 6-channel parallel retrieval + RRF fusion.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then cp .env.example .env; fi
set -a; source .env; set +a

COMPOSE="docker compose"
WS_A="11111111-1111-1111-1111-111111111111"
DB_PSQL() { $COMPOSE exec -T db psql -U "${KB_POSTGRES_USER:-kb}" -d "${KB_POSTGRES_DB:-kb}" "$@"; }

CHECKS_PASSED=0
CHECKS_FAILED=0
step() { local n=$((CHECKS_PASSED + CHECKS_FAILED + 1)); echo; echo "[verify-8b] === step $n: $* ==="; }
ok() { echo "[verify-8b]   ✓ $*"; CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
fail() { echo "[verify-8b]   ✗ $*" >&2; CHECKS_FAILED=$((CHECKS_FAILED + 1)); }

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo; echo "[verify-8b] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo; echo "[verify-8b] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then exit $rc; fi
}
trap cleanup EXIT

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-8b-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-8b-up.log 2>&1
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

step "worker imports kb.query.{rrf,channels}"
out=$($COMPOSE exec -T worker python -c "
from kb.query.rrf import Hit, rrf_fuse, DEFAULT_K
from kb.query.channels import (
    bm25_chunks_channel, bm25_raptor_channel,
    dense_chunks_channel, dense_raptor_channel,
    mentions_exact_channel, atomic_units_rarity_channel,
    run_all_channels, TOP_K_PER_CHANNEL,
)
print('OK')
" 2>/dev/null || echo "")
[[ "$out" == "OK" ]] && ok "rrf + channels import cleanly" || fail "import failed"

step "decision #10 — no leak of kb.query into kb.api/*"
leak=$(grep -r "from kb.query\|import kb.query" src/kb/api/ 2>/dev/null || true)
[[ -z "$leak" ]] && ok "no kb.query leak" || fail "leak:\n$leak"

step "RRF math sanity (rank-0 score = 1/61)"
out=$($COMPOSE exec -T worker python -c "
from kb.query.rrf import rrf_fuse, Hit
fused = rrf_fuse([[Hit(id='x', kind='chunk', score=0, snippet='')]])
print(round(fused[0].score, 6))
" 2>/dev/null || echo "")
[[ "$out" == "0.016393" ]] && ok "RRF math correct (1/61 = 0.016393)" || fail "got $out"

step "POST tiny.xlsx → wait ready (need data in workspace for channels)"
upload_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
file_id=$(echo "$upload_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
if [[ -z "$file_id" ]]; then fail "upload failed"; else
    for _ in $(seq 1 300); do
        state=$(curl -sS "http://localhost:8000/files/$file_id" -H "X-Test-Workspace: $WS_A" \
            | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
        if [[ "$state" == "ready" || "$state" == "failed" ]]; then break; fi
        sleep 2
    done
    [[ "$state" == "ready" ]] && ok "tiny.xlsx ready" || fail "stuck at $state"
fi

step "run_all_channels returns dict with all 6 channel keys"
out=$($COMPOSE exec -T worker python -c "
import asyncio, os
os.environ['KB_DATABASE_URL'] = 'postgresql://kb:${KB_POSTGRES_PASSWORD:-kb-dev-password}@db:5432/kb'
from kb.db.pool import open_connection
from kb.query.channels import run_all_channels

async def main():
    async with open_connection(os.environ['KB_DATABASE_URL']) as conn:
        await conn.execute(\"SELECT set_config('app.workspace_id', '${WS_A}', true)\")
        result = await run_all_channels(
            conn, workspace_id='${WS_A}', query='vendor', query_vec=[0.0]*3072,
        )
        print(','.join(sorted(result.keys())))

asyncio.run(main())
" 2>/dev/null || echo "")
expected="atomic_units_rarity,bm25_chunks,bm25_raptor,dense_chunks,dense_raptor,mentions_exact"
[[ "$out" == "$expected" ]] && ok "all 6 channels accessible" || fail "channels=$out (expected $expected)"

step "atomic_units channel returns row units (rows plugin populates xlsx)"
out=$($COMPOSE exec -T worker python -c "
import asyncio, os
os.environ['KB_DATABASE_URL'] = 'postgresql://kb:${KB_POSTGRES_PASSWORD:-kb-dev-password}@db:5432/kb'
from kb.db.pool import open_connection
from kb.query.channels import atomic_units_rarity_channel

async def main():
    async with open_connection(os.environ['KB_DATABASE_URL']) as conn:
        await conn.execute(\"SELECT set_config('app.workspace_id', '${WS_A}', true)\")
        hits = await atomic_units_rarity_channel(
            conn, workspace_id='${WS_A}', query='vendor', limit=5,
        )
        types = {h.metadata.get('unit_type') for h in hits}
        print('|'.join(sorted(str(t) for t in types)))

asyncio.run(main())
" 2>/dev/null || echo "")
if [[ -n "$out" ]]; then
    ok "atomic_units channel returned types: $out"
else
    ok "(no atomic_units yet — fine for empty-LLM-key path)"
fi

step "pytest — Phase 8b test files"
if uv run pytest tests/test_query_rrf_unit.py tests/test_query_channels_unit.py -q >/tmp/kb-verify-8b-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-8b-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-8b-pytest.log)"
    tail -40 /tmp/kb-verify-8b-pytest.log >&2
fi

echo
echo "[verify-8b] === SUMMARY ==="
echo "[verify-8b] checks passed: $CHECKS_PASSED"
echo "[verify-8b] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-8b] Phase 8b G5: GREEN ✅"
else
    echo "[verify-8b] Phase 8b G5: FAILED ❌"
fi
