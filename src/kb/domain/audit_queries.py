"""B4b — audit_queries repo (Design 1 layer 10).

One row per Q-mode execution. Append-only (kb_app has SELECT + INSERT
only). Read by:
  - the dashboard's Q-mode list ("recent aggregations")
  - the audit drill-down from a query_log row to its Q execution
  - the CSV download endpoint
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kb.db.pool import Connection


# Mirror of the CHECK enum in migrations/sql/0027_q_mode.sql.
AUDIT_QUERY_STATUSES: tuple[str, ...] = (
    "ok", "refused", "timeout", "row_cap_exceeded", "error",
)


@dataclass(frozen=True)
class AuditQueryRecord:
    id: str
    workspace_id: str
    query_log_id: str | None
    plan: dict
    compiled_sql: str
    params: list
    row_count: int
    runtime_ms: int
    status: str
    refusal_reason: str | None
    csv_artifact_key: str | None
    created_at: str


_COLS = (
    "id, workspace_id, query_log_id, plan, compiled_sql, params, "
    "row_count, runtime_ms, status, refusal_reason, csv_artifact_key, "
    "created_at"
)


def _row(row: tuple) -> AuditQueryRecord:
    return AuditQueryRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        query_log_id=str(row[2]) if row[2] else None,
        plan=row[3] if isinstance(row[3], dict) else (
            json.loads(row[3]) if row[3] else {}
        ),
        compiled_sql=str(row[4]),
        params=list(row[5]) if isinstance(row[5], list) else (
            json.loads(row[5]) if row[5] else []
        ),
        row_count=int(row[6] or 0),
        runtime_ms=int(row[7] or 0),
        status=str(row[8]),
        refusal_reason=row[9],
        csv_artifact_key=row[10],
        created_at=(
            row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11])
        ),
    )


async def insert_audit_query(
    conn: Connection,
    *,
    workspace_id: str,
    query_log_id: str | None,
    plan: dict,
    compiled_sql: str,
    params: list,
    row_count: int,
    runtime_ms: int,
    status: str,
    refusal_reason: str | None = None,
    csv_artifact_key: str | None = None,
    audit_query_id: str | None = None,
) -> str:
    """Append-only insert. Returns the audit_queries.id (UUID string).

    `audit_query_id` is optional — caller can pre-compute the UUID so the
    CSV artifact can be uploaded under that key BEFORE the row exists.
    This avoids the need for UPDATE permission on audit_queries (the
    table is GRANT-locked to SELECT + INSERT for append-only audit
    semantics)."""
    if status not in AUDIT_QUERY_STATUSES:
        raise ValueError(
            f"status must be one of {AUDIT_QUERY_STATUSES}, got {status!r}"
        )
    if audit_query_id is None:
        cur = await conn.execute(
            """
            INSERT INTO audit_queries (
                workspace_id, query_log_id, plan, compiled_sql, params,
                row_count, runtime_ms, status, refusal_reason, csv_artifact_key
            ) VALUES (
                %s, %s, %s::jsonb, %s, %s::jsonb,
                %s, %s, %s, %s, %s
            )
            RETURNING id::text
            """,
            (
                workspace_id, query_log_id,
                json.dumps(plan or {}),
                compiled_sql,
                json.dumps(list(params or [])),
                row_count, runtime_ms, status,
                refusal_reason, csv_artifact_key,
            ),
        )
    else:
        cur = await conn.execute(
            """
            INSERT INTO audit_queries (
                id, workspace_id, query_log_id, plan, compiled_sql, params,
                row_count, runtime_ms, status, refusal_reason, csv_artifact_key
            ) VALUES (
                %s, %s, %s, %s::jsonb, %s, %s::jsonb,
                %s, %s, %s, %s, %s
            )
            RETURNING id::text
            """,
            (
                audit_query_id, workspace_id, query_log_id,
                json.dumps(plan or {}),
                compiled_sql,
                json.dumps(list(params or [])),
                row_count, runtime_ms, status,
                refusal_reason, csv_artifact_key,
            ),
        )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def read_audit_query_by_id(
    conn: Connection, *, audit_query_id: str,
) -> AuditQueryRecord | None:
    cur = await conn.execute(
        f"SELECT {_COLS} FROM audit_queries WHERE id = %s",
        (audit_query_id,),
    )
    row = await cur.fetchone()
    return _row(row) if row else None


async def read_audit_queries_for_workspace(
    conn: Connection, *, workspace_id: str, limit: int = 50,
) -> list[AuditQueryRecord]:
    cur = await conn.execute(
        f"SELECT {_COLS} FROM audit_queries WHERE workspace_id = %s "
        f"ORDER BY created_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    return [_row(r) for r in await cur.fetchall()]
