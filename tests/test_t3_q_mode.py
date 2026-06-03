"""T3 (§6.11) — Q-mode generous-aggregation internals.

Covers, deterministically, the §8 acceptance criteria T3 adds:

  - generous catalog: an arbitrary extracted table's numeric key aggregates
  - value_type gate (§6.11): a numeric agg over a text-only field refuses cleanly
  - row-level date filter compiled into the WHERE (§8 #12) + executes / narrows
  - grain dedup: superseded versions excluded from the aggregate
  - group-by canonicalization (HDFC / HDFC Bank → one) + cardinality cap
  - sanity all-null detection (§8 #4) — matched rows but nothing parseable
  - active stated-vs-computed reconciliation surfaced (§8 #7)

The pure-function tests pin the logic; one temp-table test proves the guarded
date row-filter actually narrows + tolerates dirty dates without aborting. The
full end-to-end firing (planner → predicate → audit envelope) is verified by the
live adversarial trace recorded in the build notes.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from kb.domain.structured_schema import FieldInfo, LiveSchema, UnitColumnInfo
from kb.q_planner import (
    QPlanValidationError,
    compile_plan,
    parse_plan,
    validate,
)
from kb.q_planner.compiler import _jsonb_extract_sql, compile_row_filters
from kb.q_planner.dynamic_catalog import _collapse, _from_live_schema
from kb.q_planner.grammar import Aggregation
from kb.q_planner.group_by import canonicalize_and_cap
from kb.query.mode_router import (
    _aggregate_all_null,
    _concept_tokens,
    _reconcile_note,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _catalog(unit_cols, fields=()):
    """Build a DynamicCatalog from synthetic unit columns + doc-root fields.
    Numeric columns get a full castable sample; text columns get a 0 fraction so
    `is_text_only` fires."""
    schema = LiveSchema(
        workspace_id="ws", epoch=1, doc_types=(), doc_counts={},
        fields=tuple(fields),
        unit_types=tuple({c.unit_type for c in unit_cols}),
        unit_columns=tuple(unit_cols),
    )
    cast = {
        (c.unit_type, c.canonical_key): (
            (c.n_rows, c.n_rows, 1.0) if c.is_numeric else (0, c.n_rows, 0.0)
        )
        for c in unit_cols
    }
    return _from_live_schema(schema, cast)


def _sum_plan(unit_type, key):
    return parse_plan({
        "from": "extracted_entities",
        "filters": [{"field": "unit_type", "op": "eq", "value": unit_type}],
        "aggregations": [
            {"op": "SUM", "field": f"fields.{key}::numeric", "alias": "t"},
        ],
    })


# ---------------------------------------------------------------------------
# Generous catalog — an arbitrary extracted table now aggregates (§8)
# ---------------------------------------------------------------------------


def test_generous_catalog_aggregates_arbitrary_unit_type():
    """A never-whitelisted unit_type with a numeric jsonb key validates +
    compiles to a SUM — the generous-aggregation goal."""
    cat = _catalog([
        UnitColumnInfo("safety_widget", "count_inr", "number", 1.0, 12, 3, True,
                       grain="unit:safety_widget"),
    ])
    plan = _sum_plan("safety_widget", "count_inr")
    validated = validate(plan, live_catalog=cat)  # must NOT raise
    sql, params = compile_plan(validated, workspace_id="ws", row_cap=10)
    assert "->>'count_inr')" in sql           # the numeric jsonb extraction
    assert "safety_widget" in params          # unit_type filter is parameterized


def test_catalog_groups_unit_type_spellings():
    """transaction_listing / transactionlisting collapse + union so an aggregate
    doesn't miss half its rows."""
    cat = _catalog([
        UnitColumnInfo("transaction_listing", "debit", "number", 1.0, 45, 4, True,
                       grain="unit:transaction_listing"),
        UnitColumnInfo("transactionlisting", "debit", "number", 1.0, 92, 6, True,
                       grain="unit:transactionlisting"),
    ])
    assert _collapse("transaction_listing") == _collapse("transactionlisting")
    variants = cat.variants_for("transactionlisting")
    assert set(variants) == {"transaction_listing", "transactionlisting"}


# ---------------------------------------------------------------------------
# value_type gate (§6.11) — text-only field refuses; numeric / unknown allowed
# ---------------------------------------------------------------------------


def test_value_type_gate_refuses_text_only_field():
    cat = _catalog([
        UnitColumnInfo("entity_detail", "value", "string", 1.0, 65, 7, False,
                       grain="unit:entity_detail"),
    ])
    with pytest.raises(QPlanValidationError):
        validate(_sum_plan("entity_detail", "value"), live_catalog=cat)


def test_value_type_gate_backcompat_without_catalog():
    """The SAME plan with NO catalog is allowed — never a NEW refusal."""
    validate(_sum_plan("entity_detail", "value"))  # must NOT raise


