#!/usr/bin/env bash
# Phase 1c G5 — end-to-end verification.
#
# Two stacks (same pattern as verify_phase_0.sh / 1a.sh / 1b.sh):
#   1. docker-compose smoke (proves the runnable stack with 0007 applied).
#   2. pytest over testcontainers (Phase 1c test files only).
#
# Phase 1c's added surface = 3 new tables (schema_entities, schema_fields,
# schema_relationships) + 11 new endpoints + extended schema_versions
# snapshot body + rollback restores hierarchy.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[verify-1c] .env not found; copying from .env.example"
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
    echo "[verify-1c] === step $n: $* ==="
}

ok() {
    echo "[verify-1c]   ✓ $*"
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
}

fail() {
    echo "[verify-1c]   ✗ $*" >&2
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
}

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" ]]; then
        echo
        echo "[verify-1c] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    if (( CHECKS_FAILED > 0 )); then
        echo
        echo "[verify-1c] RESULT: $CHECKS_FAILED check(s) failed, $CHECKS_PASSED passed."
        exit 1
    fi
    if [[ $rc -ne 0 ]]; then
        echo "[verify-1c] script exited non-zero before all checks ran"
        exit $rc
    fi
}

trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stack 1: docker compose
# ----------------------------------------------------------------------------

step "compose build + up"
$COMPOSE build >/tmp/kb-verify-1c-build.log 2>&1
$COMPOSE up -d >/tmp/kb-verify-1c-up.log 2>&1
ok "stack starting"

step "wait for migrate exited 0 (now includes 0007)"
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
# DDL invariants — 0007 applied correctly
# ---------------------------------------------------------------------------

