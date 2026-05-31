"""Unit tests for the FIX 1 doc_root bridge — kb.domain.fields.build_doc_root_fields.

build_doc_root_fields turns a file's proposed_field rows into the
`extracted_entities.fields` jsonb the F-mode query path filters on. The
contract that matters: numeric values land as real numbers (so
"interest_rate>9" works), text falls back to value_text, and junk rows
are dropped.

Pure — no DB, no LLM:

    uv run pytest tests/test_doc_root_fields_unit.py -q
"""

from __future__ import annotations

from kb.domain.fields import build_doc_root_fields


def _row(name, *, value_text=None, value_numeric=None):
    return {
        "field_name": name,
        "value_text": value_text,
        "value_numeric": value_numeric,
        "value_type": "text",
        "model_id": "test",
    }


class TestBuildDocRootFields:
    def test_prefers_numeric_as_real_number(self):
        # The loan's rate: stored typed so range filters compare numerically.
        fields = build_doc_root_fields(
            [_row("interest_rate", value_text="9.40%", value_numeric=9.4)]
        )
        assert fields == {"interest_rate": 9.4}
        assert isinstance(fields["interest_rate"], float)

    def test_text_fallback_when_no_numeric(self):
        fields = build_doc_root_fields(
            [_row("borrower", value_text="Acme Corp", value_numeric=None)]
        )
        assert fields == {"borrower": "Acme Corp"}

    def test_zero_numeric_is_kept(self):
        # 0.0 is falsy but a legitimate value — must not be dropped.
        fields = build_doc_root_fields(
            [_row("balance", value_text="0", value_numeric=0.0)]
        )
        assert fields == {"balance": 0.0}

    def test_skips_rows_with_no_value(self):
        fields = build_doc_root_fields(
            [
                _row("blank", value_text="", value_numeric=None),
                _row("whitespace", value_text="   ", value_numeric=None),
                _row("none", value_text=None, value_numeric=None),
                _row("real", value_text="x", value_numeric=None),
            ]
        )
        assert fields == {"real": "x"}

    def test_skips_rows_with_no_name(self):
        fields = build_doc_root_fields(
            [
                {"field_name": "", "value_text": "x", "value_numeric": None},
                {"field_name": None, "value_text": "y", "value_numeric": None},
                _row("ok", value_text="z"),
            ]
        )
        assert fields == {"ok": "z"}

    def test_last_write_wins_on_duplicate_name(self):
        # Reader orders rows; the last row for a name overwrites earlier ones
        # (e.g. a frontmatter-authoritative value landing after an LLM scalar).
        fields = build_doc_root_fields(
            [
                _row("status", value_text="draft"),
                _row("status", value_text="live"),
            ]
        )
        assert fields == {"status": "live"}

    def test_empty_input(self):
        assert build_doc_root_fields([]) == {}

    def test_decimal_numeric_is_coerced_to_float(self):
        # value_numeric is a Postgres `numeric` → psycopg returns Decimal,
        # which json.dumps can't serialize. The builder must coerce to float.
        from decimal import Decimal
        fields = build_doc_root_fields(
            [_row("interest_rate", value_text="9.40", value_numeric=Decimal("9.4"))]
        )
        assert fields == {"interest_rate": 9.4}
        assert isinstance(fields["interest_rate"], float)
        import json
        json.dumps(fields)  # must not raise

    def test_mixed_document(self):
        fields = build_doc_root_fields(
            [
                _row("interest_rate", value_text="9.4", value_numeric=9.4),
                _row("principal", value_text="₹22 lakh", value_numeric=2_200_000.0),
                _row("borrower", value_text="Acme Corp"),
                _row("ignored_blank", value_text=""),
            ]
        )
        assert fields == {
            "interest_rate": 9.4,
            "principal": 2_200_000.0,
            "borrower": "Acme Corp",
        }
