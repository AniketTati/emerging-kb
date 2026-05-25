#!/usr/bin/env bash
# B9 — Wave A eval harness orchestrator.
#
# Assumes the API is reachable at $KB_API_BASE_URL (default
# http://localhost:8000) and the worker queue is draining files as they
# land. Workflow:
#
#   1. Optionally ingest a directory tree into the target workspace.
#   2. Wait for the worker pipeline to reach 'ready' on every file.
#   3. Run the golden 45-question set against POST /chat.
#   4. Write per-question results CSV + per-stratum summary JSON.
#
# Usage:
#   scripts/run_eval.sh <workspace_id> [<corpus_dir>]
#
# Env:
#   KB_API_BASE_URL  Base URL of the running API (default http://localhost:8000)
#   KB_EVAL_OUT_DIR  Output directory for CSV + JSON (default ./eval_out)
#   KB_EVAL_TIMEOUT  Max seconds to wait for files to reach 'ready' (default 600)

set -euo pipefail

WORKSPACE="${1:?usage: run_eval.sh <workspace_id> [<corpus_dir>]}"
CORPUS_DIR="${2:-}"
BASE_URL="${KB_API_BASE_URL:-http://localhost:8000}"
OUT_DIR="${KB_EVAL_OUT_DIR:-./eval_out}"
mkdir -p "${OUT_DIR}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
CSV_PATH="${OUT_DIR}/eval_${STAMP}.csv"
JSON_PATH="${OUT_DIR}/eval_${STAMP}.summary.json"

if [[ -n "${CORPUS_DIR}" ]]; then
    echo "==> Ingesting ${CORPUS_DIR} into workspace=${WORKSPACE}"
    python -m kb.eval ingest \
        --base-url "${BASE_URL}" \
        --workspace "${WORKSPACE}" \
        --dir "${CORPUS_DIR}"
    echo "==> Waiting up to ${KB_EVAL_TIMEOUT:-600}s for files to reach 'ready'..."
    # The /dashboard/summary endpoint exposes lifecycle counts; we poll
    # for files_by_lifecycle to be all 'ready'.
    deadline=$(( $(date +%s) + ${KB_EVAL_TIMEOUT:-600} ))
    while (( $(date +%s) < deadline )); do
        body=$(curl -fsS -H "X-Test-Workspace: ${WORKSPACE}" \
                    "${BASE_URL}/dashboard/summary" || true)
        # Crude check: count lines mentioning a non-ready state.
        non_ready=$(echo "${body}" | grep -oE '"label":[^,]+,"count":[0-9]+' | \
                    grep -v '"label":"ready"' | grep -oE '"count":[0-9]+' | \
                    awk -F: '{s+=$2} END {print s+0}')
        echo "  non-ready files: ${non_ready}"
        if [[ "${non_ready}" == "0" ]]; then
            echo "  all files ready."
            break
        fi
        sleep 5
    done
fi

echo "==> Running 45-question eval against ${BASE_URL}, workspace=${WORKSPACE}"
python -m kb.eval run \
    --base-url "${BASE_URL}" \
    --workspace "${WORKSPACE}" \
    --out "${CSV_PATH}" \
    --summary-json "${JSON_PATH}"

echo ""
echo "==> Done. Results: ${CSV_PATH}"
echo "    Summary:    ${JSON_PATH}"
