"""B6b / WA-13 — User feedback (Design 4) repo + routing.

Four tables: corrections, entity_overrides, schema_field_overrides,
regression_set. This module owns:

  - CRUD helpers for each
  - `route_correction()` — applies the scope-conditional side-effects
    sketched in Design 4 §"Feedback flow":
      scope='entity_merge'  → INSERT entity_overrides(rule_type='never_merge')
      scope='entity_split'  → INSERT entity_overrides(rule_type='split')
      scope='schema_field'  → INSERT schema_field_overrides
      scope='extraction'    → mark correction status='fixing' so the
                              worker (a follow-up commit) can pick it up
      scope='answer'        → status='triaged' (re-run augmented later)
      scope='doc_chain'     → status='fixing' (chain re-detection later)
      scope='source_authority' → calls B2's set_source_authority_override
      others / scope='other' → status='triaged' (manual review)
  - `build_regression_entry()` — synthesizes a regression_set row from
    the correction so the eval harness picks up the case.

Worker-side targeted re-extraction (scope='extraction'), entity-cluster
re-resolution (scope='entity_*'), and chain re-detection (scope='doc_chain')
are stubbed as `status='fixing'` for Wave A; the resolution payload
records what would have run, and the actual re-extraction is wired in a
follow-up after the worker pipeline is ready to accept correction-triggered
re-runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kb.db.pool import Connection


CORRECTION_SCOPES: tuple[str, ...] = (
    "answer", "citation", "extraction",
    "entity_merge", "entity_split",
    "schema_field", "doc_chain",
    "source_authority", "other",
)
CORRECTION_SEVERITIES: tuple[str, ...] = (
    "blocker", "important", "minor", "enhancement",
)
CORRECTION_STATUSES: tuple[str, ...] = (
    "open", "triaged", "fixing", "verified", "closed", "rejected",
)
ENTITY_OVERRIDE_RULES: tuple[str, ...] = (
    "never_merge", "always_merge", "rename", "split",
)
SCHEMA_FIELD_OVERRIDE_KINDS: tuple[str, ...] = (
    "undo_promotion", "retype", "rename", "blacklist",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrectionRecord:
    id: str
    workspace_id: str
    user_id: str | None
    scope: str
    target: dict
    observed_value: str | None
    correct_value: str | None
    reason: str | None
    severity: str
    status: str
    resolution: dict | None
    audit_query_id: str | None
    created_at: str
    resolved_at: str | None


@dataclass(frozen=True)
class EntityOverrideRecord:
    id: str
    workspace_id: str
    rule_type: str
    entity_a: str | None
    entity_b: str | None
    rename_to: str | None
    reason: str | None
    active: bool
    correction_id: str | None
    created_at: str


@dataclass(frozen=True)
class SchemaFieldOverrideRecord:
    id: str
    workspace_id: str
    field_path: str
    override_kind: str
    details: dict
    reason: str | None
    active: bool
    correction_id: str | None
    created_at: str


@dataclass(frozen=True)
class RegressionEntryRecord:
    id: str
    workspace_id: str
    source_correction_id: str | None
    query_text: str
    expected_facts: dict
    implicated_docs: tuple[str, ...]
    severity: str
    active: bool
    fail_count: int
    created_at: str


# ---------------------------------------------------------------------------
# corrections CRUD
# ---------------------------------------------------------------------------


_C_COLS = (
    "id::text, workspace_id::text, user_id::text, scope, target, "
    "observed_value, correct_value, reason, severity, status, resolution, "
    "audit_query_id::text, created_at, resolved_at"
)


def _c_row(row: tuple) -> CorrectionRecord:
    return CorrectionRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        user_id=str(row[2]) if row[2] else None,
        scope=str(row[3]),
        target=(row[4] if isinstance(row[4], dict)
                else (json.loads(row[4]) if row[4] else {})),
        observed_value=row[5],
        correct_value=row[6],
        reason=row[7],
        severity=str(row[8]),
        status=str(row[9]),
        resolution=(row[10] if isinstance(row[10], dict) else None),
        audit_query_id=str(row[11]) if row[11] else None,
        created_at=(row[12].isoformat() if hasattr(row[12], "isoformat") else str(row[12])),
        resolved_at=(
            row[13].isoformat() if (row[13] and hasattr(row[13], "isoformat"))
            else (str(row[13]) if row[13] else None)
        ),
    )


async def insert_correction(
    conn: Connection,
    *,
    workspace_id: str,
    scope: str,
    target: dict,
    observed_value: str | None = None,
    correct_value: str | None = None,
    reason: str | None = None,
    severity: str = "important",
    user_id: str | None = None,
    audit_query_id: str | None = None,
    status: str = "open",
) -> str:
    if scope not in CORRECTION_SCOPES:
        raise ValueError(f"scope must be one of {CORRECTION_SCOPES}, got {scope!r}")
    if severity not in CORRECTION_SEVERITIES:
        raise ValueError(f"severity must be one of {CORRECTION_SEVERITIES}, got {severity!r}")
    if status not in CORRECTION_STATUSES:
        raise ValueError(f"status must be one of {CORRECTION_STATUSES}, got {status!r}")
    cur = await conn.execute(
        """
        INSERT INTO corrections
            (workspace_id, user_id, scope, target, observed_value, correct_value,
             reason, severity, status, audit_query_id)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id, user_id, scope,
            json.dumps(target or {}),
            observed_value, correct_value, reason, severity, status,
            audit_query_id,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def read_correction(
    conn: Connection, *, correction_id: str,
) -> CorrectionRecord | None:
    cur = await conn.execute(
        f"SELECT {_C_COLS} FROM corrections WHERE id = %s",
        (correction_id,),
    )
    row = await cur.fetchone()
    return _c_row(row) if row else None