def test_value_type_gate_unknown_key_falls_through():
    """A key the catalog doesn't know about falls through (allowed) so a missing
    probe never blocks a query."""
    cat = _catalog([
        UnitColumnInfo("entity_detail", "value", "string", 1.0, 65, 7, False,
                       grain="unit:entity_detail"),
    ])
    validate(_sum_plan("entity_detail", "mystery_key"), live_catalog=cat)


def test_value_type_gate_allows_numeric_field():
    cat = _catalog([
        UnitColumnInfo("emi_payment", "principal_portion", "number", 1.0, 9, 3,
                       True, grain="unit:emi_payment"),
    ])
    validate(_sum_plan("emi_payment", "principal_portion"), live_catalog=cat)


def test_value_type_gate_allows_count_over_text():
    """COUNT_DISTINCT over a text field is fine — the gate only blocks NUMERIC
    aggregations."""
    cat = _catalog([
        UnitColumnInfo("entity_detail", "value", "string", 1.0, 65, 7, False,
                       grain="unit:entity_detail"),
    ])
    plan = parse_plan({
        "from": "extracted_entities",
        "filters": [{"field": "unit_type", "op": "eq", "value": "entity_detail"}],
        "aggregations": [
            {"op": "COUNT_DISTINCT", "field": "fields.value::text", "alias": "n"},
        ],
    })
    validate(plan, live_catalog=cat)  # must NOT raise


# ---------------------------------------------------------------------------
# Row-level date filter compiled into the WHERE (§8 #12)
# ---------------------------------------------------------------------------


def test_row_level_date_filter_in_where():
    plan = parse_plan({
        "from": "extracted_entities",
        "filters": [{"field": "unit_type", "op": "in",
                     "value": ["major_transaction", "transaction_listing"]}],
        "aggregations": [{"op": "MAX", "field": "fields.debit::numeric",
                          "alias": "highest"}],
    })
    validated = validate(plan)
    rfs = [{"column": "date", "op": "between",
            "value": ["2025-01-01", "2025-03-31"], "kind": "date"}]
    sql, params = compile_plan(
        validated, workspace_id="ws", row_cap=1000, row_filters=rfs,
    )
    assert "BETWEEN %s AND %s" in sql
    assert "->>'date')::date" in sql           # guarded date cast at row level
    assert params[0] == "ws"                   # workspace stays first
    assert params[-2:] == ["2025-01-01", "2025-03-31"]


def test_row_filter_backcompat_no_filters():
    plan = parse_plan({
        "from": "extracted_entities",
        "filters": [{"field": "unit_type", "op": "eq", "value": "transaction"}],
        "aggregations": [{"op": "MAX", "field": "fields.debit::numeric", "alias": "m"}],
    })
    sql, _ = compile_plan(validate(plan), workspace_id="ws", row_cap=10)
    assert "BETWEEN" not in sql


def test_row_filter_fail_open_on_unsupported_table_and_bad_column():
    assert compile_row_filters(
        "proposed_fields", [{"column": "x", "op": "gt", "value": 5}],
    ) == []
    assert compile_row_filters(
        "extracted_entities", [{"column": "a; DROP TABLE x", "op": "eq", "value": 1}],
    ) == []


# ---------------------------------------------------------------------------
# Grain dedup — superseded versions excluded (§6.11)
# ---------------------------------------------------------------------------


def test_grain_dedup_exclusion_compiles():
    plan = parse_plan({
        "from": "proposed_fields",
        "filters": [{"field": "field_name", "op": "eq", "value": "outstanding"}],
        "aggregations": [{"op": "SUM", "field": "value_numeric", "alias": "t"}],
    })
    sql, params = compile_plan(
        validate(plan), workspace_id="ws", row_cap=10,
        exclude_file_ids=["11111111-1111-1111-1111-111111111111"],
    )
    assert 'file_id" <> ALL(%s::uuid[])' in sql
    assert params[-1] == ["11111111-1111-1111-1111-111111111111"]


