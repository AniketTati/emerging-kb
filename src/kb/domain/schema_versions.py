"""Schema-versions domain layer — snapshot models, diff helper, repo functions.

Phase 1b. api_contracts §3.5–§3.9; build_tracker §5.3 decisions #1–#13.

Versions are immutable: SELECT + INSERT only on `schema_versions` (enforced
by GRANTs in 0006). Body is a full JSON snapshot (decision #1); diff is
computed at read time (decision #7); rollback is clone-forward (decision #5).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class VersionSummary(BaseModel):
    """List-item shape (§3.7). Lightweight — no body, no diff."""

    version: int
    kind: str
    parent_version: int | None
    created_at: str


class VersionRead(BaseModel):
    """Single-version read shape (§3.5 + §3.8). Includes body + computed diff."""

    version: int
    kind: str
    body: dict[str, Any]
    parent_version: int | None
    diff_from_prior: dict[str, Any] | None
    created_at: str


class VersionListResponse(BaseModel):
    items: list[VersionSummary]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Domain exceptions — translated to HTTP errors in the API layer
# ---------------------------------------------------------------------------


class VersionNotFoundError(Exception):
    """No `schema_versions` row with that (schema_id, version_number)."""


class RollbackNoopError(Exception):
    """Rollback target equals current_version — decision #13, slug `rollback-noop`."""


# ---------------------------------------------------------------------------
# Diff (§3.6) — declarative, not strict RFC 6902
# ---------------------------------------------------------------------------


def compute_diff(prior: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any] | None:
    """Compare two snapshot bodies. Returns {added, removed, changed} or None
    if `prior` is None (v1).

    At Phase 1b: only top-level scalar keys (`name`, `description`).
    At Phase 1c: recurses into `entities` and `relationships` arrays, keyed
    by `name` (unique within parent scope). Paths become nested-dotted:
        `entities.File`               — entity added/removed (whole subtree)
        `entities.File.fields.title`  — field added/removed
        `entities.File.fields.title.type` — scalar inside field changed
        `relationships.file_to_case`  — relationship added/removed
    """
    if prior is None:
        return None

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    _diff_dict(prior, current, "", added, removed, changed)

    return {"added": added, "removed": removed, "changed": changed}


def _diff_dict(
    prior: dict[str, Any],
    current: dict[str, Any],
    prefix: str,
    added: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    changed: list[dict[str, Any]],
) -> None:
    """Recursively diff two dicts.

    For known list-of-dict keys (`entities`, `fields`, `relationships`),
    treat them as name-keyed maps so paths become `entities.<name>.fields.<name>`
    rather than `entities.0.fields.2` (matches §4.3 spec exactly).
    """
    prior_keys = set(prior.keys())
    current_keys = set(current.keys())

    for k in current_keys - prior_keys:
        added.append({"path": _join(prefix, k), "value": current[k]})
    for k in prior_keys - current_keys:
        removed.append({"path": _join(prefix, k), "value": prior[k]})

    for k in prior_keys & current_keys:
        p_val, c_val = prior[k], current[k]
        if p_val == c_val:
            continue

        # List-of-dicts with `name` keys → diff as a name-keyed map.
        if k in ("entities", "fields", "relationships") and \
           isinstance(p_val, list) and isinstance(c_val, list):
            _diff_name_keyed_list(p_val, c_val, _join(prefix, k),
                                  added, removed, changed)
            continue

        # Plain nested dict → recurse.
        if isinstance(p_val, dict) and isinstance(c_val, dict):
            _diff_dict(p_val, c_val, _join(prefix, k),
                       added, removed, changed)
            continue

        # Scalar change.
        changed.append({"path": _join(prefix, k), "old": p_val, "new": c_val})


