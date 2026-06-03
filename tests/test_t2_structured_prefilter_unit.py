"""T2 Phase A — structured-schema introspection + predicate resolver.

Covers:
  - kb.domain.schema_epoch (read default 0, bump increments)
  - kb.domain.structured_schema (live_schema coverage, find_field ambiguity,
    resolve_entity exact/trigram/prefix, rank_fields_for_query, epoch cache)
  - kb.query.structured_prefilter (ResolvedPredicate round-trip, date
    normalization, AND-salvage, and resolve(): field high/low-coverage,
    ambiguous field, entity-always-soft, doc_type, over-broad, date row_filter)

Seeding goes through the superuser connection (RLS-bypassing, force_rollback
per test) and explicit workspace_id, mirroring tests/test_query_channels_unit.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _ensure_doc_root_se(conn, ws: str) -> str:
    """Create (once per ws) a schema + doc_root schema_entity, return its id."""
    cur = await conn.execute(
        "INSERT INTO schemas (workspace_id, name, lifecycle_state) "
        "VALUES (%s, %s, 'active') ON CONFLICT DO NOTHING RETURNING id",
        (ws, "auto:t2:test"),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM schemas WHERE workspace_id = %s AND name = %s",
            (ws, "auto:t2:test"),
        )
        row = await cur.fetchone()
    schema_id = row[0]
    cur = await conn.execute(
        "SELECT id FROM schema_entities WHERE workspace_id = %s AND schema_id = %s "
        "AND kind = 'doc_root' LIMIT 1",
        (ws, schema_id),
    )
    row = await cur.fetchone()
    if row is not None:
        return str(row[0])
    cur = await conn.execute(
        "INSERT INTO schema_entities (schema_id, workspace_id, name, lifecycle_state, kind) "
        "VALUES (%s, %s, 'DocRoot', 'active', 'doc_root') RETURNING id",
        (schema_id, ws),
    )
    return str((await cur.fetchone())[0])


async def _seed_file(conn, ws: str, *, doc_type: str, label: str) -> tuple[str, str]:
    """Seed file → chunk → contextual_chunk; return (file_id, contextual_chunk_id)."""
    file_id = str(uuid.uuid4())
    sha = uuid.uuid4().hex + uuid.uuid4().hex[:0]  # 32 hex; unique per call
    sha = (sha * 2)[:64]
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
        "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready', %s)",
        (file_id, ws, f"{label}.pdf", sha, f"raw_files/{sha}", doc_type),
    )
    chunk_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
        "source_page_numbers, token_count, content_sha) "
        "VALUES (%s, %s, %s, 0, 'chunk text', %s, 5, %s)",
        (chunk_id, file_id, ws, [1], uuid.uuid4().hex * 2),
    )
    cc_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
        "contextual_prefix, contextual_text, model_id, prefix_token_count, "
        "cache_creation_input_tokens, cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, '', 'ctx', 'identity', 0, 0, 0)",
        (cc_id, chunk_id, file_id, ws),
    )
    return file_id, cc_id


async def _seed_doc(conn, ws: str, *, doc_type: str, fields: dict, label: str,
                    se_id: str | None = None) -> tuple[str, str]:
    """Seed a file + a doc_root extracted_entities row carrying `fields`."""
    import json
    file_id, cc_id = await _seed_file(conn, ws, doc_type=doc_type, label=label)
    if se_id is None:
        se_id = await _ensure_doc_root_se(conn, ws)
    await conn.execute(
        "INSERT INTO extracted_entities (schema_entity_id, file_id, workspace_id, "
        "fields, citations, model_id) VALUES (%s, %s, %s, %s::jsonb, '{}'::jsonb, 'mock')",
        (se_id, file_id, ws, json.dumps(fields)),
    )
    return file_id, cc_id


async def _seed_entity(conn, ws: str, *, name: str, etype: str = "ORG") -> str:
    eid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO canonical_entities (id, workspace_id, canonical_name, entity_type) "
        "VALUES (%s, %s, %s, %s)",
        (eid, ws, name, etype),
    )
    return eid


async def _link_mention(conn, ws: str, *, file_id: str, cc_id: str,
                        entity_id: str, text: str, mtype: str = "ORG") -> None:
    mid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO extracted_mentions (id, contextual_chunk_id, file_id, "
        "workspace_id, mention_text, mention_type, model_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'identity')",
        (mid, cc_id, file_id, ws, text, mtype),
    )
    await conn.execute(
        "INSERT INTO mention_to_entity (mention_id, entity_id, workspace_id, "
        "confidence, resolved_method) VALUES (%s, %s, %s, 1.0, 'identity')",
        (mid, entity_id, ws),
    )


async def _seed_inferred_field(conn, ws: str, *, doc_type: str, name: str,
                               value_type: str, display_name: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO inferred_schema_fields (workspace_id, inferred_doc_type, "
        "canonical_name, description, value_type, n_docs_observed, prevalence, "
        "stability, value_type_confidence, display_name) "
        "VALUES (%s, %s, %s, '', %s, 1, 1.0, 1.0, 1.0, %s)",
        (ws, doc_type, name, value_type, display_name),
    )


def _plan(**kw):
    base = dict(field_filters=(), seed_entities=(), doc_types=(),
                unit_types=(), date_filters=())
    base.update(kw)
    return SimpleNamespace(**base)


async def _set_ws(conn, ws: str) -> None:
    await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))


# ===========================================================================
# schema_epoch
# ===========================================================================


async def test_schema_epoch_default_zero_and_bump(db_superuser):
    from kb.domain.schema_epoch import bump_schema_epoch, read_schema_epoch
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    assert await read_schema_epoch(db_superuser, workspace_id=ws) == 0
    assert await bump_schema_epoch(db_superuser, workspace_id=ws) == 1
    assert await read_schema_epoch(db_superuser, workspace_id=ws) == 1
    assert await bump_schema_epoch(db_superuser, workspace_id=ws) == 2
    assert await read_schema_epoch(db_superuser, workspace_id=ws) == 2


# ===========================================================================
# Date normalization (pure — no DB)
# ===========================================================================


async def test_date_normalization_absolute_relative_fiscal():
    from kb.query.structured_prefilter import normalize_date_expression as nd
    now = datetime(2026, 5, 15)
    assert nd("March 2026", now=now).to_list() == ["2026-03-01", "2026-03-31"]
    assert nd("2026", now=now).to_list() == ["2026-01-01", "2026-12-31"]
    assert nd("2026-02", now=now).to_list() == ["2026-02-01", "2026-02-28"]  # non-leap
    assert nd("last quarter", now=now).to_list() == ["2026-01-01", "2026-03-31"]
    assert nd("this year", now=now).to_list() == ["2026-01-01", "2026-12-31"]
    assert nd("last year", now=now).to_list() == ["2025-01-01", "2025-12-31"]
    assert nd("Q3 2026", now=now).to_list() == ["2026-07-01", "2026-09-30"]
    # Fiscal year starting in April: FY2026 = 2026-04-01 .. 2027-03-31.
    fy = nd("FY2026", now=now, fiscal_year_start_month=4)
    assert fy.to_list() == ["2026-04-01", "2027-03-31"]
    assert nd("not a date", now=now) is None


# ===========================================================================
# ResolvedPredicate / Clause / RowFilter round-trip (pure)
# ===========================================================================


async def test_resolved_predicate_roundtrip():
    from kb.query.structured_prefilter import (
        Clause, ResolvedPredicate, RowFilter,
    )
    p = ResolvedPredicate(
        clauses=(Clause(kind="field", canonical_key="total", op="gt", value=1000,
                        file_ids=("a", "b"), coverage=1.0, confidence=0.9),),
        file_scope=frozenset({"a", "b"}),
        row_filters=(RowFilter(column="txn_date", op="between",
                               value=["2026-01-01", "2026-03-31"], kind="date"),),
        overall_confidence=0.9, selectivity=0.5, total_docs=4,
        dropped_clauses=("entity:foo",), notes=("and_salvage",),
    )
    d = p.to_dict()
    p2 = ResolvedPredicate.from_dict(d)
    assert p2.file_scope == frozenset({"a", "b"})
    assert p2.overall_confidence == 0.9
    assert p2.row_filters[0].value == ["2026-01-01", "2026-03-31"]
    assert p2.clauses[0].canonical_key == "total"
    assert p2.dropped_clauses == ("entity:foo",)
    # ALL round-trips as None / null.
    all_d = ResolvedPredicate().to_dict()
    assert all_d["file_scope"] is None
    assert ResolvedPredicate.from_dict(all_d).is_all


# ===========================================================================
# structured_schema — live_schema
# ===========================================================================


async def test_live_schema_coverage_and_value_type(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    # 3 invoices: all have `total` (numeric); 2 of 3 have `gst_number` +
    # `invoice_date` (a string value declared as a `date` field).
    await _seed_doc(db_superuser, ws, doc_type="invoice",
                    fields={"total": 5000, "gst_number": "22AAA",
                            "invoice_date": "2026-03-01"}, label="i1")
    await _seed_doc(db_superuser, ws, doc_type="invoice",
                    fields={"total": 200, "gst_number": "33BBB",
                            "invoice_date": "2026-03-02"}, label="i2")
    await _seed_doc(db_superuser, ws, doc_type="invoice",
                    fields={"total": 9000}, label="i3")
    await _seed_inferred_field(db_superuser, ws, doc_type="invoice",
                               name="total", value_type="number")
    await _seed_inferred_field(db_superuser, ws, doc_type="invoice",
                               name="invoice_date", value_type="date")

    schema = await live_schema(db_superuser, workspace_id=ws)
    assert schema.doc_count("invoice") == 3
    by_key = {f.canonical_key: f for f in schema.fields_for("invoice")}
    assert by_key["total"].coverage == pytest.approx(1.0)
    assert by_key["total"].is_numeric is True
    assert by_key["total"].value_type == "number"
    # Declared-type enrichment: the value is a string, but the inferred-schema
    # row declares it a date — the LEFT JOIN carries that through (a bare probe
    # would have labelled it 'string').
    assert by_key["invoice_date"].value_type == "date"
    assert by_key["invoice_date"].coverage == pytest.approx(2 / 3)
    assert by_key["gst_number"].coverage == pytest.approx(2 / 3)
    assert by_key["gst_number"].value_type == "string"   # no inferred row → probe
    assert by_key["gst_number"].is_numeric is False


async def test_find_field_resolves_and_flags_ambiguity(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_doc(db_superuser, ws, doc_type="loan",
                    fields={"interest_rate": 9.5}, label="l1")
    schema = await live_schema(db_superuser, workspace_id=ws)
    # token-subset: "rate" → interest_rate (unambiguous here).
    got = schema.find_field("rate")
    assert {f.canonical_key for f in got} == {"interest_rate"}
    # exact + normalized
    assert schema.find_field("Interest Rate")[0].canonical_key == "interest_rate"

    # Add a second *_rate key in another doc_type → "rate" now ambiguous.
    await _seed_doc(db_superuser, ws, doc_type="bond",
                    fields={"coupon_rate": 4.0}, label="b1")
    clear_cache()
    schema = await live_schema(db_superuser, workspace_id=ws)
    amb = schema.find_field("rate")
    assert {f.canonical_key for f in amb} == {"interest_rate", "coupon_rate"}


async def test_resolve_entity_exact_trigram_prefix(db_superuser):
    from kb.domain.structured_schema import resolve_entity
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_entity(db_superuser, ws, name="Acme Corporation")
    # exact (case-insensitive)
    exact = await resolve_entity(db_superuser, workspace_id=ws,
                                 name="acme corporation", floor=0.45)
    assert len(exact) == 1 and exact[0].score == 1.0 and exact[0].method == "exact"
    # trigram on a near-miss
    fuzzy = await resolve_entity(db_superuser, workspace_id=ws,
                                 name="Acme Corporatn", floor=0.3)
    assert any(m.canonical_name == "Acme Corporation" for m in fuzzy)
    # prefix catches even when similarity is below the floor
    pref = await resolve_entity(db_superuser, workspace_id=ws,
                                name="Acme", floor=0.99)
    assert any(m.method == "prefix" for m in pref)


async def test_rank_fields_for_query_is_relevance_ranked(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema, rank_fields_for_query
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_doc(db_superuser, ws, doc_type="loan",
                    fields={"interest_rate": 9.5, "borrower_name": "X",
                            "principal_amount": 100000}, label="l1")
    schema = await live_schema(db_superuser, workspace_id=ws)
    ranked = rank_fields_for_query(schema, "what is the interest rate", top_k=3)
    assert ranked[0].canonical_key == "interest_rate"


async def test_live_schema_epoch_cache_invalidation(db_superuser):
    from kb.domain.schema_epoch import bump_schema_epoch
    from kb.domain.structured_schema import clear_cache, live_schema
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_doc(db_superuser, ws, doc_type="invoice",
                    fields={"total": 1}, label="i1")
    s1 = await live_schema(db_superuser, workspace_id=ws)
    assert s1.doc_count("invoice") == 1

    # Add a doc but DON'T bump → cache hit returns the SAME (stale) snapshot.
    await _seed_doc(db_superuser, ws, doc_type="invoice",
                    fields={"total": 2}, label="i2")
    s2 = await live_schema(db_superuser, workspace_id=ws)
    assert s2 is s1                       # served from cache (epoch unchanged)
    assert s2.doc_count("invoice") == 1

    # Bump the epoch → next call rebuilds and sees both docs.
    await bump_schema_epoch(db_superuser, workspace_id=ws)
    s3 = await live_schema(db_superuser, workspace_id=ws)
    assert s3 is not s1
    assert s3.epoch == 1
    assert s3.doc_count("invoice") == 2


# ===========================================================================
# structured_prefilter — resolve()
# ===========================================================================


async def test_resolve_high_coverage_field_is_hard_scope(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    # 4 invoices, all have `total`; 2 exceed 1000.
    f_hi1, _ = await _seed_doc(db_superuser, ws, doc_type="invoice",
                               fields={"total": 5000}, label="h1")
    f_hi2, _ = await _seed_doc(db_superuser, ws, doc_type="invoice",
                               fields={"total": 9000}, label="h2")
    await _seed_doc(db_superuser, ws, doc_type="invoice", fields={"total": 100}, label="l1")
    await _seed_doc(db_superuser, ws, doc_type="invoice", fields={"total": 200}, label="l2")

    pred = await resolve(
        db_superuser, workspace_id=ws,
        plan=_plan(field_filters=({"field": "total", "op": "gt", "value": 1000},)),
    )
    assert pred.file_scope == frozenset({f_hi1, f_hi2})
    assert pred.overall_confidence >= 0.75
    assert pred.is_hard() is True
    assert pred.selectivity == pytest.approx(0.5)


async def test_resolve_low_coverage_field_is_soft_scope(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    # 4 invoices, only 1 has gst_number → coverage 0.25.
    f_gst, _ = await _seed_doc(db_superuser, ws, doc_type="invoice",
                               fields={"total": 1, "gst_number": "22AAA"}, label="g1")
    for i in range(3):
        await _seed_doc(db_superuser, ws, doc_type="invoice",
                        fields={"total": 1}, label=f"n{i}")

    pred = await resolve(
        db_superuser, workspace_id=ws,
        plan=_plan(field_filters=({"field": "gst_number", "op": "eq", "value": "22AAA"},)),
    )
    assert pred.file_scope == frozenset({f_gst})       # still narrows...
    assert pred.overall_confidence < 0.75              # ...but softly (P1)
    assert pred.is_hard() is False


async def test_resolve_ambiguous_field_not_autopicked(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_doc(db_superuser, ws, doc_type="loan",
                    fields={"interest_rate": 9.5}, label="l1")
    await _seed_doc(db_superuser, ws, doc_type="bond",
                    fields={"coupon_rate": 4.0}, label="b1")
    pred = await resolve(
        db_superuser, workspace_id=ws,
        plan=_plan(field_filters=({"field": "rate", "op": "gt", "value": 5},)),
    )
    field_clause = next(c for c in pred.clauses if c.kind == "field")
    assert field_clause.ambiguous is True
    assert field_clause.canonical_key is None
    # Ambiguous clause must not produce a hard scope.
    assert pred.is_hard() is False


async def test_resolve_entity_clause_is_always_soft(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    f1, cc1 = await _seed_doc(db_superuser, ws, doc_type="invoice",
                              fields={"total": 1}, label="e1")
    # Several other docs that do NOT mention Globex, so the entity scope is a
    # genuine narrowing (1/4), not 100% of the corpus (which would read as
    # over-broad → ALL).
    for i in range(3):
        await _seed_doc(db_superuser, ws, doc_type="invoice",
                        fields={"total": 1}, label=f"other{i}")
    eid = await _seed_entity(db_superuser, ws, name="Globex")
    await _link_mention(db_superuser, ws, file_id=f1, cc_id=cc1,
                        entity_id=eid, text="Globex")
    pred = await resolve(
        db_superuser, workspace_id=ws, plan=_plan(seed_entities=("Globex",)),
    )
    ent = next(c for c in pred.clauses if c.kind == "entity")
    assert pred.file_scope == frozenset({f1})
    assert ent.confidence <= 0.70                      # capped (entity always low)
    assert pred.is_hard() is False                     # → soft scope only


async def test_resolve_and_salvage_keeps_larger_scope(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    # entity Globex on f1,f2 (both total<=1000); a contract f3 has total>1000.
    f1, cc1 = await _seed_doc(db_superuser, ws, doc_type="invoice",
                              fields={"total": 500}, label="s1")
    f2, cc2 = await _seed_doc(db_superuser, ws, doc_type="invoice",
                              fields={"total": 600}, label="s2")
    f3, _ = await _seed_doc(db_superuser, ws, doc_type="contract",
                            fields={"total": 5000}, label="s3")
    eid = await _seed_entity(db_superuser, ws, name="Globex")
    await _link_mention(db_superuser, ws, file_id=f1, cc_id=cc1, entity_id=eid, text="Globex")
    await _link_mention(db_superuser, ws, file_id=f2, cc_id=cc2, entity_id=eid, text="Globex")

    # entity → {f1,f2}; field total>1000 → {f3}; AND empty → salvage to {f1,f2}.
    pred = await resolve(
        db_superuser, workspace_id=ws,
        plan=_plan(seed_entities=("Globex",),
                   field_filters=({"field": "total", "op": "gt", "value": 1000},)),
    )
    assert pred.file_scope == frozenset({f1, f2})
    assert any(d.startswith("field:") for d in pred.dropped_clauses)
    assert "and_salvage" in pred.notes


async def test_resolve_doctype_clause(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    inv = [await _seed_doc(db_superuser, ws, doc_type="invoice",
                           fields={"total": 1}, label=f"d{i}") for i in range(3)]
    await _seed_doc(db_superuser, ws, doc_type="contract", fields={}, label="c1")
    await _seed_doc(db_superuser, ws, doc_type="contract", fields={}, label="c2")
    pred = await resolve(
        db_superuser, workspace_id=ws, plan=_plan(doc_types=("invoice",)),
    )
    assert pred.file_scope == frozenset(f for f, _ in inv)
    assert pred.is_hard() is True                      # doc_type is reliable
    assert pred.selectivity == pytest.approx(0.6)


async def test_resolve_overbroad_predicate_falls_back_to_all(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    for i in range(5):
        await _seed_doc(db_superuser, ws, doc_type="invoice",
                        fields={"status": "paid"}, label=f"p{i}")
    pred = await resolve(
        db_superuser, workspace_id=ws,
        plan=_plan(field_filters=({"field": "status", "op": "eq", "value": "paid"},)),
    )
    # Matches all 5/5 → over-broad → no narrowing (ALL); plain RAG reachable.
    assert pred.is_all is True
    assert any(n.startswith("overbroad") for n in pred.notes)


async def test_resolve_date_filter_becomes_row_filter(db_superuser):
    from kb.domain.structured_schema import clear_cache
    from kb.query.structured_prefilter import resolve
    clear_cache()
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_doc(db_superuser, ws, doc_type="statement",
                    fields={"total": 1}, label="st1")
    pred = await resolve(
        db_superuser, workspace_id=ws,
        plan=_plan(date_filters=({"field": "txn_date", "expr": "last quarter",
                                  "grain": "unit:transaction"},)),
        now=datetime(2026, 5, 15),
    )
    assert len(pred.row_filters) == 1
    rf = pred.row_filters[0]
    assert rf.kind == "date" and rf.op == "between"
    assert rf.value == ["2026-01-01", "2026-03-31"]
    assert rf.column == "txn_date"
    # A date clause does not narrow the doc scope by itself.
    assert pred.is_all is True


async def test_resolve_no_constraints_is_all(db_superuser):
    from kb.query.structured_prefilter import resolve
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    pred = await resolve(db_superuser, workspace_id=ws, plan=_plan())
    assert pred.is_all is True
    assert pred.is_hard() is False
