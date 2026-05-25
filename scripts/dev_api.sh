#!/usr/bin/env bash
# Launch the API natively for live-edit dev. Auto-reloads on file changes.
#
#   ./scripts/dev_api.sh
#
# Requires `source scripts/dev_env.sh` to have been run in the same shell.
# Assumes Docker stack is up for db + minio (`docker compose up -d`).

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Source env if the caller hasn't already (idempotent).
if [[ -z "${KB_POSTGRES_HOST:-}" ]]; then
    # shellcheck disable=SC1091
    source ./scripts/dev_env.sh
fi

# Pre-flight: db reachable?
python -c "
import os, psycopg
url = os.environ['KB_DATABASE_URL']
try:
    with psycopg.connect(url, connect_timeout=2) as conn:
        conn.execute('SELECT 1')
    print(f'[dev_api] db ready at {url.split(\"@\")[1]}')
except Exception as e:
    raise SystemExit(f'[dev_api] db not reachable ({e}); run: docker compose up -d db minio')
"

# --reload watches src/. Each request rebuilds against fresh source.
exec uv run uvicorn kb.api.main:app \
    --host 0.0.0.0 --port 8000 \
    --reload \
    --reload-dir src
