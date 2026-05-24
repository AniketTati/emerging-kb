#!/usr/bin/env bash
# Run all 12 verify_phase_*.sh scripts against a single shared docker-compose
# stack — eliminates the 11 redundant build/up/migrate/down cycles that make
# the sequential sweep take ~20 min.
#
# Each phase script supports KB_REUSE_STACK=1, which skips its own stack
# setup + teardown. This wrapper:
#   1. Brings the compose stack up ONCE.
#   2. Waits for migrate exit + api healthy ONCE.
#   3. For each verify_phase_*.sh: TRUNCATE workspace-scoped tables to give
#      that phase a clean DB, then run with KB_REUSE_STACK=1.
#   4. Tears down the stack ONCE (unless KB_VERIFY_KEEP_STACK=1).
#
# Standalone phase scripts still work — they default to managing their own
# stack when KB_REUSE_STACK is unset.
#
# Usage:
#   scripts/verify_sweep.sh                       # full sweep, default order
#   scripts/verify_sweep.sh 0 1a 1b               # subset
#   KB_VERIFY_KEEP_STACK=1 scripts/verify_sweep.sh  # leave stack up at end

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "[sweep] .env not found; copying from .env.example"
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

# Selected phases — default to all 12 in order; CLI args can subset.
ALL_PHASES=(0 1a 1b 1c 2a 2b 2c 3a 3b 3c 3d 3e)
if (( $# > 0 )); then
    PHASES=("$@")
else
    PHASES=("${ALL_PHASES[@]}")
fi

PASSED=()
FAILED=()
TIMINGS=()

cleanup() {
    local rc=$?
    if [[ "${KB_VERIFY_KEEP_STACK:-0}" != "1" ]]; then
        echo
        echo "[sweep] tearing down compose stack..."
        $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    echo
    echo "[sweep] === SWEEP SUMMARY ==="
    for line in "${TIMINGS[@]}"; do echo "[sweep] $line"; done
    echo "[sweep] passed: ${#PASSED[@]} / ${#PHASES[@]}"
    if (( ${#FAILED[@]} > 0 )); then
        echo "[sweep] failed phases: ${FAILED[*]}"
        exit 1
    fi
    if (( rc != 0 )); then exit $rc; fi
    echo "[sweep] ALL GREEN ✅"
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# One-time stack setup
# ----------------------------------------------------------------------------

t0=$(date +%s)
echo "[sweep] === bringing stack up (once) ==="
$COMPOSE build >/tmp/kb-sweep-build.log 2>&1
echo "[sweep] compose build done ($(($(date +%s) - t0))s)"

$COMPOSE up -d >/tmp/kb-sweep-up.log 2>&1
echo "[sweep] compose up -d done"

echo "[sweep] waiting for migrate exit 0..."
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
(( migrate_ok == 1 )) || { echo "[sweep] migrate did not exit cleanly within 120s"; exit 1; }
echo "[sweep] migrate exited 0"

echo "[sweep] waiting for db/minio/api healthy..."
for svc in db minio api; do
    for _ in $(seq 1 30); do
        h=$($COMPOSE ps --format json $svc 2>/dev/null | python3 -c "import sys,json
try:
    d=json.loads(sys.stdin.read() or '{}')
    print(d.get('Health',''))
except Exception: print('')" 2>/dev/null || echo "")
        if [[ "$h" == "healthy" ]]; then break; fi
        sleep 2
    done
    if [[ "$h" == "healthy" ]]; then
        echo "[sweep]   ✓ $svc healthy"
    else
        echo "[sweep]   ✗ $svc not healthy (state: $h)"
        exit 1
    fi
done
echo "[sweep] stack ready in $(($(date +%s) - t0))s"

# ----------------------------------------------------------------------------
# Per-phase: reset state, then run with KB_REUSE_STACK=1
# ----------------------------------------------------------------------------

# Workspace-scoped tables that accumulate per-script state. TRUNCATE CASCADE
# clears all FK chains in one shot. audit_log is partitioned but TRUNCATE
# works on parent partitions in PG13+.
RESET_SQL=$(cat <<'EOF'
TRUNCATE
    raptor_edges,
    raptor_nodes,
    chunk_embeddings,
    contextual_chunks,
    chunks,
    parse_artifacts,
    raw_pages,
    file_lifecycle,
    files,
    schema_relationships,
    schema_fields,
    schema_entities,
    schema_versions,
    schemas,
    audit_log,
    idempotency_keys
RESTART IDENTITY CASCADE;
TRUNCATE procrastinate_jobs, procrastinate_events RESTART IDENTITY CASCADE;
EOF
)

for phase in "${PHASES[@]}"; do
    script="scripts/verify_phase_${phase}.sh"
    if [[ ! -x "$script" ]]; then
        echo "[sweep] ✗ $script not found or not executable; skipping"
        FAILED+=("$phase")
        continue
    fi

    echo
    echo "[sweep] === phase $phase: resetting data + running ==="
    t_phase=$(date +%s)

    # Reset data state so each phase starts clean (workspace UUIDs are
    # hardcoded; without this, e.g. phase 1b would see phase 1a's schemas).
    DB_PSQL -q -c "$RESET_SQL" >/dev/null 2>&1 \
        || { echo "[sweep] ✗ reset failed before phase $phase"; FAILED+=("$phase"); continue; }

    # Run with KB_REUSE_STACK=1 so the script skips its own stack lifecycle.
    if KB_REUSE_STACK=1 "$script"; then
        dur=$(($(date +%s) - t_phase))
        PASSED+=("$phase")
        TIMINGS+=("phase $phase: ✓ ${dur}s")
        echo "[sweep] === phase $phase: GREEN (${dur}s) ==="
    else
        dur=$(($(date +%s) - t_phase))
        FAILED+=("$phase")
        TIMINGS+=("phase $phase: ✗ ${dur}s")
        echo "[sweep] === phase $phase: FAILED (${dur}s) ==="
        # Continue with remaining phases — partial results are useful.
    fi
done
