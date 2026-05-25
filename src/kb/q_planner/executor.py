"""B4b — Q-mode executor (Design 1 layers 7 + 8 + 9: read-only role,
statement_timeout, row cap).

Wraps the compiled SQL in a SAVEPOINT with `SET LOCAL statement_timeout`
(layer 8 = 30s default). Layer 7 (read-only role) lives in the migration
as a dedicated `kb_app_q` role that holds SELECT-only grants on the
catalog tables — wired into orchestrator connections by a follow-up
commit that introduces per-mode connection pools.

For Wave A we DO NOT use `SET LOCAL transaction_read_only = on` inside
the outer chat() transaction. PostgreSQL refuses to flip read_only=off
mid-transaction ("cannot set transaction read-write mode inside a read-
only transaction"), which deadlocks the subsequent audit_queries
INSERT. The compiler's grammar is the load-bearing defense: it never
emits anything but a SELECT. Layer 9 (LIMIT clamp) lives in the
compiler too. Layer 10 (audit_queries) lives in the mode_router.

The compiled SQL itself ends in a LIMIT clause clamped to row_cap
(layer 9), so the executor doesn't have to enforce row count separately.
That said, we still abort if .fetchall() returns more than row_cap+1
rows as a sanity check.

Returns a `QExecutionResult` carrying the rows + column names + runtime.
On timeout, refusal, or any DB error the executor returns a
`QExecutionResult(status='...')` rather than propagating the raw
psycopg exception — callers (mode_router) need a stable shape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# Defaults — overridable per call.
DEFAULT_ROW_CAP: int = 100_000
DEFAULT_TIMEOUT_MS: int = 30_000


class QExecutionError(RuntimeError):
    """Raised only for programmer errors (e.g. malformed (sql, params)
    tuple). Normal SQL failures land in QExecutionResult.status."""


@dataclass(frozen=True)
class QExecutionResult:
    status: str                         # 'ok' | 'timeout' | 'row_cap_exceeded' | 'error'
    rows: tuple[tuple[Any, ...], ...] = field(default_factory=tuple)
    column_names: tuple[str, ...] = field(default_factory=tuple)
    row_count: int = 0
    runtime_ms: int = 0
    error_message: str | None = None


async def execute(
    conn: Any,
    sql: str,
    params: list,
    *,
    row_cap: int = DEFAULT_ROW_CAP,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> QExecutionResult:
    """Execute a compiled Q-mode SQL against `conn`.

    Wraps the query in a SAVEPOINT so the outer transaction (if any)
    survives a failed Q execution. SET LOCAL knobs apply for the duration
    of the SAVEPOINT scope only.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise QExecutionError("sql must be a non-empty string")
    if params is None:
        params = []

    sp_name = "q_mode_sp"
    t0 = time.monotonic()

    try:
        # SAVEPOINT scoping so SET LOCAL doesn't bleed.
        # Note: SAVEPOINT requires an active transaction. psycopg3 async
        # connections may be autocommit; callers ensure a transaction is
        # open (the orchestrator's chat() owns one). For tests calling
        # the executor directly without a transaction, the SAVEPOINT
        # fails — we handle that as a fall-through.
        try:
            await conn.execute(f"SAVEPOINT {sp_name}")
            in_savepoint = True
        except Exception as _sp_exc:
            import logging
            logging.getLogger(__name__).debug(
                "Q-mode SAVEPOINT failed (likely no outer txn): %s", _sp_exc,
            )
            in_savepoint = False

        try:
            # Layer 8 — statement_timeout caps runtime. Layer 7 (read-only)
            # is deferred to per-mode pool wiring (see module docstring).
            #
            # PostgreSQL doesn't accept bind parameters in SET LOCAL, so
            # we interpolate the integer ourselves. The value is server-
            # controlled (an int passed from kb code) — never user input.
            timeout_ms_int = int(timeout_ms)
            if timeout_ms_int <= 0 or timeout_ms_int > 600_000:
                raise QExecutionError(
                    f"timeout_ms out of range: {timeout_ms_int}"
                )
            await conn.execute(
                f"SET LOCAL statement_timeout = '{timeout_ms_int}ms'"
            )

            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            # psycopg3 cursor exposes description for SELECT.
            description = getattr(cur, "description", None) or []
            col_names = tuple(
                d.name if hasattr(d, "name") else str(d[0])
                for d in description
            )
        except Exception as exc:  # noqa: BLE001
            # Distinguish timeout vs. generic SQL error.
            msg = str(exc)
            if in_savepoint:
                try:
                    await conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                except Exception:  # noqa: BLE001
                    pass

            runtime_ms = int((time.monotonic() - t0) * 1000)
            if "statement timeout" in msg.lower() or "57014" in msg:
                return QExecutionResult(
                    status="timeout",
                    runtime_ms=runtime_ms,
                    error_message="query exceeded statement_timeout",
                )
            return QExecutionResult(
                status="error",
                runtime_ms=runtime_ms,
                error_message=msg[:500],
            )

        # SET LOCAL statement_timeout persists through SAVEPOINT RELEASE,
        # so we explicitly reset to '0' (no timeout) on the success path.
        # This must NOT raise — wrap defensively.
        async def _restore_settings():
            if not in_savepoint:
                return
            try:
                await conn.execute("SET LOCAL statement_timeout = '0'")
            except Exception as _e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning(
                    "Q-mode failed to reset statement_timeout: %s", _e,
                )

        # Sanity check on the row_cap layer.
        if len(rows) > row_cap:
            await _restore_settings()
            if in_savepoint:
                try:
                    await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                except Exception:  # noqa: BLE001
                    pass
            return QExecutionResult(
                status="row_cap_exceeded",
                row_count=len(rows),
                runtime_ms=int((time.monotonic() - t0) * 1000),
                error_message=f"result exceeded row_cap={row_cap}",
            )

        await _restore_settings()
        if in_savepoint:
            try:
                await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:  # noqa: BLE001
                pass

        return QExecutionResult(
            status="ok",
            rows=tuple(tuple(r) for r in rows),
            column_names=col_names,
            row_count=len(rows),
            runtime_ms=int((time.monotonic() - t0) * 1000),
        )

    except QExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001
        return QExecutionResult(
            status="error",
            runtime_ms=int((time.monotonic() - t0) * 1000),
            error_message=str(exc)[:500],
        )
