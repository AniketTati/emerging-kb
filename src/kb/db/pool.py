"""Async Postgres connections for the API and worker.

`open_connection(url)` returns a context-managed wrapper around psycopg3's
async connection. The wrapper adds `fetch()` and `fetchrow()` shorthand
methods on top of psycopg3's cursor API — tests use these. All other
psycopg3 attributes (transaction, execute, etc.) pass through.

The pool itself (psycopg_pool.AsyncConnectionPool) is set up by the
FastAPI app's lifespan handler in `kb.api.main`. For one-off uses
(migrations, tests, scripts), `open_connection` is the entry point.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Sequence
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import tuple_row


class Connection:
    """Thin wrapper over psycopg3 AsyncConnection.

    Adds `fetch()` and `fetchrow()` for parity with the asyncpg style used
    in tests. Other attribute access (execute, transaction, commit, ...)
    passes through to the wrapped connection.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        return await self._conn.execute(sql, params)

    async def fetch(self, sql: str, *args: Any) -> list[tuple]:
        params = list(args) if args else None
        cur = await self._conn.execute(sql, params)
        return await cur.fetchall()

    async def fetchrow(self, sql: str, *args: Any) -> tuple | None:
        params = list(args) if args else None
        cur = await self._conn.execute(sql, params)
        return await cur.fetchone()

    def transaction(self, **kwargs: Any) -> Any:
        return self._conn.transaction(**kwargs)

    async def close(self) -> None:
        await self._conn.close()

    @property
    def raw(self) -> AsyncConnection:
        """Escape hatch for psycopg-native usage (e.g. cursor row_factory tweaks)."""
        return self._conn

    def __getattr__(self, item: str) -> Any:
        # Pass-through for anything we didn't wrap (e.g. .info, .pgconn, etc.).
        return getattr(self._conn, item)


@contextlib.asynccontextmanager
async def open_connection(url: str) -> AsyncIterator[Connection]:
    """Open a single psycopg3 async connection wrapped in our `Connection`.

    The underlying connection uses `tuple_row` for predictable test assertions.
    """
    raw: AsyncConnection = await psycopg.AsyncConnection.connect(
        url, row_factory=tuple_row
    )
    try:
        yield Connection(raw)
    finally:
        await raw.close()
