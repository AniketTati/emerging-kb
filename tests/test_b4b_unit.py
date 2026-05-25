"""B4b — Q-mode unit tests.

Heavy security focus. Each defense layer of Design 1 has its own block of
tests. Adversarial inputs (SQL injection attempts, nested objects, unknown
identifiers) are exercised explicitly — a regression on any of these would
be a vulnerability, not just a bug.

Layers covered here (pure-function — no DB):

  1. Catalog whitelist            → validator
  2. Operator enum                → grammar.parse_plan
  3. Aggregation enum             → grammar.parse_plan
  4. Set-op enum                  → grammar.parse_plan (rejects until B-future)
  5. Parameters only              → compiler emits %s placeholders only
  6. No raw-SQL escape hatch      → grammar refuses nested objects
  9. Row cap clamping             → compiler clamps LIMIT

Layers 7 + 8 + 10 are tested end-to-end in test_b4b_api.py (need DB).
"""

from __future__ import annotations

import pytest

from kb.q_planner import (
    ALLOWED_AGGREGATIONS,
    ALLOWED_OPERATORS,
    ALLOWED_SET_OPS,
    QPlanValidationError,
    compile_plan,
    parse_plan,
    validate,
)
from kb.q_planner.artifact import build_artifact_key, rows_to_csv_bytes
from kb.q_planner.catalog import (
    ALLOWED_COLUMNS,
    ALLOWED_TABLES,
    closest_columns,
    column_type,
    is_comparable,
    is_numeric,
)
from kb.q_planner.grammar import QPlanParseError


# ===========================================================================
# Catalog — sanity
# ===========================================================================


def test_catalog_has_seven_tables():
    assert "files" in ALLOWED_TABLES
    assert "extracted_entities" in ALLOWED_TABLES
    assert "atomic_units" in ALLOWED_TABLES
    assert "doc_chains" in ALLOWED_TABLES
    # Tables we do NOT want exposed (workspace settings, raw blobs, etc.)
    assert "users" not in ALLOWED_TABLES
    assert "kb_app" not in ALLOWED_TABLES


def test_column_type_lookup():
    assert column_type("files", "source_authority") == "numeric"
    assert column_type("files", "doc_status") == "text"
    assert column_type("files", "id") == "uuid"
    assert column_type("files", "nonexistent_field") is None
    assert column_type("nonexistent_table", "id") is None


def test_is_numeric_predicate():
    assert is_numeric("files", "source_authority") is True
    assert is_numeric("files", "size_bytes") is True
    assert is_numeric("files", "doc_status") is False
    assert is_numeric("files", "id") is False


def test_is_comparable_predicate():
    assert is_comparable("files", "created_at") is True   # timestamp
    assert is_comparable("files", "doc_status") is True   # text is comparable
    assert is_comparable("files", "source_authority") is True
    assert is_comparable("files", "id") is False           # uuid


def test_closest_columns_suggests_neighbors():
    out = closest_columns("files", "name")
    assert "name" in out
    out2 = closest_columns("files", "doc_type")
    assert "inferred_doc_type" in out2


# ===========================================================================
# Layer 2 — operator enum
# ===========================================================================


def test_grammar_accepts_each_allowed_operator():
    for op in ALLOWED_OPERATORS:
        value: list | str | None = "x"
        if op == "between":
            value = [1, 2]
        elif op in ("in", "not_in"):
            value = ["x"]
        elif op in ("is_null", "is_not_null"):
            value = None
        plan_dict = {
            "from": "files",
            "filters": [
                {"field": "doc_status", "op": op, "value": value},
            ],
            "aggregations": [
                {"op": "COUNT", "field": "*", "alias": "n"},
            ],
        }
        plan = parse_plan(plan_dict)
        assert plan.filters[0].op == op