async def update_correction_status(
    conn: Connection,
    *,
    correction_id: str,
    status: str,
    resolution: dict | None = None,
) -> bool:
    if status not in CORRECTION_STATUSES:
        raise ValueError(f"status must be one of {CORRECTION_STATUSES}, got {status!r}")
    cur = await conn.execute(
        """
        UPDATE corrections SET
            status = %s,
            resolution = COALESCE(%s::jsonb, resolution),
            resolved_at = CASE
                WHEN %s IN ('verified', 'closed', 'rejected') THEN NOW()
                ELSE resolved_at
            END
         WHERE id = %s
        """,
        (
            status,
            json.dumps(resolution) if resolution is not None else None,
            status,
            correction_id,
        ),
    )
    return getattr(cur, "rowcount", 0) > 0


async def list_corrections(
    conn: Connection,
    *,
    workspace_id: str,
    status: str | None = None,
    scope: str | None = None,
    limit: int = 100,
) -> list[CorrectionRecord]:
    clauses = ["workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if status is not None:
        if status not in CORRECTION_STATUSES:
            raise ValueError(f"status filter must be one of {CORRECTION_STATUSES}")
        clauses.append("status = %s")
        params.append(status)
    if scope is not None:
        if scope not in CORRECTION_SCOPES:
            raise ValueError(f"scope filter must be one of {CORRECTION_SCOPES}")
        clauses.append("scope = %s")
        params.append(scope)
    where = " AND ".join(clauses)
    params.append(limit)
    cur = await conn.execute(
        f"SELECT {_C_COLS} FROM corrections WHERE {where} "
        f"ORDER BY created_at DESC LIMIT %s",
        tuple(params),
    )
    return [_c_row(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# entity_overrides
# ---------------------------------------------------------------------------


_EO_COLS = (
    "id::text, workspace_id::text, rule_type, entity_a::text, entity_b::text, "
    "rename_to, reason, active, correction_id::text, created_at"
)


def _eo_row(row: tuple) -> EntityOverrideRecord:
    return EntityOverrideRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        rule_type=str(row[2]),
        entity_a=str(row[3]) if row[3] else None,
        entity_b=str(row[4]) if row[4] else None,
        rename_to=row[5],
        reason=row[6],
        active=bool(row[7]),
        correction_id=str(row[8]) if row[8] else None,
        created_at=(row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9])),
    )


