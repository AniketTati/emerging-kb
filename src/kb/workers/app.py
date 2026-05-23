"""Procrastinate App — Postgres-backed job queue.

Phase 0 ships the App scaffold; no tasks are registered yet. Tasks land
at Phases 2–7 as worker pipelines come online. The worker container in
docker-compose runs `procrastinate --app=kb.workers.app.app worker`.
"""

from __future__ import annotations

import os

from procrastinate import App, PsycopgConnector

# Procrastinate uses the superuser DB URL (it manages its own tables).
_conninfo = os.environ.get("KB_DATABASE_URL", "")

connector = PsycopgConnector(conninfo=_conninfo) if _conninfo else PsycopgConnector()
app = App(connector=connector)