def test_grain_dedup_skipped_on_file_idless_table():
    plan = parse_plan({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, _ = compile_plan(
        validate(plan), workspace_id="ws", row_cap=10, exclude_file_ids=["x"],
    )
    assert "<> ALL" not in sql


# ---------------------------------------------------------------------------
# Group-by canonicalization + cardinality cap (§8)
# ---------------------------------------------------------------------------


def test_groupby_canonicalization_merges_entity_spellings():
    aggs = [Aggregation(op="SUM", field="fields.amount::numeric", alias="total")]
    rows = [("HDFC Bank", 100), ("HDFC Ltd", 50), ("HDFC", 25), ("ICICI Bank", 200)]
    _cols, merged, notes = canonicalize_and_cap(
        ["lender", "total"], rows, group_by=["lender"], aggregations=aggs,
    )
    by_key = {str(r[0]): r[1] for r in merged}
    assert any(v == 175 for v in by_key.values())  # 100 + 50 + 25
    assert any(v == 200 for v in by_key.values())
    assert any("merged" in n for n in notes)


def test_groupby_avg_merge_is_flagged_approximate():
    aggs = [Aggregation(op="AVG", field="fields.rate::numeric", alias="avg")]
    rows = [("HDFC Bank", 9.0), ("HDFC Ltd", 9.4), ("Axis", 7.0)]
    _cols, _merged, notes = canonicalize_and_cap(
        ["lender", "avg"], rows, group_by=["lender"], aggregations=aggs,
    )
    assert any("approximate" in n for n in notes)


def test_groupby_date_keys_not_mangled():
    aggs = [Aggregation(op="COUNT", field="*", alias="n")]
    rows = [("2025-03", 5), ("2025-04", 7)]
    _cols, out, notes = canonicalize_and_cap(
        ["month", "n"], rows, group_by=["month"], aggregations=aggs,
    )
    assert {r[0] for r in out} == {"2025-03", "2025-04"}
    assert notes == []


def test_groupby_cardinality_cap():
    aggs = [Aggregation(op="SUM", field="fields.x::numeric", alias="n")]
    rows = [(f"entity{i}", i) for i in range(250)]
    _cols, out, notes = canonicalize_and_cap(
        ["e", "n"], rows, group_by=["e"], aggregations=aggs, cap=200,
    )
    assert len(out) == 200
    assert any("capped to 200 of 250" in n for n in notes)


# ---------------------------------------------------------------------------
# Sanity all-null detection (§8 #4)
# ---------------------------------------------------------------------------


def test_sanity_all_null_true_when_aggregate_empty():
    # one SUM column, value NULL → all-null (the 'INR 18,400/year' case).
    assert _aggregate_all_null((), ["total"], [(None,)]) is True
    assert _aggregate_all_null((), ["total"], []) is True


def test_sanity_all_null_false_when_any_value_present():
    assert _aggregate_all_null((), ["total"], [(42,)]) is False
    # group-by: key col + agg col; a present agg → not all-null
    assert _aggregate_all_null(["k"], ["k", "n"], [("a", None), ("b", 3)]) is False


def test_sanity_count_zero_is_not_all_null():
    """A COUNT of 0 is a real value (0, not NULL) → a valid answer, never the
    'computed nothing' refusal path."""
    assert _aggregate_all_null((), ["n"], [(0,)]) is False


# ---------------------------------------------------------------------------
# Active stated-vs-computed reconciliation (§8 #7)
# ---------------------------------------------------------------------------


def test_concept_tokens_drops_stopwords():
    assert "principal" in _concept_tokens("total_outstanding_principal")
    assert "total" not in _concept_tokens("total_principal")


def test_reconcile_note_surfaces_both_on_material_gap():
    note = _reconcile_note(5_000_000.0, "stated_total_principal", 5_200_000.0, 0.01)
    assert "differs" in note.lower()
    assert "5,000,000" in note and "5,200,000" in note
    assert "do not silently pick" in note.lower()


def test_reconcile_note_consistent_within_tolerance():
    note = _reconcile_note(5_200_000.0, "stated_total", 5_200_100.0, 0.01)
    assert "consistent" in note.lower()


# ---------------------------------------------------------------------------
# Execution proof — guarded date row-filter narrows + tolerates dirty dates
# (§8 #12). Temp table → no FK chain.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_level_date_filter_executes_and_narrows(db_url_superuser):
    frags = compile_row_filters(
        "extracted_entities",
        [{"column": "date", "op": "between",
          "value": ["2025-01-01", "2025-03-31"], "kind": "date"}],
    )
    assert frags, "row filter should compile"
    where_sql, where_params = frags[0]
    amount_expr = _jsonb_extract_sql("extracted_entities", "fields", "amount", "numeric")

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "CREATE TEMP TABLE extracted_entities (fields jsonb) ON COMMIT DROP"
        )
        async with conn.transaction():
            for d, amt in [
                ("2025-02-15", 100),   # in window
                ("2025-03-20", 200),   # in window (max of window)
                ("2025-05-01", 999),   # OUT of window — must be excluded
                ("not-a-date", 5000),  # dirty — must be NULL-skipped, not abort
            ]:
                await conn.execute(
                    "INSERT INTO extracted_entities(fields) VALUES (%s::jsonb)",
                    (json.dumps({"date": d, "amount": amt}),),
                )
            cur = await conn.execute(
                f"SELECT max({amount_expr}) FROM extracted_entities "
                f"WHERE {where_sql}",
                where_params,
            )
            row = await cur.fetchone()

    # Max over the Jan–Mar window only (200); the May 999 + dirty-date 5000
    # are excluded, and the dirty date did not abort the query.
    assert row[0] == 200
