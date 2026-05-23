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

echo "[bootstrap_db] applying kb migrations..."
python -m migrations.runner

echo "[bootstrap_db] applying procrastinate schema..."
procrastinate --app=kb.workers.app.app schema --apply

echo "[bootstrap_db] done."