async def insert_entity_override(
    conn: Connection,
    *,
    workspace_id: str,
    rule_type: str,
    entity_a: str | None = None,
    entity_b: str | None = None,
    rename_to: str | None = None,
    reason: str | None = None,
    correction_id: str | None = None,
    created_by: str | None = None,
) -> str:
    if rule_type not in ENTITY_OVERRIDE_RULES:
        raise ValueError(
            f"rule_type must be one of {ENTITY_OVERRIDE_RULES}, got {rule_type!r}"
        )
    cur = await conn.execute(
        """
        INSERT INTO entity_overrides
            (workspace_id, rule_type, entity_a, entity_b, rename_to,
             reason, correction_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id, rule_type, entity_a, entity_b, rename_to,
            reason, correction_id, created_by,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def list_active_entity_overrides(
    conn: Connection, *, workspace_id: str,
) -> list[EntityOverrideRecord]:
    cur = await conn.execute(
        f"SELECT {_EO_COLS} FROM entity_overrides "
        f"WHERE workspace_id = %s AND active = true "
        f"ORDER BY created_at DESC",
        (workspace_id,),
    )
    return [_eo_row(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# schema_field_overrides
# ---------------------------------------------------------------------------


_SFO_COLS = (
    "id::text, workspace_id::text, field_path, override_kind, details, "
    "reason, active, correction_id::text, created_at"
)


def _sfo_row(row: tuple) -> SchemaFieldOverrideRecord:
    return SchemaFieldOverrideRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        field_path=str(row[2]),
        override_kind=str(row[3]),
        details=(row[4] if isinstance(row[4], dict)
                 else (json.loads(row[4]) if row[4] else {})),
        reason=row[5],
        active=bool(row[6]),
        correction_id=str(row[7]) if row[7] else None,
        created_at=(row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8])),
    )


async def insert_schema_field_override(
    conn: Connection,
    *,
    workspace_id: str,
    field_path: str,
    override_kind: str,
    details: dict | None = None,
    reason: str | None = None,
    correction_id: str | None = None,
    created_by: str | None = None,
) -> str:
    if override_kind not in SCHEMA_FIELD_OVERRIDE_KINDS:
        raise ValueError(
            f"override_kind must be one of {SCHEMA_FIELD_OVERRIDE_KINDS}, "
            f"got {override_kind!r}"
        )
    cur = await conn.execute(
        """
        INSERT INTO schema_field_overrides
            (workspace_id, field_path, override_kind, details, reason,
             correction_id, created_by)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id, field_path, override_kind,
            json.dumps(details or {}), reason, correction_id, created_by,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def list_active_schema_field_overrides(
    conn: Connection, *, workspace_id: str,
) -> list[SchemaFieldOverrideRecord]:
    cur = await conn.execute(
        f"SELECT {_SFO_COLS} FROM schema_field_overrides "
        f"WHERE workspace_id = %s AND active = true "
        f"ORDER BY created_at DESC",
        (workspace_id,),
    )
    return [_sfo_row(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# regression_set
# ---------------------------------------------------------------------------


_R_COLS = (
    "id::text, workspace_id::text, source_correction_id::text, "
    "query_text, expected_facts, implicated_docs, severity, "
    "active, fail_count, created_at"
)


def _r_row(row: tuple) -> RegressionEntryRecord:
    return RegressionEntryRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        source_correction_id=str(row[2]) if row[2] else None,
        query_text=str(row[3]),
        expected_facts=(row[4] if isinstance(row[4], dict)
                        else (json.loads(row[4]) if row[4] else {})),
        implicated_docs=tuple(str(d) for d in (row[5] or [])),
        severity=str(row[6]),
        active=bool(row[7]),
        fail_count=int(row[8] or 0),
        created_at=(row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9])),
    )


