"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from kb.config import get_settings
from kb.db.pool import Connection, open_connection
from kb.logging import workspace_id_var


def current_workspace_id() -> str:
    """Resolve the workspace_id for the current request from the contextvar
    populated by `WorkspaceMiddleware`.
    """
    ws_id = workspace_id_var.get()
    if ws_id is None:  # middleware not mounted — programmer error
        raise RuntimeError("workspace_id contextvar unset; mount WorkspaceMiddleware")
    return ws_id


async def kb_app_connection() -> AsyncIterator[Connection]:
    """Per-request kb_app DB connection with `app.workspace_id` set.

    Each request opens its own connection (psycopg3 doesn't pool natively for
    async; `psycopg_pool.AsyncConnectionPool` is a Phase 2+ optimization).
    The `SET set_config('app.workspace_id', ..., true)` is LOCAL — scoped
    to the surrounding transaction. The `transaction()` block commits on
    successful exit, rolls back on exception.
    """
    settings = get_settings()
    workspace_id = current_workspace_id()
    async with open_connection(settings.app_database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            yield conn
