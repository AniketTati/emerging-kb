"""Phase 6 — schema-driven extractor unit tests (no DB, no real LLM)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest

from kb.extraction.entities import (
    GeminiSchemaDrivenExtractor,
    IdentitySchemaDrivenExtractor,
    SchemaEntityRequest,
    SchemaExtractionError,
    _build_user_prompt,
    _parse_instances,
    build_chunk_indexed_text,
    make_schema_driven_extractor,
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


def test_parse_instances_filters_unknown_fields():
    """LLM may emit fields not in the schema — silently drop them."""
    raw = json.dumps({
        "instances": [
            {
                "fields": {"vendor_name": "ACME", "bogus_field": "x"},
                "citations": {"vendor_name": 0, "bogus_field": 1},
            }
        ]
    })
    instances = _parse_instances(raw, valid_field_names={"vendor_name", "amount"})
    assert len(instances) == 1
    assert instances[0].fields == {"vendor_name": "ACME"}
    assert instances[0].citations == {"vendor_name": 0}


def test_parse_instances_drops_non_int_citations():
    raw = json.dumps({
        "instances": [
            {
                "fields": {"amount": 100},
                "citations": {"amount": "not_an_int"},
            }
        ]
    })
    instances = _parse_instances(raw, valid_field_names={"amount"})
    assert len(instances) == 1
    assert instances[0].fields == {"amount": 100}
    assert instances[0].citations == {}  # dropped


def test_parse_instances_skips_empty_instances():
    """Instance with neither fields nor citations → skip."""
    raw = json.dumps({"instances": [{"fields": {}, "citations": {}}]})
    instances = _parse_instances(raw, valid_field_names={"x"})
    assert instances == []


def test_parse_instances_handles_code_fence():
    raw = "```json\n" + json.dumps({
        "instances": [{"fields": {"x": 1}, "citations": {}}]
    }) + "\n```"
    instances = _parse_instances(raw, valid_field_names={"x"})
    assert len(instances) == 1


def test_parse_instances_invalid_json_raises():
    with pytest.raises(SchemaExtractionError):
        _parse_instances("not json {{", valid_field_names={"x"})


def test_parse_instances_handles_missing_top_level_key():
    """Top-level isn't a dict, or `instances` missing → []."""
    assert _parse_instances(json.dumps({}), valid_field_names={"x"}) == []
    assert _parse_instances(json.dumps([1, 2]), valid_field_names={"x"}) == []


def test_parse_instances_coerces_citation_strings_to_int():
    """LLM sometimes returns chunk indexes as strings; coerce."""
    raw = json.dumps({
        "instances": [
            {"fields": {"x": 1}, "citations": {"x": "3"}}
        ]
    })
    instances = _parse_instances(raw, valid_field_names={"x"})
    assert instances[0].citations == {"x": 3}


# ===========================================================================
# Chunk-indexed text builder
# ===========================================================================


def test_build_chunk_indexed_text_format():
    chunks = [("cc-1", "First chunk text"), ("cc-2", "Second chunk text")]
    text = build_chunk_indexed_text(chunks)
    assert "[CHUNK_0]" in text
    assert "[CHUNK_1]" in text
    assert "First chunk text" in text
    assert "Second chunk text" in text


def test_build_chunk_indexed_text_empty():
    assert build_chunk_indexed_text([]) == ""


# ===========================================================================
# Prompt builder
# ===========================================================================


def test_build_user_prompt_includes_fields_and_chunks():
    req = SchemaEntityRequest(
        schema_entity_name="Clause",
        schema_entity_description="A contract clause",
        field_defs=[
            {"name": "clause_type", "type": "string", "nl_description": "Type of clause"},
            {"name": "payment_due_days", "type": "number", "nl_description": ""},
        ],
        chunk_indexed_text="[CHUNK_0]\nNet 30 terms",
    )
    prompt = _build_user_prompt(req)
    assert "Clause" in prompt
    assert "clause_type (string)" in prompt
    assert "payment_due_days (number)" in prompt
    assert "[CHUNK_0]" in prompt


