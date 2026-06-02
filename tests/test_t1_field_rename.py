"""T1 (schema-as-a-view) — convergence rewrites stored keys IN PLACE.

The old corpus-finalize re-extracted every ready doc (2 LLM calls each) on
every settle just to propagate converged canonical names into the stored
extraction — O(corpus) per upload. T1 replaces that with an in-place key
rewrite of `extracted_entities.fields` (no LLM, no re-read) for both scalar
doc_root fields and table (sub_entity) columns, recorded append-only in
`field_rename_audit`. Re-extraction is now a user-only action.

These tests run the convergence impls against a real Postgres (the worker
connects as superuser, which is what's allowed to UPDATE
`extracted_entities.fields`).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

pytestmark = pytest.mark.asyncio


@contextmanager
def _use_db(db_url: str):
    """Point settings.database_url (KB_DATABASE_URL) at the testcontainer DB —
    the convergence impls open their own connection from get_settings()."""
    from kb.config import get_settings

    prior = os.environ.get("KB_DATABASE_URL")
    os.environ["KB_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("KB_DATABASE_URL", None)
        else:
            os.environ["KB_DATABASE_URL"] = prior
        get_settings.cache_clear()


async def _fake_embed(texts):
    # All names embed identically → cosine 1.0 ≥ blocking threshold, so every
    # pair reaches the judge (which decides the actual merge).
    return [[1.0, 0.0, 0.0] for _ in texts]


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_scalar_doc(
    db_url: str,
    *,
    workspace: str,
    doc_type: str,
    field_name: str,
    value,
    doc_root_fields: dict | None = None,
) -> str:
    """Insert a ready file + one proposed_field (drives clustering) + a doc_root
    extracted_entities row whose `fields` hold the variant key (the thing the
    rename rewrites). `doc_root_fields` overrides the doc_root jsonb when you
    need a collision (both raw + canonical present)."""
    from kb.domain.extracted_entities import insert_extracted_entity
    from kb.domain.fields import insert_proposed_field
    from kb.extraction.promotion import ensure_auto_schema_entity

    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    fields = doc_root_fields if doc_root_fields is not None else {field_name: value}
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s,%s,%s,%s,%s,'application/pdf',100,'ready',%s)",
            (file_id, workspace, f"f-{field_name}", sha, f"raw_files/{sha}", doc_type),
        )
        await insert_proposed_field(
            conn, file_id=file_id, workspace_id=workspace,
            inferred_doc_type=doc_type, field_name=field_name,
            field_description="x", value_text=str(value), value_type="number",
            is_pii=False, model_id="test",
        )
        _, doc_root_id = await ensure_auto_schema_entity(
            conn, workspace_id=workspace, doc_type=doc_type,
        )
        await insert_extracted_entity(
            conn, schema_entity_id=doc_root_id, file_id=file_id,
            workspace_id=workspace, fields=fields, citations={},
            model_id="test", unit_type=None,
        )
        await conn.commit()
    return file_id


async def _seed_unit_rows(
    db_url: str,
    *,
    workspace: str,
    doc_type: str,
    unit_type: str,
    rows: list[dict],
) -> str:
    """Insert a ready file + N child sub_entity rows for `unit_type`, each with
    the given `fields` dict. Returns the file_id."""
    from kb.domain.extracted_entities import insert_extracted_entity
    from kb.extraction.promotion import (
        ensure_auto_schema_entity,
        ensure_contains_relationship,
        ensure_sub_entity_type,
    )

    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s,%s,%s,%s,%s,'application/pdf',100,'ready',%s)",
            (file_id, workspace, f"u-{unit_type}", sha, f"raw_files/{sha}", doc_type),
        )
        schema_id, doc_root_id = await ensure_auto_schema_entity(
            conn, workspace_id=workspace, doc_type=doc_type,
        )
        sub_id = await ensure_sub_entity_type(
            conn, workspace_id=workspace, schema_id=schema_id,
            parent_type_id=doc_root_id, unit_type=unit_type,
        )
        await ensure_contains_relationship(
            conn, workspace_id=workspace, schema_id=schema_id,
            parent_entity_id=doc_root_id, child_entity_id=sub_id,
        )
        for fields in rows:
            await insert_extracted_entity(
                conn, schema_entity_id=sub_id, file_id=file_id,
                workspace_id=workspace, fields=fields, citations={},
                model_id="test", unit_type=unit_type,
            )
        await conn.commit()
    return file_id


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def _doc_root_fields(db_url: str, workspace: str, file_id: str) -> dict | None:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT fields FROM extracted_entities "
            "WHERE file_id = %s AND unit_type IS NULL",
            (file_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def _unit_rows(db_url: str, workspace: str, file_id: str, unit_type: str) -> list[dict]:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT fields FROM extracted_entities "
            "WHERE file_id = %s AND unit_type = %s ORDER BY created_at",
            (file_id, unit_type),
        )
        return [r[0] for r in await cur.fetchall()]


async def _audit(db_url: str, workspace: str) -> list[dict]:
    from kb.domain.field_rename import read_field_rename_audit

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        return await read_field_rename_audit(conn, workspace_id=workspace)


async def _subentity_field_names(db_url: str, workspace: str, unit_type: str) -> set[str]:
    from kb.extraction.promotion import sub_entity_name_for

    name = sub_entity_name_for(unit_type)
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT sf.name FROM schema_fields sf "
            "JOIN schema_entities se ON se.id = sf.entity_id "
            "WHERE se.workspace_id = %s AND se.name = %s "
            "  AND se.kind = 'sub_entity' AND sf.lifecycle_state = 'active'",
            (workspace, name),
        )
        return {r[0] for r in await cur.fetchall()}


# ---------------------------------------------------------------------------
# Scalar convergence → in-place doc_root key rewrite
# ---------------------------------------------------------------------------


async def _judge_totals(a, b):
    return {a.canonical_name, b.canonical_name} == {"total_cost", "total_amount"}


async def test_scalar_convergence_renames_stored_doc_root_keys(db_url_superuser):
    """total_amount (1 doc) merges into total_cost (2 docs, dominant); the
    stored doc_root key on the total_amount doc is REWRITTEN in place — no
    re-extraction — and the rewrite is audited."""
    from kb.workers.tasks import converge_workspace_fields_impl

    ws = str(uuid.uuid4())
    await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                           field_name="total_cost", value=100)
    await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                           field_name="total_cost", value=110)
    f3 = await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                                field_name="total_amount", value=200)

    with _use_db(db_url_superuser):
        summary = await converge_workspace_fields_impl(
            workspace_id=ws, embed_fn=_fake_embed, judge_fn=_judge_totals,
        )

    fields = await _doc_root_fields(db_url_superuser, ws, f3)
    assert fields is not None
    assert "total_cost" in fields and "total_amount" not in fields, fields
    assert fields["total_cost"] == 200, fields
    assert summary["renamed_scalar_keys"] >= 1, summary

    audit = await _audit(db_url_superuser, ws)
    assert any(
        r["scope"] == "scalar" and r["raw_key"] == "total_amount"
        and r["canonical_key"] == "total_cost" and r["n_rows"] >= 1
        for r in audit
    ), audit


async def test_scalar_merge_collision_keeps_canonical(db_url_superuser):
    """If a doc already holds the canonical key, the canonical value WINS and
    the variant key is dropped (a merge, never a clobber)."""
    from kb.workers.tasks import converge_workspace_fields_impl

    ws = str(uuid.uuid4())
    await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                           field_name="total_cost", value=100)
    await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                           field_name="total_cost", value=110)
    # f3 proposes total_amount but its doc_root jsonb holds BOTH keys.
    f3 = await _seed_scalar_doc(
        db_url_superuser, workspace=ws, doc_type="contract",
        field_name="total_amount", value=200,
        doc_root_fields={"total_amount": 200, "total_cost": 999},
    )

    with _use_db(db_url_superuser):
        await converge_workspace_fields_impl(
            workspace_id=ws, embed_fn=_fake_embed, judge_fn=_judge_totals,
        )

    fields = await _doc_root_fields(db_url_superuser, ws, f3)
    assert fields == {"total_cost": 999}, fields  # canonical kept, variant dropped


async def test_scalar_convergence_leaves_distinct_fields_untouched(db_url_superuser):
    """A field with no variant is never renamed (nothing to converge)."""
    from kb.workers.tasks import converge_workspace_fields_impl

    ws = str(uuid.uuid4())
    f1 = await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                                field_name="vendor_name", value="ACME")

    async def judge_never(a, b):
        return False

    with _use_db(db_url_superuser):
        summary = await converge_workspace_fields_impl(
            workspace_id=ws, embed_fn=_fake_embed, judge_fn=judge_never,
        )

    fields = await _doc_root_fields(db_url_superuser, ws, f1)
    assert fields == {"vendor_name": "ACME"}, fields
    assert summary["renamed_scalar_keys"] == 0, summary
    assert await _audit(db_url_superuser, ws) == []


# ---------------------------------------------------------------------------
# Column convergence → in-place sub_entity column rewrite + promotion
# ---------------------------------------------------------------------------


async def _judge_debit(a, b):
    return {a.canonical_name, b.canonical_name} == {"debit", "debit_amount"}


async def test_column_convergence_renames_and_promotes(db_url_superuser):
    """A table column variant (`debit` → `debit_amount`) is consolidated in the
    stored child rows in place, audited, and the canonical column is promoted
    into the sub_entity schema once it repeats across N docs."""
    from kb.workers.tasks import converge_workspace_columns_impl

    ws = str(uuid.uuid4())
    # debit_amount appears in 2 files (dominant), debit in 1 file.
    await _seed_unit_rows(db_url_superuser, workspace=ws, doc_type="bank_statement",
                          unit_type="transaction",
                          rows=[{"debit_amount": 10}, {"debit_amount": 20}])
    await _seed_unit_rows(db_url_superuser, workspace=ws, doc_type="bank_statement",
                          unit_type="transaction", rows=[{"debit_amount": 30}])
    fc = await _seed_unit_rows(db_url_superuser, workspace=ws, doc_type="bank_statement",
                               unit_type="transaction", rows=[{"debit": 40}])

    with _use_db(db_url_superuser):
        summary = await converge_workspace_columns_impl(
            workspace_id=ws, embed_fn=_fake_embed, judge_fn=_judge_debit,
        )

    rows = await _unit_rows(db_url_superuser, ws, fc, "transaction")
    assert rows and all("debit_amount" in r and "debit" not in r for r in rows), rows
    assert rows[0]["debit_amount"] == 40, rows
    assert summary["renamed_column_keys"] >= 1, summary

    audit = await _audit(db_url_superuser, ws)
    assert any(
        r["scope"] == "column" and r["raw_key"] == "debit"
        and r["canonical_key"] == "debit_amount" and r["unit_type"] == "transaction"
        for r in audit
    ), audit

    promoted = await _subentity_field_names(db_url_superuser, ws, "transaction")
    assert "debit_amount" in promoted, promoted


# ---------------------------------------------------------------------------
# Regression guard — finalize must NOT auto re-extract
# ---------------------------------------------------------------------------


async def test_finalize_corpus_does_not_auto_reextract():
    """The blanket auto re-extraction is gone; convergence (rename) replaces it
    and column convergence is wired in. Re-extraction is user-only."""
    import inspect

    from kb.workers.tasks import finalize_corpus_impl

    src = inspect.getsource(finalize_corpus_impl)
    assert "reextract_workspace_schema_entities_impl(" not in src, (
        "finalize must not auto re-extract — it does not scale to 1000s of docs"
    )
    assert "converge_workspace_columns_impl(" in src, (
        "column convergence should run at finalize"
    )


# ---------------------------------------------------------------------------
# Manual rename = display pointer (no key rewrite), resolved at query time
# ---------------------------------------------------------------------------


async def test_resolve_field_name_uses_display_map():
    """The query resolver maps a user display label → canonical stored key, so a
    manual rename is queryable without touching stored data."""
    from kb.query.mode_router import _resolve_field_name

    fields = {"closing_balance": 100}
    dmap = {"ending_balance": "closing_balance"}  # normalized display → canonical
    # Without the map the display label does NOT resolve (no token overlap).
    assert _resolve_field_name("ending balance", fields) is None
    # With the map it translates to the canonical stored key.
    assert _resolve_field_name("ending balance", fields, dmap) == "closing_balance"
    # The canonical name still resolves directly (back-compat).
    assert _resolve_field_name("closing balance", fields) == "closing_balance"


async def test_read_field_display_map(db_url_superuser):
    """read_field_display_map surfaces {display_name: canonical} for fields the
    user gave a custom label."""
    from kb.domain.fields import read_field_display_map

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await conn.execute(
            "INSERT INTO inferred_schema_fields "
            "(workspace_id, inferred_doc_type, canonical_name, display_name, value_type) "
            "VALUES (%s, 'contract', 'closing_balance', 'Ending Balance', 'number')",
            (ws,),
        )
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        m = await read_field_display_map(conn, workspace_id=ws)
    assert m.get("Ending Balance") == "closing_balance", m


async def test_fmode_resolves_renamed_display_label(db_url_superuser):
    """End-to-end: a field renamed via display label is queryable by the NEW
    name. F-mode maps the label → canonical stored key, so the predicate matches
    and the hit survives the filter (rather than degrading)."""
    from kb.query.mode_router import _route_f_mode
    from kb.query.planner import Plan
    from kb.query.rrf import Hit

    ws = str(uuid.uuid4())
    fid = await _seed_scalar_doc(db_url_superuser, workspace=ws, doc_type="contract",
                                 field_name="closing_balance", value=100)
    # User renamed the DISPLAY label to "ending_balance"; canonical key stays.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await conn.execute(
            "INSERT INTO inferred_schema_fields "
            "(workspace_id, inferred_doc_type, canonical_name, display_name, "
            " description, value_type, n_docs_observed, prevalence, stability, "
            " value_type_confidence) "
            "VALUES (%s, 'contract', 'closing_balance', 'ending_balance', "
            "        '', 'number', 1, 1.0, 1.0, 1.0)",
            (ws,),
        )
        await conn.commit()

    plan = Plan(mode="F", field_filters=(
        {"field": "ending balance", "op": "eq", "value": 100},
    ))
    hits = [Hit(id="h1", kind="chunk", score=1.0, snippet="x",
                metadata={"file_id": fid})]
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        out = await _route_f_mode(plan, hits, conn, workspace_id=ws)

    # Matched (not degraded): the surviving hit carries the applied filters.
    assert len(out) == 1, out
    assert out[0].metadata.get("field_filters"), out[0].metadata
