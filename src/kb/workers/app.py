"""Procrastinate App — Postgres-backed job queue.

Phase 0 ships the App scaffold; Phase 2a registers the `parse_file` task.
The worker container in docker-compose runs
`procrastinate --app=kb.workers.app.app worker`.

The connector reads `KB_DATABASE_URL` LAZILY at App.open_async() time,
not at module import. This lets tests (which set KB_DATABASE_URL via
fixture) build the connector correctly.
"""

from __future__ import annotations

import os

from procrastinate import App, PsycopgConnector


class _LazyConninfoConnector(PsycopgConnector):
    """PsycopgConnector that reads KB_DATABASE_URL at open() time, not at
    construction. Lets tests set the env var via fixture after the module
    has already been imported.
    """

    async def open_async(self, pool=None):
        # If conninfo was empty at construction, re-read env now.
        # Procrastinate stores the kwargs in self._pool_args; mutate it.
        if not self._pool_args.get("conninfo"):
            env_conninfo = os.environ.get("KB_DATABASE_URL", "")
            if env_conninfo:
                self._pool_args["conninfo"] = env_conninfo
        return await super().open_async(pool=pool)


_conninfo = os.environ.get("KB_DATABASE_URL", "")
connector = _LazyConninfoConnector(conninfo=_conninfo) if _conninfo else _LazyConninfoConnector()
app = App(connector=connector)


def _register_tasks() -> None:
    """Eagerly import task modules so `@app.task` decorators register them
    before the Procrastinate worker process discovers tasks.

    Called from module bottom. Avoids the circular import that would occur
    if `kb.workers.tasks` imported `kb.workers.app` at module top while
    this module was still executing.
    """
    from kb.workers import tasks as _tasks  # noqa: F401


_register_tasks()
