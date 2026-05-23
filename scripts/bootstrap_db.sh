#!/usr/bin/env bash
# Phase 0 DB bootstrap.
#
# Runs inside the `migrate` compose service (one-shot). Exits 0 on success
# so `api` + `worker` (which depend on service_completed_successfully) can start.
#
# Steps:
#   1. Our SQL migrations via `python -m migrations.runner`. This also sets
#      kb_app's password from KB_APP_PASSWORD.
#   2. Procrastinate's own schema via `procrastinate schema --apply`. Idempotent.

set -euo pipefail

# pg_isready healthcheck can pass a moment before postgres is actually
# accepting TCP connections. Loop until we can connect (max ~30s).
echo "[bootstrap_db] waiting for postgres to accept connections..."
python -c "
import os, sys, time
import psycopg
url = os.environ['KB_DATABASE_URL']
for i in range(30):
    try:
        # connect_timeout=2 prevents hanging when the server is reachable
        # but not yet listening (e.g. mid-initdb).
        with psycopg.connect(url, autocommit=True, connect_timeout=2) as conn:
            conn.execute('SELECT 1')
        print(f'  postgres ready after {i}s')
        sys.exit(0)
    except psycopg.OperationalError as exc:
        print(f'  attempt {i+1}/30: {exc}', flush=True)
        time.sleep(1)
print('postgres did not become ready in 30s', file=sys.stderr)
sys.exit(1)
"

echo "[bootstrap_db] applying kb migrations..."
python -m migrations.runner

echo "[bootstrap_db] applying procrastinate schema..."
procrastinate --app=kb.workers.app.app schema --apply

echo "[bootstrap_db] done."
