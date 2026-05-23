"""Idempotency-Key dependency + cache helpers.

api_contracts §0.5 + §2.2-§2.6. Replay semantics:
- A request with a previously-seen `(workspace_id, Idempotency-Key)` returns
  the cached body + status_code verbatim; handler does NOT re-execute.
- Cache scope: 2xx responses only. Domain errors (409, 422, 404) are
  deterministic in Phase 1a — a retry will reproduce them naturally.

Race-on-concurrent-identical-keys is accepted in Phase 1a: two parallel
requests can both pass the cache miss and both execute. The DB-side unique
constraint (e.g. schemas_workspace_name_active_idx) catches the doubled
write for POST. The second-arriving INSERT into `idempotency_keys` is
no-op'd via ON CONFLICT DO NOTHING. Phase 9 may add a proper lock.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Header

from kb.api.errors import MissingIdempotencyKeyError
from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Header dependencies
# ---------------------------------------------------------------------------


async def idempotency_key_required(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """POST dependency — header must be present."""
    if not idempotency_key:
        raise MissingIdempotencyKeyError()
    return idempotency_key


async def idempotency_key_optional(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    """PUT/DELETE dependency — header optional."""
    return idempotency_key or None


# ---------------------------------------------------------------------------
# Cache lookup + write
# ---------------------------------------------------------------------------


async def get_cached(
    conn: Connection, workspace_id: str, key: str
) -> tuple[dict[str, Any] | None, int] | None:
    """Return `(body_or_None, status_code)` for a cached entry, or None on miss.

    body_or_None is None for 204 (DELETE) entries; non-None for JSON bodies.
    """
    cur = await conn.execute(
        "SELECT response, status_code FROM idempotency_keys "
        "WHERE workspace_id = %s AND key = %s",
        (workspace_id, key),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    raw_body, status_code = row[0], row[1]
    # status_code 204 → stored as {} sentinel; surface as None to caller.
    body = None if status_code == 204 else raw_body
    return body, status_code


async def cache_response(
    conn: Connection,
    workspace_id: str,
    key: str | None,
    *,
    body: dict[str, Any] | None,
    status_code: int,
) -> None:
    """Store the response under (workspace_id, key). No-op if key is None or status >= 300.

    ON CONFLICT DO NOTHING: if a parallel identical request beat us to the row,
    leave their entry alone (it's the source of truth for replays).
    """
    if key is None:
        return
    if status_code >= 300:
        return  # Phase 1a only caches successful responses.

    cached_body = body if body is not None else {}
    await conn.execute(
        "INSERT INTO idempotency_keys (workspace_id, key, response, status_code) "
        "VALUES (%s, %s, %s::jsonb, %s) "
        "ON CONFLICT (workspace_id, key) DO NOTHING",
        (workspace_id, key, json.dumps(cached_body), status_code),
    )
