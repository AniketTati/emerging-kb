"""Phase 5c — KV+Tables extractor unit tests (no DB, no real LLM)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest

from kb.extraction.kv_tables import (
    CARDINALITIES,
    GeminiKVTablesExtractor,
    IdentityKVTablesExtractor,
    KVTablesExtractionError,
    KVTablesPayload,
    VALUE_TYPES,
    _parse_payload,
    _truncate_doc,
    make_kv_tables_extractor,
)


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ===========================================================================
# Parser
# ===========================================================================


def test_parse_payload_happy_path():
    raw = json.dumps({
        "doc_type": "bank_statement",
        "scalars": [
            {
                "name": "account_holder",
                "description": "Primary account holder",
                "value": "John Doe",
                "value_type": "text",
                "is_pii": True,
                "source_chunk": 0,
            },
            {
                "name": "statement_period_end",
                "value": "2024-01-31",
                "value_type": "date",
                "source_chunk": 0,
            },
        ],
        "tables": [
            {
                "name": "transactions",
                "description": "Account transactions",
                "cardinality": "many",
                "columns": [
                    {"name": "date", "value_type": "date"},
                    {"name": "description", "value_type": "text"},
                    {"name": "debit", "value_type": "number"},
                    {"name": "credit", "value_type": "number"},
                ],
                "rows": [
                    {
                        "values": {
                            "date": "2024-01-15",
                            "description": "Coffee",
                            "debit": "4.50",
                            "credit": None,
                        },
                        "source_chunk": 2,
                    },
                    {
                        "values": {
                            "date": "2024-01-16",
                            "description": "Salary",
                            "debit": None,
                            "credit": "5000.00",
                        },
                        "source_chunk": 3,
                    },
                ],
            }
        ],
    })

    doc_type, scalars, tables = _parse_payload(raw)
    assert doc_type == "bank_statement"
    assert len(scalars) == 2
    assert scalars[0].name == "account_holder"
    assert scalars[0].is_pii is True
    assert scalars[0].source_chunk == 0
    assert scalars[1].value_type == "date"

    assert len(tables) == 1
    txn = tables[0]
    assert txn.name == "transactions"
    assert txn.cardinality == "many"
    assert len(txn.columns) == 4
    assert {c.name for c in txn.columns} == {"date", "description", "debit", "credit"}
    assert len(txn.rows) == 2
    assert txn.rows[0].values["description"] == "Coffee"
    assert txn.rows[0].source_chunk == 2
    assert txn.rows[1].values["credit"] == "5000.00"


def test_parse_payload_strips_code_fences():
    raw = "```json\n" + json.dumps({
        "doc_type": "invoice",
        "scalars": [],
        "tables": [],
    }) + "\n```"
    doc_type, scalars, tables = _parse_payload(raw)
    assert doc_type == "invoice"
    assert scalars == []
    assert tables == []


def test_parse_payload_normalizes_doc_type_to_snake_case():
    raw = json.dumps({
        "doc_type": "Bank Statement",
        "scalars": [],
        "tables": [],
    })
    doc_type, _, _ = _parse_payload(raw)
    assert doc_type == "bank_statement"


def test_parse_payload_drops_invalid_value_types():
    raw = json.dumps({
        "doc_type": "x",
        "scalars": [
            {"name": "f1", "value": "v", "value_type": "ufo"},  # invalid → text
            {"name": "f2", "value": "v", "value_type": "number"},
        ],
        "tables": [
            {
                "name": "t",
                "cardinality": "marsupial",  # invalid → many
                "columns": [
                    {"name": "c1", "value_type": "alien"},  # invalid → text
                    {"name": "c2", "value_type": "boolean"},
                ],
                "rows": [{"values": {"c1": "a", "c2": "true"}, "source_chunk": 0}],
            }
        ],
    })
    _, scalars, tables = _parse_payload(raw)
    assert scalars[0].value_type == "text"
    assert scalars[1].value_type == "number"
    assert tables[0].cardinality == "many"
    assert tables[0].columns[0].value_type == "text"
    assert tables[0].columns[1].value_type == "boolean"


def test_parse_payload_snake_cases_field_and_column_names():
    raw = json.dumps({
        "doc_type": "resume",
        "scalars": [{"name": "Candidate Name", "value": "Alice"}],
        "tables": [{
            "name": "Work Experiences",
            "columns": [{"name": "Job Title", "value_type": "text"}],
            "rows": [{"values": {"Job Title": "Engineer"}, "source_chunk": 1}],
        }],
    })
    _, scalars, tables = _parse_payload(raw)
    assert scalars[0].name == "candidate_name"
    assert tables[0].name == "work_experiences"
    assert tables[0].columns[0].name == "job_title"
    assert tables[0].rows[0].values == {"job_title": "Engineer"}


def test_parse_payload_drops_row_values_not_in_declared_columns():
    """If the LLM declares columns, rows are filtered to those columns."""
    raw = json.dumps({
        "doc_type": "x",
        "scalars": [],
        "tables": [{
            "name": "t",
            "columns": [{"name": "a", "value_type": "text"}],
            "rows": [{"values": {"a": "1", "b": "2"}, "source_chunk": 0}],
        }],
    })
    _, _, tables = _parse_payload(raw)
    assert tables[0].rows[0].values == {"a": "1"}


def test_parse_payload_keeps_all_values_when_columns_undeclared():
    """If columns is empty/missing, row values pass through (snake_cased)."""
    raw = json.dumps({
        "doc_type": "x",
        "scalars": [],
        "tables": [{
            "name": "t",
            "columns": [],
            "rows": [{"values": {"a": "1", "b": "2"}, "source_chunk": 0}],
        }],
    })
    _, _, tables = _parse_payload(raw)
    assert tables[0].rows[0].values == {"a": "1", "b": "2"}


def test_parse_payload_drops_rows_without_values():
    raw = json.dumps({
        "doc_type": "x",
        "scalars": [],
        "tables": [{
            "name": "t",
            "rows": [
                {"values": {}, "source_chunk": 0},          # empty dict → drop
                {"source_chunk": 1},                         # missing values → drop
                {"values": "string", "source_chunk": 2},    # wrong shape → drop
                {"values": {"a": "ok"}, "source_chunk": 3},  # keep
            ],
        }],
    })
    _, _, tables = _parse_payload(raw)
    assert len(tables[0].rows) == 1
    assert tables[0].rows[0].source_chunk == 3


def test_parse_payload_drops_empty_table_names_and_empty_scalar_names():
    raw = json.dumps({
        "doc_type": "x",
        "scalars": [
            {"name": "", "value": "v"},     # empty → drop
            {"name": "ok", "value": "v"},
        ],
        "tables": [
            {"name": "", "rows": [{"values": {"a": 1}}]},   # empty name → drop
            {"name": "ok", "rows": [{"values": {"a": 1}}]},
        ],
    })
    _, scalars, tables = _parse_payload(raw)
    assert [s.name for s in scalars] == ["ok"]
    assert [t.name for t in tables] == ["ok"]


def test_parse_payload_invalid_json_recovers_via_truncation():
    """If the LLM truncates, we still get whatever closed cleanly."""
    raw = (
        '{"doc_type": "x", "scalars": [], "tables": [{"name": "t", '
        '"columns": [{"name": "a", "value_type": "text"}], '
        '"rows": [{"values": {"a": "1"}, "source_chunk": 0}, '
        '{"values": {"a": "2"}, "source_chunk": 1}, '
        '{"values": {"a": "3"'   # truncated mid-row
    )
    doc_type, _, tables = _parse_payload(raw)
    # Recovery returns doc_type="unknown" on truncation (we don't try to
    # recover the doc_type field) but should salvage the closed rows.
    assert doc_type == "unknown"
    # Two complete rows should survive
    assert len(tables) == 1
    assert len(tables[0].rows) >= 2


def test_parse_payload_handles_non_dict_top_level():
    doc_type, scalars, tables = _parse_payload(json.dumps([1, 2, 3]))
    assert doc_type == "unknown"
    assert scalars == []
    assert tables == []


def test_parse_payload_coerces_non_string_scalar_values():
    raw = json.dumps({
        "doc_type": "x",
        "scalars": [{"name": "total", "value": 1234.56, "value_type": "number"}],
        "tables": [],
    })
    _, scalars, _ = _parse_payload(raw)
    assert scalars[0].value == "1234.56"


# ===========================================================================
# Truncation helper
# ===========================================================================


def test_truncate_doc_keeps_short_input_intact():
    assert _truncate_doc("hello") == "hello"
    assert _truncate_doc("") == ""


def test_truncate_doc_marks_long_input():
    long = "x" * 100000
    truncated = _truncate_doc(long)
    assert truncated.endswith("[... TRUNCATED ...]")
    assert len(truncated) < len(long)


# ===========================================================================
# Identity extractor
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_extractor_returns_empty_payload():
    extractor = IdentityKVTablesExtractor()
    payload = await extractor.extract(chunk_indexed_text="[CHUNK_0] hi")
    assert isinstance(payload, KVTablesPayload)
    assert payload.doc_type == "unknown"
    assert payload.scalars == []
    assert payload.tables == []
    assert payload.model_id == "identity"


# ===========================================================================
# Gemini extractor with a fake client
# ===========================================================================


class _FakeGeminiPart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeGeminiContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakeGeminiPart(text)]


class _FakeGeminiCandidate:
    def __init__(self, text: str) -> None:
        self.content = _FakeGeminiContent(text)


class _FakeGeminiUsage:
    prompt_token_count = 1500
    candidates_token_count = 800


class _FakeGeminiResponse:
    def __init__(self, text: str) -> None:
        self.candidates = [_FakeGeminiCandidate(text)]
        self.usage_metadata = _FakeGeminiUsage()


class _FakeGeminiModels:
    def __init__(self, response_text: str) -> None:
        self._text = response_text
        self.last_call: dict = {}

    async def generate_content(self, *, model, contents, config):
        self.last_call = {"model": model, "contents": contents, "config": config}
        return _FakeGeminiResponse(self._text)


class _FakeGeminiAio:
    def __init__(self, response_text: str) -> None:
        self.models = _FakeGeminiModels(response_text)


class _FakeGeminiClient:
    def __init__(self, response_text: str) -> None:
        self.aio = _FakeGeminiAio(response_text)


@pytest.mark.asyncio
async def test_gemini_extractor_happy_path():
    payload = json.dumps({
        "doc_type": "bank_statement",
        "scalars": [{"name": "account_holder", "value": "Jane", "source_chunk": 0}],
        "tables": [{
            "name": "transactions",
            "columns": [{"name": "amount", "value_type": "number"}],
            "rows": [{"values": {"amount": "10.00"}, "source_chunk": 1}],
        }],
    })
    fake = _FakeGeminiClient(payload)
    extractor = GeminiKVTablesExtractor(client=fake, model="gemini-test")

    result = await extractor.extract(chunk_indexed_text="[CHUNK_0] hi\n[CHUNK_1] world")
    assert result.doc_type == "bank_statement"
    assert len(result.scalars) == 1
    assert len(result.tables) == 1
    assert result.tables[0].rows[0].values["amount"] == "10.00"
    assert result.model_id == "gemini-test"
    assert result.input_token_count == 1500
    assert result.output_token_count == 800
    # User prompt should reference the chunk-indexed text
    assert "[CHUNK_0]" in fake.aio.models.last_call["contents"]


@pytest.mark.asyncio
async def test_gemini_extractor_passes_hints_into_prompt():
    fake = _FakeGeminiClient('{"doc_type": "x", "scalars": [], "tables": []}')
    extractor = GeminiKVTablesExtractor(client=fake)
    await extractor.extract(
        chunk_indexed_text="[CHUNK_0] doc",
        doc_type_hint="invoice",
        existing_sub_entity_hints=["line_items", "tax_breakdown"],
    )
    prompt = fake.aio.models.last_call["contents"]
    assert "Likely doc_type: invoice" in prompt
    assert "line_items" in prompt
    assert "tax_breakdown" in prompt


@pytest.mark.asyncio
async def test_gemini_extractor_raises_on_transport_failure():
    class _BoomModels:
        async def generate_content(self, **_kwargs):
            raise RuntimeError("network down")

    class _Boom:
        aio = type("Aio", (), {"models": _BoomModels()})()

    extractor = GeminiKVTablesExtractor(client=_Boom())
    with pytest.raises(KVTablesExtractionError):
        await extractor.extract(chunk_indexed_text="[CHUNK_0] hi")


@pytest.mark.asyncio
async def test_gemini_extractor_raises_on_empty_candidates():
    class _EmptyResp:
        candidates = []
        usage_metadata = None

    class _EmptyModels:
        async def generate_content(self, **_kwargs):
            return _EmptyResp()

    class _EmptyClient:
        aio = type("Aio", (), {"models": _EmptyModels()})()

    extractor = GeminiKVTablesExtractor(client=_EmptyClient())
    with pytest.raises(KVTablesExtractionError, match="no candidates"):
        await extractor.extract(chunk_indexed_text="[CHUNK_0] hi")


# ===========================================================================
# Factory
# ===========================================================================


def test_factory_returns_identity_when_no_keys():
    with _env(
        KB_KV_TABLES_EXTRACTOR=None,
        KB_FIELD_EXTRACTOR=None,
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        extractor = make_kv_tables_extractor()
        assert isinstance(extractor, IdentityKVTablesExtractor)


def test_factory_picks_gemini_when_gemini_key_present():
    with _env(
        KB_KV_TABLES_EXTRACTOR=None,
        KB_FIELD_EXTRACTOR=None,
        KB_GEMINI_API_KEY="fake-key",
        KB_ANTHROPIC_API_KEY=None,
    ):
        extractor = make_kv_tables_extractor()
        assert isinstance(extractor, GeminiKVTablesExtractor)


def test_factory_explicit_selector_requires_matching_key():
    with _env(
        KB_KV_TABLES_EXTRACTOR="gemini",
        KB_FIELD_EXTRACTOR=None,
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        with pytest.raises(ValueError, match="requires KB_GEMINI_API_KEY"):
            make_kv_tables_extractor()


def test_factory_unknown_selector_raises():
    with _env(
        KB_KV_TABLES_EXTRACTOR="palm",
        KB_FIELD_EXTRACTOR=None,
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        with pytest.raises(ValueError, match="Unknown KB_KV_TABLES_EXTRACTOR"):
            make_kv_tables_extractor()


def test_factory_falls_back_to_kb_field_extractor_when_kv_var_unset():
    with _env(
        KB_KV_TABLES_EXTRACTOR=None,
        KB_FIELD_EXTRACTOR="identity",
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        extractor = make_kv_tables_extractor()
        assert isinstance(extractor, IdentityKVTablesExtractor)


def test_value_types_and_cardinalities_are_locked_tuples():
    """Guard against accidental schema drift."""
    assert set(VALUE_TYPES) == {"text", "number", "date", "datetime", "boolean", "enum"}
    assert set(CARDINALITIES) == {"many", "one"}
