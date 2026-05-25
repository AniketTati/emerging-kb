#!/usr/bin/env bash
# Launch the procrastinate worker natively for live-edit dev.
#
#   ./scripts/dev_worker.sh
#
# Requires `source scripts/dev_env.sh` to have been run in the same shell.
# Worker doesn't have a built-in reload; restart it (Ctrl-C + rerun) after
# Python edits to the worker tasks.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ -z "${KB_POSTGRES_HOST:-}" ]]; then
    # shellcheck disable=SC1091
    source ./scripts/dev_env.sh
fi

exec uv run procrastinate --app=kb.workers.app.app worker
