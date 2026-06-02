"""T1 (schema-as-a-view) — in-place canonical key rewrite + audit.

When corpus convergence decides that several raw field spellings mean the
same thing (e.g. `end_balance`, `closing_bal` → `closing_balance`), we
rewrite the key *in the stored schema-based extraction*
(`extracted_entities.fields`) rather than re-reading the documents. This is
the cheap, scalable alternative to the old blanket re-extraction: one
set-based SQL UPDATE per rename, no LLM, no re-parse — so a new upload never
re-processes previously-ready docs.

The raw open-vocab layer (`proposed_fields`) is left untouched as immutable
evidence. Every rewrite is recorded append-only in `field_rename_audit` so
the decision is reversible + auditable (finance provenance).

Collision rule: if a row already holds the canonical key, the canonical
value WINS and the raw key is dropped (a merge), so we never clobber a real
canonical value with a stale variant.

These UPDATEs touch `extracted_entities.fields`, which is immutable for the
kb_app role (0017 GRANT) — they run from the worker, which connects as the
superuser DB role, exactly like the existing force-reextract field refresh.
"""

from __future__ import annotations

from kb.db.pool import Connection

# One CASE-based rewrite, reused for both scalars and columns. The only
# difference is the row-selection WHERE clause, supplied by the caller.
#   - canonical already present  → drop the raw key (canonical wins / merge)
#   - canonical absent           → move raw's value under the canonical key
_REWRITE_SET = (
    "SET fields = CASE "
    "  WHEN jsonb_exists(ee.fields, %(canon)s) THEN ee.fields - %(raw)s "
    "  ELSE (ee.fields - %(raw)s) "
    "       || jsonb_build_object(%(canon)s, ee.fields -> %(raw)s) "
    "END "
)


async def rename_doc_root_field_keys(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str,
    rename_map: dict[str, str],
) -> dict[str, int]:
    """Rewrite scalar keys in doc_root `extracted_entities.fields`
    (`unit_type IS NULL`) for every file of `inferred_doc_type`.

    `rename_map` is {raw_key: canonical_key}. Returns {raw_key: rows_touched}
    for the keys that actually existed somewhere (so the caller can audit a
    precise per-key row count). No-op entries (raw == canonical, blanks) are
    skipped.
    """
    out: dict[str, int] = {}
    for raw, canon in rename_map.items():
        if not raw or not canon or raw == canon:
            continue
        cur = await conn.execute(
            "UPDATE extracted_entities ee " + _REWRITE_SET +
            "FROM files f "
            "WHERE ee.file_id = f.id "
            "  AND ee.workspace_id = %(ws)s "
            "  AND f.inferred_doc_type = %(dt)s "
            "  AND ee.unit_type IS NULL "
            "  AND jsonb_exists(ee.fields, %(raw)s)",
            {"raw": raw, "canon": canon, "ws": workspace_id,
             "dt": inferred_doc_type},
        )
        out[raw] = cur.rowcount or 0
    return out


async def rename_sub_entity_column_keys(
    conn: Connection,
    *,
    workspace_id: str,
    unit_type: str,
    rename_map: dict[str, str],
) -> dict[str, int]:
    """Rewrite column keys in child `extracted_entities.fields` for one
    `unit_type` (a table). Same shape + collision rule as the doc_root
    rename. Returns {raw_key: rows_touched}."""
    out: dict[str, int] = {}
    for raw, canon in rename_map.items():
        if not raw or not canon or raw == canon:
            continue
        cur = await conn.execute(
            "UPDATE extracted_entities ee " + _REWRITE_SET +
            "WHERE ee.workspace_id = %(ws)s "
            "  AND ee.unit_type = %(ut)s "
            "  AND jsonb_exists(ee.fields, %(raw)s)",
            {"raw": raw, "canon": canon, "ws": workspace_id, "ut": unit_type},
        )
        out[raw] = cur.rowcount or 0
    return out


async def insert_field_rename_audit(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str,
    scope: str,                 # 'scalar' | 'column'
    raw_key: str,
    canonical_key: str,
    unit_type: str | None = None,
    method: str = "convergence",
    confidence: float = 1.0,
    n_rows: int = 0,
) -> None:
    """Append one immutable audit row recording a raw→canonical key rewrite."""
    await conn.execute(
        "INSERT INTO field_rename_audit "
        "(workspace_id, inferred_doc_type, scope, unit_type, raw_key, "
        " canonical_key, method, confidence, n_rows) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (workspace_id, inferred_doc_type, scope, unit_type, raw_key,
         canonical_key, method, confidence, n_rows),
    )


async def read_sub_entity_columns_per_file(
    conn: Connection, *, workspace_id: str,
) -> dict[str, dict[str, list[str]]]:
    """Return {unit_type: {file_id: [column_key, ...]}} from child
    `extracted_entities`. This is the column-side analogue of
    `read_proposed_fields_for_doctype` — it feeds column convergence so table
    columns get the same consolidation treatment as scalar fields.

    DISTINCT collapses the per-row repetition (a file with 50 transaction rows
    surfaces each column once), so each (unit_type, file) lists its columns
    once.
    """
    cur = await conn.execute(
        "SELECT DISTINCT unit_type, file_id::text, jsonb_object_keys(fields) AS col "
        "FROM extracted_entities "
        "WHERE workspace_id = %s "
        "  AND unit_type IS NOT NULL "
        "  AND fields IS NOT NULL",
        (workspace_id,),
    )
    out: dict[str, dict[str, list[str]]] = {}
    for unit_type, file_id, col in await cur.fetchall():
        out.setdefault(unit_type, {}).setdefault(file_id, []).append(col)
    return out


async def read_field_rename_audit(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str | None = None,
) -> list[dict]:
    """Read audit rows (newest first). For tests + a future UI undo / lineage
    view. Scoped to a doc_type when given."""
    base = (
        "SELECT inferred_doc_type, scope, unit_type, raw_key, canonical_key, "
        "       method, confidence, n_rows, created_at "
        "FROM field_rename_audit WHERE workspace_id = %s "
    )
    if inferred_doc_type is None:
        cur = await conn.execute(base + "ORDER BY created_at DESC", (workspace_id,))
    else:
        cur = await conn.execute(
            base + "AND inferred_doc_type = %s ORDER BY created_at DESC",
            (workspace_id, inferred_doc_type),
        )
    rows = await cur.fetchall()
    return [
        {
            "inferred_doc_type": r[0], "scope": r[1], "unit_type": r[2],
            "raw_key": r[3], "canonical_key": r[4], "method": r[5],
            "confidence": r[6], "n_rows": r[7], "created_at": r[8],
        }
        for r in rows
    ]
