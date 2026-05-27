#!/usr/bin/env bash
# Launch the procrastinate worker natively for live-edit dev.
#
#   ./scripts/dev_worker.sh                 # uses default concurrency=5
#   KB_WORKER_CONCURRENCY=1 ./scripts/dev_worker.sh
#
# Requires `source scripts/dev_env.sh` to have been run in the same shell.
# Worker doesn't have a built-in reload; restart it (Ctrl-C + rerun) after
# Python edits to the worker tasks.
#
# Default concurrency=5. Tier-1 Gemini quotas (1000 RPM Flash, 3000 RPM
# embedding) have plenty of headroom even when 5 docs hit the pipeline
# simultaneously. The two prior concurrency-race bugs (H ensure_sub_entity
# UNIQUE, G graph_edges FK) are both fixed (H by ON CONFLICT DO NOTHING +
# re-SELECT, G by skipping lineage edges + per-edge savepoint). Lower
# via KB_WORKER_CONCURRENCY=N if you need to debug a sequential pipeline.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ -z "${KB_POSTGRES_HOST:-}" ]]; then
    # shellcheck disable=SC1091
    source ./scripts/dev_env.sh
fi

CONC="${KB_WORKER_CONCURRENCY:-5}"
exec uv run procrastinate --app=kb.workers.app.app worker --concurrency "$CONC"