def test_grammar_rejects_unknown_operator():
    with pytest.raises(QPlanParseError, match="not allowed"):
        parse_plan({
            "from": "files",
            "filters": [{"field": "x", "op": "exists_when", "value": "y"}],
            "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        })


def test_grammar_rejects_uppercase_drop_as_operator():
    """Defense vs. an LLM emitting SQL keywords in op slot."""
    with pytest.raises(QPlanParseError, match="not allowed"):
        parse_plan({
            "from": "files",
            "filters": [{"field": "x", "op": "DROP TABLE files", "value": "y"}],
            "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        })


# ===========================================================================
# Layer 3 — aggregation enum
# ===========================================================================


def test_grammar_accepts_each_allowed_aggregation():
    for agg in ALLOWED_AGGREGATIONS:
        # COUNT can take *, others can't.
        field_name = "size_bytes" if agg in ("SUM", "AVG") else "id"
        if agg == "COUNT":
            field_name = "*"
        plan_dict = {
            "from": "files",
            "aggregations": [{"op": agg, "field": field_name, "alias": "out"}],
        }
        plan = parse_plan(plan_dict)
        assert plan.aggregations[0].op == agg


def test_grammar_rejects_unknown_aggregation():
    with pytest.raises(QPlanParseError, match="not allowed"):
        parse_plan({
            "from": "files",
            "aggregations": [{"op": "BLOWUP", "field": "id", "alias": "x"}],
        })


def test_grammar_rejects_star_with_non_count_aggregation():
    with pytest.raises(QPlanParseError, match=r"only allowed with COUNT"):
        parse_plan({
            "from": "files",
            "aggregations": [{"op": "SUM", "field": "*", "alias": "x"}],
        })


# ===========================================================================
# Layer 4 — set-op enum (refused until forward wave)
# ===========================================================================


def test_grammar_rejects_set_op():
    for op in ALLOWED_SET_OPS:
        with pytest.raises(QPlanParseError, match="not implemented in Wave A"):
            parse_plan({
                "from": "files",
                "set_op": op,
                "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
            })


def test_grammar_rejects_unknown_set_op_keyword():
    with pytest.raises(QPlanParseError, match="not allowed"):
        parse_plan({
            "from": "files",
            "set_op": "DROP DATABASE",
            "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        })


def test_grammar_rejects_joins_field():
    with pytest.raises(QPlanParseError, match="joins are not implemented"):
        parse_plan({
            "from": "files",
            "joins": [{"table": "users", "on": {}}],
            "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        })


# ===========================================================================
# Layer 6 — no raw-SQL escape hatch
# ===========================================================================


def test_grammar_rejects_nested_dict_as_value():
    """A nested object in a filter value would be a vector for hiding raw
    SQL. We refuse before it reaches the compiler."""
    with pytest.raises(QPlanParseError, match="primitive"):
        parse_plan({
            "from": "files",
            "filters": [{
                "field": "name",
                "op": "eq",
                "value": {"$raw": "OR 1=1"},
            }],
            "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        })


def test_grammar_rejects_value_with_semicolon_passes_only_as_text():
    """Semicolons in string values are PRESERVED as data — they ride
    through to the parameter, not the SQL string. The grammar should NOT
    reject them. (Layer 5 makes them safe regardless.)"""
    plan = parse_plan({
        "from": "files",
        "filters": [{
            "field": "name",
            "op": "eq",
            "value": "'; DROP TABLE files; --",
        }],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    assert plan.filters[0].value == "'; DROP TABLE files; --"


def test_grammar_rejects_field_with_special_chars():
    """Column identifiers must match a strict regex — no quotes, parens,
    semicolons, etc. The grammar refuses the plan immediately."""
    for bad_field in (
        "files.name",          # dotted
        "name; --",            # injection attempt
        "name)",               # unbalanced
        '"name"',              # quoted
        "name OR 1=1",         # spaces
        "",                    # empty
    ):
        with pytest.raises(QPlanParseError):
            parse_plan({
                "from": "files",
                "filters": [{"field": bad_field, "op": "eq", "value": "x"}],
                "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
            })


def test_grammar_rejects_alias_with_special_chars():
    for bad_alias in ("n; DROP", "n.evil", "1n", ""):
        with pytest.raises(QPlanParseError):
            parse_plan({
                "from": "files",
                "aggregations": [{"op": "COUNT", "field": "*", "alias": bad_alias}],
            })


def test_grammar_rejects_filter_list_overflow():
    """Hard cap on filter count to defend against pathological plans."""
    plan_dict = {
        "from": "files",
        "filters": [
            {"field": "doc_status", "op": "eq", "value": "live"}
            for _ in range(21)
        ],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    }
    with pytest.raises(QPlanParseError, match="filters list exceeds"):
        parse_plan(plan_dict)


def test_grammar_rejects_in_list_overflow():
    plan_dict = {
        "from": "files",
        "filters": [{
            "field": "id", "op": "in",
            "value": [f"value-{i}" for i in range(101)],
        }],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    }
    with pytest.raises(QPlanParseError, match="exceeds max length"):
        parse_plan(plan_dict)


# ===========================================================================
# Layer 1 — catalog whitelist
# ===========================================================================


def test_validator_rejects_unknown_column():
    plan = parse_plan({
        "from": "files",
        "filters": [{"field": "ssn", "op": "eq", "value": "x"}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    with pytest.raises(QPlanValidationError) as ei:
        validate(plan)
    # Must include "did you mean" suggestions.
    assert ei.value.suggestions


def test_validator_rejects_unknown_table():
    plan = parse_plan({
        "from": "users",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    with pytest.raises(QPlanValidationError, match="not in the Q-mode allowlist"):
        validate(plan)


def test_validator_rejects_sum_on_text_column():
    plan = parse_plan({
        "from": "files",
        "aggregations": [{"op": "SUM", "field": "doc_status", "alias": "x"}],
    })
    with pytest.raises(QPlanValidationError, match="requires a numeric column"):
        validate(plan)


def test_validator_rejects_avg_on_uuid_column():
    plan = parse_plan({
        "from": "files",
        "aggregations": [{"op": "AVG", "field": "id", "alias": "x"}],
    })
    with pytest.raises(QPlanValidationError, match="requires a numeric column"):
        validate(plan)


def test_validator_rejects_like_on_numeric():
    plan = parse_plan({
        "from": "files",
        "filters": [{"field": "source_authority", "op": "like", "value": "0.5%"}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    with pytest.raises(QPlanValidationError, match=r"'like' requires a text column"):
        validate(plan)


def test_validator_rejects_between_on_uuid():
    plan = parse_plan({
        "from": "files",
        "filters": [{"field": "id", "op": "between", "value": ["a", "b"]}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    with pytest.raises(QPlanValidationError, match="comparable"):
        validate(plan)


def test_validator_rejects_workspace_id_filter():
    """workspace scoping is enforced by the compiler — user can't override."""
    plan = parse_plan({
        "from": "files",
        "filters": [{"field": "workspace_id", "op": "eq", "value": "other-ws"}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    with pytest.raises(QPlanValidationError, match="workspace_id directly"):
        validate(plan)


def test_validator_rejects_empty_plan():
    plan = parse_plan({"from": "files"})
    with pytest.raises(QPlanValidationError, match="aggregations or group_by"):
        validate(plan)


def test_validator_rejects_duplicate_alias():
    plan = parse_plan({
        "from": "files",
        "aggregations": [
            {"op": "COUNT", "field": "*", "alias": "x"},
            {"op": "SUM", "field": "size_bytes", "alias": "x"},
        ],
    })
    with pytest.raises(QPlanValidationError, match="duplicate"):
        validate(plan)


def test_validator_accepts_valid_count_plan():
    plan = parse_plan({
        "from": "files",
        "filters": [{"field": "doc_status", "op": "eq", "value": "live"}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    validated = validate(plan)
    assert ("files", "doc_status") in validated.column_types


def test_validator_accepts_group_by_query():
    plan = parse_plan({
        "from": "files",
        "group_by": ["doc_status"],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    validated = validate(plan)
    assert validated.plan.group_by == ("doc_status",)


# ===========================================================================
# Layer 5 + compiler — parameter-only values
# ===========================================================================


def _validated(plan_dict):
    return validate(parse_plan(plan_dict))


def test_compiler_emits_workspace_id_as_first_param():
    validated = _validated({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, params = compile_plan(validated, workspace_id="ws-1", row_cap=100)
    assert params[0] == "ws-1"
    assert "%s" in sql
    assert "workspace_id" in sql


def test_compiler_uses_placeholders_for_user_values():
    """Even values containing SQL keywords / semicolons travel via $N."""
    validated = _validated({
        "from": "files",
        "filters": [
            {"field": "name", "op": "eq", "value": "'; DROP TABLE files; --"},
            {"field": "doc_status", "op": "in", "value": ["live", "superseded"]},
        ],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, params = compile_plan(validated, workspace_id="ws", row_cap=100)
    # The injection string lives in params, NOT in the SQL string.
    assert "'; DROP TABLE files; --" not in sql
    assert "'; DROP TABLE files; --" in params
    # All in-clause values land as params.
    assert "live" in params
    assert "superseded" in params
    # Count of %s placeholders = 1 (ws) + 1 (name eq) + 2 (in list) = 4
    assert sql.count("%s") == 4


def test_compiler_quotes_all_identifiers():
    """Every table and column identifier in the emitted SQL is wrapped in
    double quotes — even though the grammar already restricts them to a
    safe regex. Defense in depth."""
    validated = _validated({
        "from": "files",
        "group_by": ["doc_status"],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert '"files"' in sql
    assert '"doc_status"' in sql
    assert '"n"' in sql


def test_compiler_emits_count_star_correctly():
    validated = _validated({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "COUNT(*)" in sql


def test_compiler_emits_count_distinct():
    validated = _validated({
        "from": "files",
        "aggregations": [{"op": "COUNT_DISTINCT", "field": "id", "alias": "n"}],
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "COUNT(DISTINCT" in sql


def test_compiler_emits_group_by_clause():
    validated = _validated({
        "from": "files",
        "group_by": ["doc_status"],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "GROUP BY" in sql


def test_compiler_emits_order_by_alias():
    validated = _validated({
        "from": "files",
        "group_by": ["doc_status"],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        "order_by": [{"field": "n", "direction": "desc"}],
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert 'ORDER BY "n" DESC' in sql


def test_compiler_clamps_limit_to_row_cap():
    """Layer 9 — user-requested limit is clamped to row_cap."""
    validated = _validated({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        "limit": 9999,
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "LIMIT 100" in sql
    assert "LIMIT 9999" not in sql


def test_compiler_clamps_limit_to_at_least_one():
    validated = _validated({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        "limit": 1,
    })
    sql, _ = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "LIMIT 1" in sql


def test_compiler_in_clause_uses_correct_placeholder_count():
    validated = _validated({
        "from": "files",
        "filters": [{
            "field": "doc_status", "op": "in",
            "value": ["live", "draft", "superseded"],
        }],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, params = compile_plan(validated, workspace_id="ws", row_cap=100)
    # 1 ws + 3 in values
    assert sql.count("%s") == 4
    assert params == ["ws", "live", "draft", "superseded"]


def test_compiler_between_uses_two_placeholders():
    validated = _validated({
        "from": "files",
        "filters": [{
            "field": "source_authority", "op": "between",
            "value": [0.3, 0.7],
        }],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, params = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "BETWEEN %s AND %s" in sql
    assert params == ["ws", 0.3, 0.7]


def test_compiler_is_null_uses_zero_placeholders():
    validated = _validated({
        "from": "files",
        "filters": [{"field": "source_authority", "op": "is_null"}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    sql, params = compile_plan(validated, workspace_id="ws", row_cap=100)
    assert "IS NULL" in sql
    # Only workspace_id is bound.
    assert params == ["ws"]


# ===========================================================================
# Artifact helpers
# ===========================================================================


def test_rows_to_csv_bytes_round_trip():
    cols = ["doc_status", "n"]
    rows = [("live", 12), ("superseded", 3)]
    payload = rows_to_csv_bytes(cols, rows)
    text = payload.decode("utf-8")
    assert text.splitlines()[0] == "doc_status,n"
    assert "live,12" in text
    assert "superseded,3" in text


def test_rows_to_csv_bytes_escapes_commas_in_values():
    cols = ["title"]
    rows = [("ACME, Inc.",)]
    text = rows_to_csv_bytes(cols, rows).decode("utf-8")
    assert '"ACME, Inc."' in text


def test_rows_to_csv_bytes_handles_none():
    cols = ["x", "y"]
    rows = [(None, 1)]
    text = rows_to_csv_bytes(cols, rows).decode("utf-8")
    assert ",1" in text


def test_build_artifact_key_deterministic():
    k = build_artifact_key("ws-1", "aq-2")
    assert k == "q_mode_artifacts/ws-1/aq-2.csv"
