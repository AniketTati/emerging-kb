#!/usr/bin/env bash
# Phase 3b G5 — end-to-end verification.
#
# Two stacks (same pattern as 0/1a/1b/1c/2a/2b/3a):
#   1. docker-compose smoke — confirms 0010_contextual_chunks.sql applied,
#      chained contextualize_file task ran, lifecycle reached 'contextualized'.
#   2. pytest over testcontainers (Phase 3b test files only).
#
# Phase 3b's added surface: CREATE TABLE contextual_chunks + RLS +
# contextualize_file Procrastinate task + chained-defer from chunk_file's
# success path. NO new HTTP endpoints (lifecycle_state enum widens only).
#
# Adapter selection (Phase 3b-bis §5.8.1 #2):
#   - KB_GEMINI_API_KEY set in .env → GeminiContextualizer (model_id='gemini-2.5-flash')
#   - else KB_ANTHROPIC_API_KEY set  → AnthropicContextualizer
#   - else                            → IdentityContextualizer (degraded mode)
# Verify branches on which path the auto-selector picked.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-3b] .env not found; copying from .env.example"
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
    echo "[verify-3b] === step $n: $* ==="
}

ok() {
    echo "[verify-3b]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-3b]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-3b] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-3b] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-3b] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose
# ----------------------------------------------------------------------------

step "compose build + up"
$COMPOSE build >/tmp/kb-verify-3b-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-3b-up.log 2>&1
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

# ---------------------------------------------------------------------------
# Adapter-selection sanity (Phase 3b-bis §5.8.1 #2)
# ---------------------------------------------------------------------------

step "compose env: contextualizer adapter probe (KB_CONTEXTUALIZER, KB_GEMINI_API_KEY, KB_ANTHROPIC_API_KEY presence in worker)"
worker_env=$($COMPOSE exec -T worker sh -c 'echo "KB_CONTEXTUALIZER=${KB_CONTEXTUALIZER:-<unset>}"; echo "KB_GEMINI_API_KEY=$([ -n "$KB_GEMINI_API_KEY" ] && echo set || echo unset)"; echo "KB_ANTHROPIC_API_KEY=$([ -n "$KB_ANTHROPIC_API_KEY" ] && echo set || echo unset)"' 2>/dev/null || echo "")
if [[ -n "$worker_env" ]]; then
    ok "worker env probe: $(echo "$worker_env" | tr '\n' ' ')"
else
    fail "could not probe worker env"
fi

# ---------------------------------------------------------------------------
# DDL invariants — 0010_contextual_chunks.sql applied
# ---------------------------------------------------------------------------

step "psql: contextual_chunks table exists with workspace_id + RLS forced"
exists=$(DB_PSQL -tA -c "SELECT relrowsecurity::text || '|' || relforcerowsecurity::text FROM pg_class WHERE relname='contextual_chunks';" | tr -d '[:space:]')
[[ "$exists" == "true|true" ]] && ok "contextual_chunks: RLS forced" || fail "contextual_chunks RLS state wrong: '$exists'"

step "psql: contextual_chunks UNIQUE (chunk_id) constraint present"
cnt=$(DB_PSQL -tA -c "SELECT count(*) FROM pg_constraint WHERE conrelid='contextual_chunks'::regclass AND contype='u';" | tr -d '[:space:]')
[[ "$cnt" -ge 1 ]] && ok "UNIQUE (chunk_id) constraint present ($cnt)" || fail "no UNIQUE constraint on contextual_chunks"

step "psql: kb_app cannot UPDATE or DELETE on contextual_chunks"
upd=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','contextual_chunks','UPDATE')::text;" | tr -d '[:space:]')
del=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','contextual_chunks','DELETE')::text;" | tr -d '[:space:]')
sel=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','contextual_chunks','SELECT')::text;" | tr -d '[:space:]')
ins=$(DB_PSQL -tA -c "SELECT has_table_privilege('kb_app','contextual_chunks','INSERT')::text;" | tr -d '[:space:]')
if [[ "$upd" == "false" && "$del" == "false" && "$sel" == "true" && "$ins" == "true" ]]; then
    ok "kb_app grants: SELECT=true INSERT=true UPDATE=false DELETE=false"
else
    fail "kb_app grants wrong: SELECT=$sel INSERT=$ins UPDATE=$upd DELETE=$del"
fi

step "psql: files.lifecycle_state CHECK includes 'contextualized'"
chk=$(DB_PSQL -tA -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='files'::regclass AND conname='files_lifecycle_state_check';")
if [[ "$chk" == *"contextualized"* ]]; then
    ok "lifecycle_state CHECK includes 'contextualized'"
else
    fail "CHECK doesn't include 'contextualized': $chk"
fi

# ---------------------------------------------------------------------------
# E2E: PDF (Docling) → parsed → chunked → contextualized (IdentityContextualizer)
# ---------------------------------------------------------------------------

step "curl: POST tiny.pdf → 201"
pdf_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.pdf;type=application/pdf")
pdf_id=$(echo "$pdf_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
[[ -n "$pdf_id" ]] && ok "tiny.pdf uploaded id=$pdf_id" || { fail "POST tiny.pdf failed: $pdf_resp"; pdf_id=""; }

step "wait for tiny.pdf to reach lifecycle_state='contextualized' (≤6 min — Docling first run + chunk + contextualize chain)"
contextualized=0
for _ in $(seq 1 180); do
    if [[ -z "$pdf_id" ]]; then break; fi
    s=$(curl -sS "http://localhost:8000/files/$pdf_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3c chained embed_file may race past 'contextualized' to 'embedded'.
    if [[ "$s" == "contextualized" || "$s" == "embedded" || "$s" == "ready" ]]; then contextualized=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( contextualized == 1 )) && ok "tiny.pdf parsed + chunked + contextualized" || fail "tiny.pdf didn't reach contextualized (last state: $s)"

step "psql: contextual_chunks rows present for tiny.pdf"
ctx_count=$(DB_PSQL -tA -c "SELECT count(*) FROM contextual_chunks WHERE file_id = '$pdf_id';" | tr -d '[:space:]')
[[ "$ctx_count" -ge 1 ]] && ok "$ctx_count contextual_chunk(s) for tiny.pdf" || fail "no contextual_chunks for tiny.pdf"

# Branch on which adapter the auto-selector picked. The compose `api` +
# `worker` services inherit env via `env_file: .env`, so anything in .env
# is in the container env. Mirror the factory's auto-probe order here:
# Gemini → Anthropic → Identity (Gemini-first per §5.8.1 #2).
if [[ -n "${KB_GEMINI_API_KEY:-}" && "${KB_CONTEXTUALIZER:-auto}" != "anthropic" && "${KB_CONTEXTUALIZER:-auto}" != "identity" ]]; then
    EXPECTED_MODEL_ID="gemini-2.5-flash"
    EXPECTED_ADAPTER="gemini"
elif [[ -n "${KB_ANTHROPIC_API_KEY:-}" && "${KB_CONTEXTUALIZER:-auto}" != "gemini" && "${KB_CONTEXTUALIZER:-auto}" != "identity" ]]; then
    EXPECTED_MODEL_ID="claude-opus-4-7"
    EXPECTED_ADAPTER="anthropic"
else
    EXPECTED_MODEL_ID="identity"
    EXPECTED_ADAPTER="identity"
fi

step "psql: model_id matches expected adapter ($EXPECTED_ADAPTER → model_id=$EXPECTED_MODEL_ID)"
model_ids=$(DB_PSQL -tA -c "SELECT DISTINCT model_id FROM contextual_chunks WHERE file_id = '$pdf_id';")
if [[ "$model_ids" == "$EXPECTED_MODEL_ID" ]]; then
    ok "model_id=$model_ids ($EXPECTED_ADAPTER adapter ran as expected)"
else
    fail "expected model_id='$EXPECTED_MODEL_ID'; got '$model_ids'"
fi

if [[ "$EXPECTED_ADAPTER" == "identity" ]]; then
    step "psql: contextual_text matches chunks.text for identity path (no prefix)"
    mismatch=$(DB_PSQL -tA -c "
        SELECT count(*) FROM contextual_chunks cc
        JOIN chunks c ON cc.chunk_id = c.id
        WHERE cc.file_id = '$pdf_id' AND cc.contextual_text <> c.text;
    " | tr -d '[:space:]')
    [[ "$mismatch" == "0" ]] && ok "every contextual_text == chunk text (identity fallback)" || fail "$mismatch row(s) where contextual_text != chunk text"
else
    # Phase 3b-bis §5.8.1 decision #4: real adapters must emit a non-empty
    # prefix + record billed-input tokens in cache_creation_input_tokens.
    # Gemini path: cache_read_input_tokens=0 (no explicit cache at demo
    # scale). Anthropic path: cache_read may be > 0 on the 2nd+ chunks.
    step "psql: contextual_text is prefix + chunk for $EXPECTED_ADAPTER path"
    prefix_present=$(DB_PSQL -tA -c "
        SELECT count(*) FROM contextual_chunks cc
        JOIN chunks c ON cc.chunk_id = c.id
        WHERE cc.file_id = '$pdf_id'
          AND cc.contextual_prefix <> ''
          AND cc.contextual_text LIKE '%' || c.text;
    " | tr -d '[:space:]')
    if [[ "$prefix_present" -ge 1 ]]; then
        ok "$prefix_present row(s) have non-empty prefix + contextual_text ends with chunk text"
    else
        fail "no contextual_chunks row has prefix + chunk-suffix structure"
    fi

    step "psql: $EXPECTED_ADAPTER path recorded billed-input tokens"
    billed=$(DB_PSQL -tA -c "
        SELECT min(cache_creation_input_tokens) FROM contextual_chunks
        WHERE file_id = '$pdf_id';
    " | tr -d '[:space:]')
    if [[ "$billed" -gt 0 ]]; then
        ok "cache_creation_input_tokens > 0 (min=$billed) — billed input recorded"
    else
        fail "expected cache_creation_input_tokens > 0; min=$billed"
    fi

    if [[ "$EXPECTED_ADAPTER" == "gemini" ]]; then
        step "psql: Gemini path keeps cache_read_input_tokens = 0 (no explicit cache)"
        cache_read_max=$(DB_PSQL -tA -c "
            SELECT max(cache_read_input_tokens) FROM contextual_chunks
            WHERE file_id = '$pdf_id';
        " | tr -d '[:space:]')
        if [[ "$cache_read_max" == "0" ]]; then
            ok "cache_read_input_tokens=0 on all Gemini rows (§5.8.1 #4 semantics)"
        else
            fail "expected cache_read=0 for Gemini path; max=$cache_read_max"
        fi
    fi
fi

step "psql: lifecycle history shows ...→chunked→contextualized"
events=$(DB_PSQL -tA -c "SELECT string_agg(to_state, ',' ORDER BY created_at) FROM file_lifecycle WHERE file_id = '$pdf_id';")
if [[ "$events" == *"chunked,contextualized"* ]]; then
    ok "lifecycle progression includes chunked→contextualized"
else
    fail "unexpected lifecycle: $events"
fi

# ---------------------------------------------------------------------------
# Idempotency — re-deferring contextualize_file is a no-op
# ---------------------------------------------------------------------------

step "psql: re-defer contextualize_file → no duplicate contextualization_done event"
$COMPOSE exec -T worker procrastinate \
    --app=kb.workers.app.app defer kb.workers.tasks.contextualize_file \
    "{\"file_id\":\"$pdf_id\"}" >/tmp/kb-verify-3b-defer.log 2>&1 || true
sleep 6
done_count=$(DB_PSQL -tA -c "SELECT count(*) FROM file_lifecycle WHERE file_id='$pdf_id' AND event='contextualization_done';" | tr -d '[:space:]')
[[ "$done_count" == "1" ]] && ok "exactly one contextualization_done event (idempotent re-run)" || fail "expected 1 contextualization_done; got $done_count"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 3b test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 3b test files over testcontainers"
phase_3b_tests=(
    tests/test_contextualization_unit.py
    tests/test_contextualization_worker.py
)
if uv run pytest "${phase_3b_tests[@]}" -q >/tmp/kb-verify-3b-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-3b-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-3b-pytest.log)"
    tail -30 /tmp/kb-verify-3b-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-3b] === SUMMARY ==="
echo "[verify-3b] checks passed: $CHECKS_PASSED"
echo "[verify-3b] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-3b] Phase 3b G5: GREEN ✅"
else
    echo "[verify-3b] Phase 3b G5: FAILED ❌"
fi