# ===========================================================================
# Identity fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_extractor_returns_empty():
    extractor = IdentitySchemaDrivenExtractor()
    request = SchemaEntityRequest(
        schema_entity_name="X", field_defs=[], chunk_indexed_text="",
    )
    result = await extractor.extract(request=request)
    assert result.instances == []
    assert result.model_id == "identity"


# ===========================================================================
# Factory
# ===========================================================================


def test_factory_selector_matrix():
    with _env(KB_ENTITY_EXTRACTOR="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_schema_driven_extractor(), IdentitySchemaDrivenExtractor)

    with _env(KB_ENTITY_EXTRACTOR="auto", KB_GEMINI_API_KEY="fake", KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_schema_driven_extractor(), GeminiSchemaDrivenExtractor)

    with _env(KB_ENTITY_EXTRACTOR="identity"):
        assert isinstance(make_schema_driven_extractor(), IdentitySchemaDrivenExtractor)

    with _env(KB_ENTITY_EXTRACTOR="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError, match="KB_ENTITY_EXTRACTOR=gemini"):
            make_schema_driven_extractor()

    with _env(KB_ENTITY_EXTRACTOR="bogus"):
        with pytest.raises(ValueError, match="Unknown KB_ENTITY_EXTRACTOR"):
            make_schema_driven_extractor()


# ===========================================================================
# Mocked Gemini path — end-to-end on a fake response
# ===========================================================================


class _FakeResponse:
    def __init__(self, raw_text: str, in_tok: int = 100, out_tok: int = 50):
        self.candidates = [
            type("C", (), {
                "content": type("Ct", (), {
                    "parts": [type("P", (), {"text": raw_text})]
                })
            })
        ]
        self.usage_metadata = type("U", (), {
            "prompt_token_count": in_tok,
            "candidates_token_count": out_tok,
        })


class _FakeModels:
    def __init__(self, raw_text: str, capture: dict):
        self._raw_text = raw_text
        self._capture = capture

    async def generate_content(self, **kwargs):
        self._capture.update(kwargs)
        return _FakeResponse(self._raw_text)


class _FakeAio:
    def __init__(self, raw_text: str, capture: dict):
        self.models = _FakeModels(raw_text, capture)


class _FakeClient:
    def __init__(self, raw_text: str):
        self._raw_text = raw_text
        self.last_kwargs: dict[str, Any] = {}
        self.aio = _FakeAio(raw_text, self.last_kwargs)


@pytest.mark.asyncio
async def test_gemini_extractor_parses_instances_end_to_end():
    raw = json.dumps({
        "instances": [
            {
                "fields": {"clause_type": "payment_terms", "payment_due_days": 30},
                "citations": {"clause_type": 0, "payment_due_days": 1},
            },
            {
                "fields": {"clause_type": "termination"},
                "citations": {"clause_type": 2},
            },
        ]
    })
    extractor = GeminiSchemaDrivenExtractor(client=_FakeClient(raw), model="gemini-2.5-flash")
    request = SchemaEntityRequest(
        schema_entity_name="Clause",
        field_defs=[
            {"name": "clause_type", "type": "string", "nl_description": ""},
            {"name": "payment_due_days", "type": "number", "nl_description": ""},
        ],
        chunk_indexed_text="[CHUNK_0] x [CHUNK_1] y [CHUNK_2] z",
    )
    result = await extractor.extract(request=request)
    assert len(result.instances) == 2
    assert result.instances[0].fields["clause_type"] == "payment_terms"
    assert result.instances[0].citations["payment_due_days"] == 1
    assert result.instances[1].fields["clause_type"] == "termination"
    assert result.model_id == "gemini-2.5-flash"
    assert result.input_token_count == 100