async def insert_regression_entry(
    conn: Connection,
    *,
    workspace_id: str,
    query_text: str,
    expected_facts: dict,
    source_correction_id: str | None = None,
    implicated_docs: list[str] | None = None,
    severity: str = "important",
) -> str:
    if severity not in CORRECTION_SEVERITIES:
        raise ValueError(f"severity must be one of {CORRECTION_SEVERITIES}")
    cur = await conn.execute(
        """
        INSERT INTO regression_set
            (workspace_id, source_correction_id, query_text, expected_facts,
             implicated_docs, severity)
        VALUES (%s, %s, %s, %s::jsonb, %s::uuid[], %s)
        RETURNING id::text
        """,
        (
            workspace_id, source_correction_id, query_text,
            json.dumps(expected_facts or {}),
            implicated_docs or [],
            severity,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def list_active_regressions(
    conn: Connection, *, workspace_id: str, limit: int = 200,
) -> list[RegressionEntryRecord]:
    cur = await conn.execute(
        f"SELECT {_R_COLS} FROM regression_set "
        f"WHERE workspace_id = %s AND active = true "
        f"ORDER BY severity, created_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    return [_r_row(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteOutcome:
    """Returned by `route_correction`. Records the side-effects applied
    so the caller can echo them in the response."""
    final_status: str
    resolution: dict
    entity_override_id: str | None = None
    schema_field_override_id: str | None = None
    regression_entry_id: str | None = None
    notes: str | None = None


async def route_correction(
    conn: Connection,
    *,
    correction: CorrectionRecord,
) -> RouteOutcome:
    """Apply scope-conditional side-effects. The correction's status is
    updated in-place. Returns RouteOutcome describing what happened.

    Side-effects per scope:

      entity_merge  → entity_overrides(rule_type='never_merge'); status='fixing'
      entity_split  → entity_overrides(rule_type='split');        status='fixing'
      schema_field  → schema_field_overrides(undo_promotion);     status='verified'
                      (the override is the entire fix — no follow-up needed)
      source_authority → kb.domain.conflicts.set_source_authority_override;
                      status='verified'
      extraction    → status='fixing' (worker re-extraction is a follow-up)
      answer/citation → status='triaged' (re-run augmented later)
      doc_chain     → status='fixing' (chain re-detection is a follow-up)
      other         → status='triaged' (manual review)

    A regression_set entry is created for blocker / important severities
    when the target carries a query_text (so the eval harness can replay).
    """
    scope = correction.scope
    target = correction.target or {}
    resolution: dict[str, Any] = {"scope": scope}
    final_status = "triaged"
    entity_override_id: str | None = None
    schema_field_override_id: str | None = None
    notes: str | None = None

    if scope == "entity_merge":
        ent_a = target.get("entity_a")
        ent_b = target.get("entity_b")
        entity_override_id = await insert_entity_override(
            conn,
            workspace_id=correction.workspace_id,
            rule_type="never_merge",
            entity_a=ent_a, entity_b=ent_b,
            reason=correction.reason or "user-reported wrong merge",
            correction_id=correction.id,
        )
        resolution["entity_override_id"] = entity_override_id
        final_status = "fixing"
        notes = (
            "never_merge rule recorded; re-resolution of the affected cluster "
            "is scheduled for the next mention-resolution pass"
        )

    elif scope == "entity_split":
        ent_a = target.get("entity_a")
        ent_b = target.get("entity_b")
        entity_override_id = await insert_entity_override(
            conn,
            workspace_id=correction.workspace_id,
            rule_type="split",
            entity_a=ent_a, entity_b=ent_b,
            reason=correction.reason or "user-reported wrong split",
            correction_id=correction.id,
        )
        resolution["entity_override_id"] = entity_override_id
        final_status = "fixing"
        notes = "split rule recorded; cluster re-resolution scheduled"

    elif scope == "schema_field":
        field_path = target.get("field_path") or ""
        override_kind = target.get("override_kind") or "undo_promotion"
        details = target.get("details") or {}
        if field_path:
            schema_field_override_id = await insert_schema_field_override(
                conn,
                workspace_id=correction.workspace_id,
                field_path=field_path,
                override_kind=override_kind,
                details=details,
                reason=correction.reason,
                correction_id=correction.id,
            )
            resolution["schema_field_override_id"] = schema_field_override_id
            final_status = "verified"
            notes = (
                f"schema_field_override applied for {field_path} "
                f"(kind={override_kind}); no further fix needed"
            )
        else:
            final_status = "triaged"
            notes = "schema_field correction missing field_path target"

    elif scope == "source_authority":
        # Delegate to B2's set_source_authority_override.
        file_id = target.get("file_id")
        authority = target.get("authority")
        if file_id is not None and authority is not None:
            try:
                from kb.domain.conflicts import set_source_authority_override
                await set_source_authority_override(
                    conn, file_id=str(file_id),
                    authority=float(authority),
                    reason=correction.reason or "user override via /corrections",
                )
                final_status = "verified"
                notes = f"source_authority for file={file_id} set to {authority}"
                resolution["file_id"] = file_id
                resolution["authority"] = float(authority)
            except Exception as exc:  # noqa: BLE001
                final_status = "triaged"
                notes = f"override failed: {exc}"
        else:
            final_status = "triaged"
            notes = "source_authority correction missing file_id/authority"

    elif scope == "extraction":
        # Wave A close-up (Design 4 §"Pipeline integration") — defer
        # targeted re-extraction tasks for the implicated docs when
        # the severity warrants it. The worker re-runs the field +
        # atomic-unit extraction on each file; the new extracted_*
        # rows OVERWRITE the old ones via per-file idempotency.
        #
        # Best-effort: a defer failure (procrastinate misconfigured,
        # network blip) leaves the correction at status='fixing' so
        # an operator can re-route manually. We never fail the
        # correction submission over the defer.
        implicated = [
            str(d) for d in (target.get("implicated_docs") or [])
            if d
        ]
        deferred_for: list[str] = []
        if (
            implicated
            and correction.severity in ("blocker", "important")
        ):
            try:
                from kb.workers.tasks import procrastinate_app
                for file_id in implicated:
                    try:
                        await procrastinate_app.configure_task(
                            name="extract_fields_file"
                        ).defer_async(file_id=file_id)
                        await procrastinate_app.configure_task(
                            name="extract_kv_tables_file"
                        ).defer_async(file_id=file_id)
                        deferred_for.append(file_id)
                    except Exception as exc:  # noqa: BLE001
                        # Per-file failure — keep going so the other
                        # implicated docs still get re-extracted.
                        notes = (
                            (notes or "")
                            + f"; defer failed for {file_id}: {exc}"
                        )
            except ImportError:
                # Procrastinate not importable in some test paths.
                deferred_for = []

        final_status = "fixing"
        resolution["deferred_re_extraction_for"] = deferred_for
        if deferred_for:
            notes = (
                f"extraction correction recorded; targeted re-extraction "
                f"deferred for {len(deferred_for)} doc(s)"
            )
        elif implicated:
            notes = (
                "extraction correction recorded; re-extraction defer "
                "skipped (procrastinate unavailable or per-file failure)"
            )
        else:
            notes = (
                "extraction correction recorded; no implicated_docs "
                "supplied so no re-extraction was triggered"
            )

    elif scope in ("answer", "citation"):
        final_status = "triaged"
        notes = (
            "answer/citation correction recorded; the orchestrator will "
            "re-run augmented with this hint when the query is replayed"
        )

    elif scope == "doc_chain":
        final_status = "fixing"
        notes = "doc_chain correction recorded; chain re-detection scheduled"

    else:  # 'other'
        final_status = "triaged"
        notes = "correction queued for manual triage"

    # Promote a regression_set entry for blocker / important severities
    # whenever we have a replayable query_text in the target.
    regression_entry_id: str | None = None
    query_text = (target.get("query_text") or "").strip()
    if (
        query_text
        and correction.severity in ("blocker", "important")
    ):
        try:
            regression_entry_id = await insert_regression_entry(
                conn,
                workspace_id=correction.workspace_id,
                query_text=query_text,
                expected_facts={
                    "correct_value": correction.correct_value,
                    "must_not_match": correction.observed_value,
                    "scope": scope,
                },
                source_correction_id=correction.id,
                implicated_docs=[d for d in (target.get("implicated_docs") or [])],
                severity=correction.severity,
            )
            resolution["regression_entry_id"] = regression_entry_id
        except Exception as exc:  # noqa: BLE001
            notes = (notes or "") + f"; regression entry insert failed: {exc}"

    # Persist the status + resolution payload.
    await update_correction_status(
        conn,
        correction_id=correction.id,
        status=final_status,
        resolution=resolution,
    )

    return RouteOutcome(
        final_status=final_status,
        resolution=resolution,
        entity_override_id=entity_override_id,
        schema_field_override_id=schema_field_override_id,
        regression_entry_id=regression_entry_id,
        notes=notes,
    )
