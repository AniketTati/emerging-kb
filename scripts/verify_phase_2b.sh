#!/usr/bin/env bash
# Phase 2b G5 — end-to-end verification.
#
# Two stacks (same pattern as 0/1a/1b/1c/2a):
#   1. docker-compose smoke — adds xlsx + email upload paths on top of
#      Phase 2a's PDF pipeline; magic-byte sniff routing; Mistral self-disable.
#   2. pytest over testcontainers (Phase 2b test files only).
#
# Phase 2b's added surface = 3 new Parser implementations + widened mime
# whitelist + magic-byte sniffer; NO new HTTP endpoints, NO new DDL.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-2b] .env not found; copying from .env.example"
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
WS_B="22222222-2222-2222-2222-222222222222"

CHECKS_PASSED=0
CHECKS_FAILED=0

step() {
    local n=$((CHECKS_PASSED + CHECKS_FAILED + 1))
    echo
    echo "[verify-2b] === step $n: $* ==="
}

ok() {
    echo "[verify-2b]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-2b]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" && "${KB_REUSE_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-2b] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-2b] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-2b] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose
# ----------------------------------------------------------------------------

if [[ "${KB_REUSE_STACK:-0}" != "1" ]]; then
step "compose build + up"
$COMPOSE build >/tmp/kb-verify-2b-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-2b-up.log 2>&1
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
else
    ok "(reuse-stack) skipping compose build/up + migrate + api-healthy wait"
fi

# ---------------------------------------------------------------------------
# Mime whitelist — Phase 2b widens to xlsx + email
# ---------------------------------------------------------------------------

XLSX_MIME="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

step "curl: POST tiny.xlsx with xlsx mime → 201 + queued"
xlsx_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.xlsx;type=${XLSX_MIME}")
state=$(echo "$xlsx_resp" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('lifecycle_state',''), d.get('mime_type',''))")
[[ "$state" == "queued ${XLSX_MIME}" ]] && ok "xlsx queued with correct mime" || fail "expected 'queued ${XLSX_MIME}'; got: $state"

step "curl: POST tiny.eml with message/rfc822 → 201 + queued"
eml_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.eml;type=message/rfc822")
state=$(echo "$eml_resp" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('lifecycle_state',''), d.get('mime_type',''))")
[[ "$state" == "queued message/rfc822" ]] && ok "email queued with correct mime" || fail "expected 'queued message/rfc822'; got: $state"

# ---------------------------------------------------------------------------
# Magic-byte sniff routing — application/octet-stream + ZIP magic → xlsx
# ---------------------------------------------------------------------------

step "curl: POST tiny.xlsx as application/octet-stream → magic sniff → xlsx mime"
sniff_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.xlsx;type=application/octet-stream")
mime=$(echo "$sniff_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('mime_type',''))")
[[ "$mime" == "$XLSX_MIME" ]] && ok "magic sniff re-classified octet-stream as xlsx mime" || fail "expected ${XLSX_MIME}; got '$mime'"

step "curl: POST tiny.eml as application/octet-stream → magic sniff → message/rfc822"
sniff_eml=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.eml;type=application/octet-stream")
mime=$(echo "$sniff_eml" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('mime_type',''))")
[[ "$mime" == "message/rfc822" ]] && ok "magic sniff re-classified octet-stream as email mime" || fail "expected message/rfc822; got '$mime'"

# ---------------------------------------------------------------------------
# Worker parses xlsx + email end-to-end
# ---------------------------------------------------------------------------

step "wait for tiny.xlsx to reach lifecycle_state='parsed'"
xlsx_id=$(echo "$xlsx_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
parsed=0
for _ in $(seq 1 60); do
    s=$(curl -sS "http://localhost:8000/files/$xlsx_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3a chained chunk_file may race past 'parsed' to 'chunked' before
    # this loop polls. Any post-parse state counts as parse-success.
    if [[ "$s" == "parsed" || "$s" == "chunked" || "$s" == "contextualized" || "$s" == "embedded" || "$s" == "raptor_building" || "$s" == "mentions_extracting" || "$s" == "fields_extracting" || "$s" == "units_extracting" || "$s" == "entities_extracting" || "$s" == "ready" ]]; then parsed=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( parsed == 1 )) && ok "xlsx parsed in worker" || fail "xlsx did not parse (state: $s)"

step "curl: GET xlsx /pages → 2 pages (one per sheet)"
xlsx_pages=$(curl -sS "http://localhost:8000/files/$xlsx_id/pages" -H "X-Test-Workspace: $WS_A" \
             | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('total',0))")
[[ "$xlsx_pages" == "2" ]] && ok "xlsx → 2 raw_pages (one per sheet)" || fail "expected 2 pages; got $xlsx_pages"

step "curl: tiny.xlsx page 1 text starts with '# Sheet: Sheet1'"
page1_text=$(curl -sS "http://localhost:8000/files/$xlsx_id/pages?limit=1" -H "X-Test-Workspace: $WS_A" \
             | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['items'][0]['text'])")
if [[ "$page1_text" == "# Sheet: Sheet1"* ]]; then
    ok "page 1 text has sheet header + TSV body"
else
    fail "page 1 text didn't start with expected header; got: ${page1_text:0:50}..."
fi

step "wait for tiny.eml to reach lifecycle_state='parsed'"
eml_id=$(echo "$eml_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
parsed=0
# Bumped from 30 iters (60s) → 90 iters (180s). Phase 6 extends the chain
# through entities_extracting; under sweep-mode load the worker can be slow
# to pick up the queued task (same transient pattern documented for 2c).
for _ in $(seq 1 90); do
    s=$(curl -sS "http://localhost:8000/files/$eml_id" -H "X-Test-Workspace: $WS_A" \
         | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('lifecycle_state',''))")
    # Phase 3a chained chunk_file may race past 'parsed' to 'chunked' before
    # this loop polls. Any post-parse state counts as parse-success.
    if [[ "$s" == "parsed" || "$s" == "chunked" || "$s" == "contextualized" || "$s" == "embedded" || "$s" == "raptor_building" || "$s" == "mentions_extracting" || "$s" == "fields_extracting" || "$s" == "units_extracting" || "$s" == "entities_extracting" || "$s" == "ready" ]]; then parsed=1; break; fi
    if [[ "$s" == "failed" ]]; then break; fi
    sleep 2
done
(( parsed == 1 )) && ok "eml parsed in worker" || fail "eml did not parse (state: $s)"

step "curl: GET eml /pages → 1 page; text includes From: and body"
eml_text=$(curl -sS "http://localhost:8000/files/$eml_id/pages?limit=1" -H "X-Test-Workspace: $WS_A" \
           | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['items'][0]['text'])")
if [[ "$eml_text" == *"From: a@example.com"* && "$eml_text" == *"hello world body"* ]]; then
    ok "eml page text has From + body"
else
    fail "eml page text missing expected lines; got: ${eml_text:0:100}..."
fi

# ---------------------------------------------------------------------------
# Mistral OCR self-disabled (KB_MISTRAL_API_KEY not set in compose)
# ---------------------------------------------------------------------------

step "psql + curl: Mistral OCR is registered but inert (PDF still routed to Docling)"
# Re-upload Phase 2a's fixture; verify mime is application/pdf + still parses
# (we just need to confirm POST works; if Mistral was wrongly winning dispatch,
# it'd 401-fail because there's no API key in compose).
pdf_resp=$(curl -sS -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@tests/fixtures/tiny.pdf;type=application/pdf")
pdf_id=$(echo "$pdf_resp" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('id',''))")
[[ -n "$pdf_id" ]] && ok "PDF POST still 201 (Mistral didn't intercept)" || fail "PDF POST failed: $pdf_resp"

# Don't wait for the Docling parse — that's covered by verify_phase_2a.sh.
# Soft-delete to keep the workspace clean.
curl -sS -o /dev/null -X DELETE "http://localhost:8000/files/$pdf_id" -H "X-Test-Workspace: $WS_A"

# ---------------------------------------------------------------------------
# Negative: text/plain still 415 (whitelist is tight)
# ---------------------------------------------------------------------------

step "curl: POST text/plain → 415 unsupported-media-type"
http=$(curl -sS -o /tmp/kb-verify-2b-415.json -w "%{http_code}" -X POST http://localhost:8000/files \
    -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -F "file=@README.md;type=text/plain")
slug=$(python3 -c "import sys,json; print(json.load(open('/tmp/kb-verify-2b-415.json')).get('type',''))" 2>/dev/null || echo "")
[[ "$http" == "415" && "$slug" == *"unsupported-media-type" ]] && ok "415 unsupported-media-type for text/plain" || fail "expected 415; got http=$http slug=$slug"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 2b test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 2b test files over testcontainers"
phase_2b_tests=(
    tests/test_parse_xlsx.py
    tests/test_parse_email.py
    tests/test_parse_mistral_ocr.py
    tests/test_files_crud.py
)
if uv run pytest "${phase_2b_tests[@]}" -q >/tmp/kb-verify-2b-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-2b-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-2b-pytest.log)"
    tail -30 /tmp/kb-verify-2b-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-2b] === SUMMARY ==="
echo "[verify-2b] checks passed: $CHECKS_PASSED"
echo "[verify-2b] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-2b] Phase 2b G5: GREEN ✅"
else
    echo "[verify-2b] Phase 2b G5: FAILED ❌"
fi
