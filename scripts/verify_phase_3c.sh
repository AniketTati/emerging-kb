#!/usr/bin/env bash
# Phase 3c G5 — end-to-end verification.
#
# Two stacks (same pattern as 0/1a/1b/1c/2a/2b/3a/3b):
#   1. docker-compose smoke — confirms 0011_chunk_embeddings.sql applied,
#      chained embed_file task ran, lifecycle reached 'embedded'.
#   2. pytest over testcontainers (Phase 3c test files only).
#
# KB_GEMINI_API_KEY is NOT set in compose — uses DeterministicMockEmbedder
# (model_id='mock-deterministic-v1'); verifies the degraded-mode path.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    cp .env.example .env
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE="docker compose"
DB_PSQL() {
    $COMPOSE exec -T db psql -U "${KB_POSTGRES_USER:-kb}" -d "${KB_POSTGRES_DB:-kb}" "$@"
}

WS_A="11111111-1111-1111-1111-111111111111"

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-3c] === step $n: $* ==="
}

ok() {
    echo "[verify-3c]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-3c]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-3c] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo "[verify-3c] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        exit $rc
    fi
}

trap cleanup EXIT

step "compose build + up"
$COMPOSE build >/tmp/kb-verify-3c-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-3c-up.log 2>&1
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
(( migrate_ok == 1 )) && ok "migrate exited 0" || fail "migrate did not exit cleanly within 120s"

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

# DDL invariants — 0011_chunk_embeddings.sql applied
step "psql: chunk_embeddings table exists with workspace_id + RLS forced"
exists=$(DB_PSQL -tA -c "SELECT relrowsecurity::text || '|' || relforcerowsecurity::text FROM pg_class WHERE relname='chunk_embeddings';" | tr -d '[:space:]')
[[ "$exists" == "true|true" ]] && ok "chunk_embeddings: RLS forced" || fail "chunk_embeddings RLS state wrong: '$exists'"

step "psql: chunk_embeddings UNIQUE (contextual_chunk_id, model_id) constraint present"
cnt=$(DB_PSQL -tA -c "SELECT count(*) FROM pg_constraint WHERE conrelid='chunk_embeddings'::regclass AND contype='u';" | tr -d '[:space:]')
[[ "$cnt" -ge 1 ]] && ok "UNIQUE constraint present ($cnt)" || fail "no UNIQUE constraint"

step "psql: kb_app cannot UPDATE or DELETE on chunk_embeddings"
upd=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunk_embeddings','UPDATE')::text;" | tr -d '[:space:]')
del=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunk_embeddings','DELETE')::text;" | tr -d '[:space:]')
sel=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunk_embeddings','SELECT')::text;" | tr -d '[:space:]')
ins=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','chunk_embeddings','INSERT')::text;" | tr -d '[:space:]')
if [[ "$upd" == "false" && "$del" == "false" && "$sel" == "true" && "$ins" == "true" ]]; then
    ok "kb_app grants: SELECT=true INSERT=true UPDATE=false DELETE=false"
else
    fail "kb_app grants wrong: SELECT=$sel INSERT=$ins UPDATE=$upd DELETE=$del"
fi

step "psql: chunk_embeddings.embedding is halfvec type"
udt=$(DB_PSQL -tA -c "SELECT udt_name FROM information_schema.columns WHERE table_name='chunk_embeddings' AND column_name='embedding';" | tr -d '[:space:]')
[[ "$udt" == "halfvec" ]] && ok "embedding column is halfvec" || fail "expected halfvec, got '$udt'"

step "psql: files.lifecycle_state CHECK includes 'embedded'"
chk=$(DB_PSQL -tA -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='files'::regclass AND conname='files_lifecycle_state_check';")
if [[ "$chk" == *"embedded"* ]]; then
    ok "lifecycle_state CHECK includes 'embedded'"
else
    fail "CHECK doesn't include 'embedded': $chk"
fi

# E2E: PDF → parsed → chunked → contextualized → embedded
step "curl: POST tiny.pdf → 201"
pdf_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.pdf;type=application/pdf")
pdf_id=$(echo "$pdf_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
[[ -n "$pdf_id" ]] && ok "tiny.pdf uploaded id=$pdf_id" || { fail "POST tiny.pdf failed: $pdf_resp"; pdf_id=""; }

step "wait for tiny.pdf to reach lifecycle_state='embedded' (≤6 min — Docling first run + chunk + contextualize + embed chain)"
embedded=0
for _ in $(seq 1 180); do
    if [[ -z "$pdf_id" ]]; then break; fi
    s=$(curl -sS "http://localhost:8000/files/$pdf_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    if [[ "$s" == "embedded" ]]; then embedded=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( embedded == 1 )) && ok "tiny.pdf parsed + chunked + contextualized + embedded" || fail "tiny.pdf didn't reach embedded (last state: $s)"

step "psql: chunk_embeddings rows present for tiny.pdf with dim 3072"
emb_count=$(DB_PSQL -tA -c "SELECT count(*) FROM chunk_embeddings WHERE file_id = '$pdf_id';" | tr -d '[:space:]')
[[ "$emb_count" -ge 1 ]] && ok "$emb_count chunk_embedding(s) for tiny.pdf" || fail "no chunk_embeddings for tiny.pdf"

step "psql: model_id='mock-deterministic-v1' (no API key in compose → mock embedder)"
model_ids=$(DB_PSQL -tA -c "SELECT DISTINCT model_id FROM chunk_embeddings WHERE file_id = '$pdf_id';")
if [[ "$model_ids" == "mock-deterministic-v1" ]]; then
    ok "model_id=mock-deterministic-v1 (DeterministicMockEmbedder fallback ran as expected)"
else
    fail "expected model_id='mock-deterministic-v1'; got '$model_ids'"
fi

step "psql: lifecycle history shows ...→embedded"
events=$(DB_PSQL -tA -c "SELECT string_agg(to_state, ',' ORDER BY created_at) FROM file_lifecycle WHERE file_id = '$pdf_id';")
if [[ "$events" == *"contextualized,embedded"* ]]; then
    ok "lifecycle progression includes contextualized→embedded"
else
    fail "unexpected lifecycle: $events"
fi

# Idempotency
step "psql: re-defer embed_file → no duplicate embedding_done event"
$COMPOSE exec -T worker procrastinate \
    --app=kb.workers.app.app defer kb.workers.tasks.embed_file \
    "{\"file_id\":\"$pdf_id\"}" >/tmp/kb-verify-3c-defer.log 2>&1 || true
sleep 6
done_count=$(DB_PSQL -tA -c "SELECT count(*) FROM file_lifecycle WHERE file_id='$pdf_id' AND event='embedding_done';" | tr -d '[:space:]')
[[ "$done_count" == "1" ]] && ok "exactly one embedding_done event (idempotent re-run)" || fail "expected 1 embedding_done; got $done_count"

# pytest
step "pytest — Phase 3c test files over testcontainers"
phase_3c_tests=(
    tests/test_embeddings_unit.py
    tests/test_embeddings_worker.py
)
if uv run pytest "${phase_3c_tests[@]}" -q >/tmp/kb-verify-3c-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-3c-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-3c-pytest.log)"
    tail -30 /tmp/kb-verify-3c-pytest.log >&2
fi

echo
echo "[verify-3c] === SUMMARY ==="
echo "[verify-3c] checks passed: $CHECKS_PASSED"
echo "[verify-3c] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-3c] Phase 3c G5: GREEN ✅"
else
    echo "[verify-3c] Phase 3c G5: FAILED ❌"
fi