step "psql: 3 new tables exist (schema_entities, schema_fields, schema_relationships)"
out=$(DB_PSQL -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('schema_entities','schema_fields','schema_relationships')")
[[ "$out" == "3" ]] && ok "3 new tables exist" || fail "expected 3 tables; got $out"

step "psql: RLS enabled+forced on all 3 new tables"
out=$(DB_PSQL -tAc "SELECT count(*) FROM pg_class WHERE relname IN ('schema_entities','schema_fields','schema_relationships') AND relrowsecurity AND relforcerowsecurity")
[[ "$out" == "3" ]] && ok "RLS forced on all 3 tables" || fail "expected 3; got $out"

step "psql: kind enum CHECK on schema_relationships"
out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='schema_relationships'::regclass AND conname LIKE '%kind%' LIMIT 1")
if [[ "$out" == *"contains"* && "$out" == *"part_of"* && "$out" == *"attribute_link"* ]]; then
    ok "kind CHECK constraint includes architecture-locked enum"
else
    fail "kind CHECK missing or malformed: $out"
fi

step "psql: field type enum CHECK on schema_fields"
out=$(DB_PSQL -tAc "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='schema_fields'::regclass AND conname LIKE '%type%' LIMIT 1")
if [[ "$out" == *"string"* && "$out" == *"datetime"* ]]; then
    ok "type CHECK constraint enforces core enum"
else
    fail "type CHECK missing: $out"
fi

# ---------------------------------------------------------------------------
# HTTP — full hierarchy build
# ---------------------------------------------------------------------------

step "curl: POST /schemas creates schema (workspace A)"
SCH=$(curl -sS -X POST http://localhost:8000/schemas \
    -H "Content-Type: application/json" -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"verify-1c","description":"hierarchy verify"}')
SCH_ID=$(echo "$SCH" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
[[ -n "$SCH_ID" ]] && ok "schema id=$SCH_ID" || fail "POST didn't return id: $SCH"

step "curl: POST entity File → 201 + bumps current_version"
E1=$(curl -sS -X POST "http://localhost:8000/schemas/$SCH_ID/entities" \
    -H "Content-Type: application/json" -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" -d '{"name":"File","description":"top-level"}')
E1_ID=$(echo "$E1" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
[[ -n "$E1_ID" ]] && ok "entity File id=$E1_ID" || fail "entity POST: $E1"

cv=$(curl -sS "http://localhost:8000/schemas/$SCH_ID" -H "X-Test-Workspace: $WS_A" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['current_version'])")
[[ "$cv" == "2" ]] && ok "schema current_version=2 (entity bumped from 1)" || fail "expected 2 got $cv"

step "curl: POST entity Case + relationship contains"
E2=$(curl -sS -X POST "http://localhost:8000/schemas/$SCH_ID/entities" \
    -H "Content-Type: application/json" -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" -d '{"name":"Case","description":"case in file"}')
E2_ID=$(echo "$E2" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")

REL=$(curl -sS -X POST "http://localhost:8000/schemas/$SCH_ID/relationships" \
    -H "Content-Type: application/json" -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d "{\"name\":\"file_to_case\",\"from_entity_id\":\"$E1_ID\",\"to_entity_id\":\"$E2_ID\",\"kind\":\"contains\",\"cardinality\":\"one_to_many\",\"cascade_delete\":true,\"single_parent\":true}")
REL_ID=$(echo "$REL" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
[[ -n "$REL_ID" ]] && ok "relationship id=$REL_ID" || fail "relationship POST: $REL"

step "curl: POST field title on File with NL description"
F=$(curl -sS -X POST "http://localhost:8000/schemas/$SCH_ID/entities/$E1_ID/fields" \
    -H "Content-Type: application/json" -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"name":"title","type":"string","nl_description":"Document header title","is_required":true}')
F_ID=$(echo "$F" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['id'])")
nl=$(echo "$F" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['nl_description'])")
[[ -n "$F_ID" && "$nl" == "Document header title" ]] && ok "field title id=$F_ID with nl_description" || fail "field POST: $F"

step "curl: snapshot body includes entities[2] with field, relationships[1] using NAMES"
cv=$(curl -sS "http://localhost:8000/schemas/$SCH_ID" -H "X-Test-Workspace: $WS_A" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['current_version'])")
SNAP=$(curl -sS "http://localhost:8000/schemas/$SCH_ID/versions/$cv" -H "X-Test-Workspace: $WS_A")
result=$(echo "$SNAP" | python3 -c "
import sys, json
b = json.loads(sys.stdin.read())['body']
entities = b['entities']
rels = b['relationships']
# 2 entities, sorted by name (Case, File)
if [e['name'] for e in entities] != ['Case', 'File']:
    print('bad entity ordering:', [e['name'] for e in entities]); sys.exit()
# File has 1 field 'title'
file_e = next(e for e in entities if e['name'] == 'File')
if len(file_e['fields']) != 1 or file_e['fields'][0]['name'] != 'title':
    print('bad fields on File:', file_e['fields']); sys.exit()
# 1 relationship using NAMES (not UUIDs)
if len(rels) != 1: print('expected 1 rel:', rels); sys.exit()
r = rels[0]
if r['from'] != 'File' or r['to'] != 'Case':
    print('rel not name-resolved:', r); sys.exit()
if 'from_entity_id' in r:
    print('rel snapshot leaked UUID:', r); sys.exit()
print('ok')
")
[[ "$result" == "ok" ]] && ok "snapshot shape correct (entities sorted by name, relationships use names)" || fail "snapshot mismatch: $result"

# ---------------------------------------------------------------------------
# Cascade-on-entity-delete
# ---------------------------------------------------------------------------

step "curl: DELETE entity File cascades to field + relationship"
http=$(curl -sS -o /dev/null -w "%{http_code}" -X DELETE \
    "http://localhost:8000/schemas/$SCH_ID/entities/$E1_ID" \
    -H "X-Test-Workspace: $WS_A")
[[ "$http" == "204" ]] && ok "DELETE entity returned 204" || fail "expected 204 got $http"

# Verify cascade via superuser psql
fields_active=$(DB_PSQL -tAc "SELECT count(*) FROM schema_fields WHERE entity_id='$E1_ID' AND lifecycle_state='active'")
rels_active=$(DB_PSQL -tAc "SELECT count(*) FROM schema_relationships WHERE (from_entity_id='$E1_ID' OR to_entity_id='$E1_ID') AND lifecycle_state='active'")
if [[ "$fields_active" == "0" && "$rels_active" == "0" ]]; then
    ok "cascade soft-deleted both field and relationship (verified via psql)"
else
    fail "cascade leaked: fields_active=$fields_active rels_active=$rels_active"
fi

# ---------------------------------------------------------------------------
# Rollback restores hierarchy
# ---------------------------------------------------------------------------

step "curl: rollback to v_with_full_hierarchy restores entity + field + relationship"
# Find the version that had everything (before DELETE). After 4 POSTs +
# DELETE that's v6 → v5; rollback to v5.
# Simpler: rollback to the current_version minus 1 (since DELETE just bumped).
cv_now=$(curl -sS "http://localhost:8000/schemas/$SCH_ID" -H "X-Test-Workspace: $WS_A" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['current_version'])")
target_v=$((cv_now - 1))
rb=$(curl -sS -X POST "http://localhost:8000/schemas/$SCH_ID/versions/$target_v/rollback" \
    -H "Content-Type: application/json" -H "X-Test-Workspace: $WS_A" \
    -H "Idempotency-Key: $(uuidgen)" -d '{}')
http=$(echo "$rb" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('current_version','ERR'))" 2>/dev/null || echo "")
[[ "$http" != "" && "$http" != "ERR" ]] && ok "rollback returned new version $http" || fail "rollback failed: $rb"

step "curl: post-rollback, entities/fields/relationships are restored"
e_list=$(curl -sS "http://localhost:8000/schemas/$SCH_ID/entities" -H "X-Test-Workspace: $WS_A")
e_total=$(echo "$e_list" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['total'])")
r_list=$(curl -sS "http://localhost:8000/schemas/$SCH_ID/relationships" -H "X-Test-Workspace: $WS_A")
r_total=$(echo "$r_list" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['total'])")
# Restored "File" entity will have a NEW UUID; need to look it up.
file_id=$(echo "$e_list" | python3 -c "
import sys, json
items = json.loads(sys.stdin.read())['items']
for e in items:
    if e['name'] == 'File':
        print(e['id']); break
")
f_list=$(curl -sS "http://localhost:8000/schemas/$SCH_ID/entities/$file_id/fields" -H "X-Test-Workspace: $WS_A")
f_total=$(echo "$f_list" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['total'])")

if [[ "$e_total" == "2" && "$r_total" == "1" && "$f_total" == "1" ]]; then
    ok "rollback restored 2 entities + 1 field + 1 relationship"
else
    fail "expected 2/1/1; got entities=$e_total fields=$f_total rels=$r_total"
fi

# ---------------------------------------------------------------------------
# RLS isolation
# ---------------------------------------------------------------------------

step "curl: workspace B can't see workspace A's entities/relationships → 404"
http_e=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:8000/schemas/$SCH_ID/entities" -H "X-Test-Workspace: $WS_B")
http_r=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:8000/schemas/$SCH_ID/relationships" -H "X-Test-Workspace: $WS_B")
[[ "$http_e" == "404" && "$http_r" == "404" ]] && ok "RLS isolates B from A's hierarchy (404, not 403)" || fail "RLS leak entities=$http_e relationships=$http_r"

# ---------------------------------------------------------------------------
# openapi exposure
# ---------------------------------------------------------------------------

step "curl: /openapi.json includes all 11 new hierarchy paths"
result=$(curl -sS http://localhost:8000/openapi.json | python3 -c "
import sys, json
ps = json.loads(sys.stdin.read())['paths']
required = [
    ('/schemas/{schema_id}/entities', 'post'),
    ('/schemas/{schema_id}/entities', 'get'),
    ('/schemas/{schema_id}/entities/{entity_id}', 'put'),
    ('/schemas/{schema_id}/entities/{entity_id}', 'delete'),
    ('/schemas/{schema_id}/entities/{entity_id}/fields', 'post'),
    ('/schemas/{schema_id}/entities/{entity_id}/fields', 'get'),
    ('/schemas/{schema_id}/entities/{entity_id}/fields/{field_id}', 'put'),
    ('/schemas/{schema_id}/entities/{entity_id}/fields/{field_id}', 'delete'),
    ('/schemas/{schema_id}/relationships', 'post'),
    ('/schemas/{schema_id}/relationships', 'get'),
    ('/schemas/{schema_id}/relationships/{relationship_id}', 'delete'),
]
missing = [(p, m) for p, m in required if p not in ps or m not in ps[p]]
print('ok' if not missing else 'missing: ' + str(missing))
")
[[ "$result" == "ok" ]] && ok "openapi has all 11 hierarchy endpoints" || fail "$result"

# ----------------------------------------------------------------------------
# Stack 2: pytest (Phase 1c test files only)
# ----------------------------------------------------------------------------

step "pytest — Phase 1c test files over testcontainers"
phase_1c_tests=(
    tests/test_schema_entities.py
    tests/test_schema_fields.py
    tests/test_schema_relationships.py
    tests/test_schema_hierarchy_versions.py
)
if uv run pytest "${phase_1c_tests[@]}" -q >/tmp/kb-verify-1c-pytest.log 2>&1; then
    ok "pytest: $(tail -1 /tmp/kb-verify-1c-pytest.log)"
else
    fail "pytest failed (see /tmp/kb-verify-1c-pytest.log)"
    tail -30 /tmp/kb-verify-1c-pytest.log >&2
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

echo
echo "[verify-1c] === SUMMARY ==="
echo "[verify-1c] checks passed: $CHECKS_PASSED"
echo "[verify-1c] checks failed: $CHECKS_FAILED"
if (( CHECKS_FAILED == 0 )); then
    echo "[verify-1c] Phase 1c G5: GREEN ✅"
else
    echo "[verify-1c] Phase 1c G5: FAILED ❌"
fi