def _diff_name_keyed_list(
    prior_list: list[dict[str, Any]],
    current_list: list[dict[str, Any]],
    prefix: str,
    added: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    changed: list[dict[str, Any]],
) -> None:
    """Diff two lists of dicts where each item has a 'name' field — treat as
    name-keyed map so the diff path is human-friendly (`entities.File`, not
    `entities.0`)."""
    prior_by_name = {item["name"]: item for item in prior_list if "name" in item}
    current_by_name = {item["name"]: item for item in current_list if "name" in item}

    for name in current_by_name.keys() - prior_by_name.keys():
        added.append({"path": _join(prefix, name), "value": current_by_name[name]})
    for name in prior_by_name.keys() - current_by_name.keys():
        removed.append({"path": _join(prefix, name), "value": prior_by_name[name]})

    for name in prior_by_name.keys() & current_by_name.keys():
        if prior_by_name[name] != current_by_name[name]:
            _diff_dict(prior_by_name[name], current_by_name[name],
                       _join(prefix, name), added, removed, changed)


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    """ISO-8601 UTC with Z suffix (api_contracts §0.2). Matches kb.domain.schemas."""
    return ts.astimezone().isoformat().replace("+00:00", "Z")


def _ensure_dict(body: Any) -> dict[str, Any]:
    """psycopg returns jsonb as dict in most cases; safety net for str fallback."""
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        return json.loads(body)
    raise TypeError(f"unexpected body type: {type(body).__name__}")


# ---------------------------------------------------------------------------
# Repo functions — talk to PG via the per-request kb_app connection
# ---------------------------------------------------------------------------


async def list_versions(
    conn: Connection, schema_id: str, limit: int, offset: int
) -> VersionListResponse:
    """List versions for a schema, newest-first. Lightweight summary form (§3.7).

    Caller is responsible for confirming the parent schema is active + visible
    (404-on-missing is the API-layer's job; this repo just returns the rows
    RLS allows it to see).
    """
    cur = await conn.execute(
        "SELECT version_number, kind, parent_version_number, created_at "
        "FROM schema_versions "
        "WHERE schema_id = %s "
        "ORDER BY version_number DESC "
        "LIMIT %s OFFSET %s",
        (schema_id, limit, offset),
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM schema_versions WHERE schema_id = %s",
        (schema_id,),
    )
    total_row = await cur.fetchone()

    items = [
        VersionSummary(
            version=r[0],
            kind=r[1],
            parent_version=r[2],
            created_at=_iso(r[3]),
        )
        for r in rows
    ]
    return VersionListResponse(
        items=items, total=total_row[0], limit=limit, offset=offset
    )


async def get_version(
    conn: Connection, schema_id: str, version_number: int
) -> VersionRead:
    """Read one version + compute diff against the prior version (§3.8).

    Raises `VersionNotFoundError` if no such version exists for this schema.
    """
    cur = await conn.execute(
        "SELECT version_number, kind, body, parent_version_number, created_at "
        "FROM schema_versions "
        "WHERE schema_id = %s AND version_number = %s",
        (schema_id, version_number),
    )
    row = await cur.fetchone()
    if row is None:
        raise VersionNotFoundError(f"schema={schema_id} version={version_number}")
    version, kind, body, parent_v, created_at = row
    body_dict = _ensure_dict(body)

    prior_body: dict[str, Any] | None = None
    if parent_v is not None:
        cur = await conn.execute(
            "SELECT body FROM schema_versions WHERE schema_id = %s AND version_number = %s",
            (schema_id, parent_v),
        )
        prior_row = await cur.fetchone()
        if prior_row is not None:
            prior_body = _ensure_dict(prior_row[0])

    return VersionRead(
        version=version,
        kind=kind,
        body=body_dict,
        parent_version=parent_v,
        diff_from_prior=compute_diff(prior_body, body_dict),
        created_at=_iso(created_at),
    )


async def insert_version(
    conn: Connection,
    *,
    schema_id: str,
    workspace_id: str,
    version_number: int,
    body: dict[str, Any],
    parent_version_number: int | None,
    kind: str,
) -> str:
    """INSERT one schema_versions row. Returns the new row's UUID id.

    Used by `kb.domain.schemas` for POST (kind='post', v=1, parent=None),
    PUT (kind='put', v=prior+1, parent=prior), and rollback (kind='rollback').
    """
    cur = await conn.execute(
        "INSERT INTO schema_versions "
        "(schema_id, workspace_id, version_number, body, parent_version_number, kind) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s) "
        "RETURNING id",
        (
            schema_id,
            workspace_id,
            version_number,
            json.dumps(body),
            parent_version_number,
            kind,
        ),
    )
    row = await cur.fetchone()
    return str(row[0])
