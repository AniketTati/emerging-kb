"""Phase 5b — field-extraction unit tests (no DB, no real LLM)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest

from kb.extraction.fields import (
    GeminiFieldExtractor,
    IdentityFieldExtractor,
    FieldExtractionError,
    _parse_doc_type,
    _parse_proposed_fields,
    make_field_extractor,
)
from kb.extraction.promotion import (
    FieldCluster,
    PromotionThresholds,
    cluster_fields_for_doctype,
    map_value_type_to_schema_type,
    should_promote,
    _normalize_field_name,
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
# Parsers
# ===========================================================================


def test_parse_doc_type_normalizes_to_snake_case():
    assert _parse_doc_type(json.dumps({"doc_type": "Legal Contract"})) == "legal_contract"
    assert _parse_doc_type(json.dumps({"doc_type": "10-K Filing"})) == "10_k_filing"
    assert _parse_doc_type(json.dumps({"type": "Bank Statement"})) == "bank_statement"


def test_parse_doc_type_handles_fenced_json():
    raw = "```json\n" + json.dumps({"doc_type": "contract"}) + "\n```"
    assert _parse_doc_type(raw) == "contract"


def test_parse_doc_type_invalid_returns_unknown_via_dict():
    """Empty doc_type field → 'unknown'."""
    assert _parse_doc_type(json.dumps({})) == "unknown"
    assert _parse_doc_type(json.dumps({"doc_type": ""})) == "unknown"


def test_parse_doc_type_invalid_json_raises():
    with pytest.raises(FieldExtractionError):
        _parse_doc_type("not json {{")


def test_parse_proposed_fields_filters_bad_rows():
    raw = json.dumps({
        "fields": [
            {"name": "Vendor Name", "value": "ACME", "value_type": "text", "is_pii": False},
            {"name": "", "value": "x"},  # empty name → drop
            "not a dict",                  # wrong shape → drop
            {"name": "DOB", "value": "1980-01-01", "value_type": "date", "is_pii": True},
            {"name": "Bogus", "value_type": "bogus_type"},  # bad value_type → default to text
        ]
    })
    fields = _parse_proposed_fields(raw)
    assert len(fields) == 3
    names = [f.field_name for f in fields]
    assert "vendor_name" in names
    assert "dob" in names
    # bogus type → fallback to text
    assert fields[-1].value_type == "text"


# ===========================================================================
# Identity fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_classifier_returns_unknown():
    extractor = IdentityFieldExtractor()
    result = await extractor.classify(doc_text="anything")
    assert result.doc_type == "unknown"
    assert result.model_id == "identity"


@pytest.mark.asyncio
async def test_identity_proposer_returns_empty():
    extractor = IdentityFieldExtractor()
    result = await extractor.propose(doc_text="anything")
    assert result.fields == []
    assert result.model_id == "identity"


# ===========================================================================
# Factory
# ===========================================================================


def test_field_factory_selector_matrix():
    with _env(KB_FIELD_EXTRACTOR="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_field_extractor(), IdentityFieldExtractor)

    with _env(KB_FIELD_EXTRACTOR="auto", KB_GEMINI_API_KEY="fake", KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_field_extractor(), GeminiFieldExtractor)

    with _env(KB_FIELD_EXTRACTOR="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError, match="KB_FIELD_EXTRACTOR=gemini"):
            make_field_extractor()


# ===========================================================================
# Clustering + promotion logic (pure functions)
# ===========================================================================


def test_normalize_field_name():
    assert _normalize_field_name("Vendor Name") == "vendor_name"
    assert _normalize_field_name("VENDOR-name") == "vendor_name"
    assert _normalize_field_name("vendor_name") == "vendor_name"
    assert _normalize_field_name("  Multiple   Spaces  ") == "multiple_spaces"


def test_cluster_fields_dedupes_within_doc():
    """Same field appearing twice in one doc counts as ONE observation
    for that doc (no double-count of n_docs)."""
    proposed = {
        "doc_a": [
            {"field_name": "vendor_name", "value_type": "text", "field_description": ""},
            {"field_name": "Vendor Name", "value_type": "text", "field_description": ""},  # dupe (normalize equal)
        ],
        "doc_b": [
            {"field_name": "vendor_name", "value_type": "text", "field_description": ""},
        ],
    }
    clusters = cluster_fields_for_doctype(proposed_per_doc=proposed, total_docs_of_type=2)
    assert len(clusters) == 1
    assert clusters[0].n_docs_observed == 2
    assert clusters[0].prevalence == 1.0


def test_cluster_fields_prevalence_below_one():
    proposed = {
        "doc_a": [{"field_name": "vendor_name", "value_type": "text", "field_description": ""}],
        "doc_b": [{"field_name": "amount", "value_type": "number", "field_description": ""}],
        "doc_c": [{"field_name": "vendor_name", "value_type": "text", "field_description": ""}],
    }
    clusters = cluster_fields_for_doctype(proposed_per_doc=proposed, total_docs_of_type=3)
    cluster_by_name = {c.canonical_name: c for c in clusters}
    assert cluster_by_name["vendor_name"].prevalence == pytest.approx(2 / 3)
    assert cluster_by_name["amount"].prevalence == pytest.approx(1 / 3)


def test_cluster_fields_stability_with_mixed_types():
    """When the same field has mixed value_types across docs, stability =
    frequency of the modal type."""
    proposed = {
        "doc_a": [{"field_name": "amount", "value_type": "number", "field_description": ""}],
        "doc_b": [{"field_name": "amount", "value_type": "number", "field_description": ""}],
        "doc_c": [{"field_name": "amount", "value_type": "text", "field_description": ""}],
    }
    clusters = cluster_fields_for_doctype(proposed_per_doc=proposed, total_docs_of_type=3)
    assert len(clusters) == 1
    assert clusters[0].value_type == "number"  # modal
    assert clusters[0].stability == pytest.approx(2 / 3)


def test_should_promote_threshold_arithmetic():
    thresholds = PromotionThresholds(prevalence=0.8, stability=0.9,
                                     value_type_confidence=0.9, min_docs=5)
    # Passes all thresholds
    good = FieldCluster(
        canonical_name="vendor", description="", value_type="text",
        n_docs_observed=8, prevalence=0.95, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(good, thresholds) is True

    # Prevalence too low
    low_prev = FieldCluster(
        canonical_name="vendor", description="", value_type="text",
        n_docs_observed=8, prevalence=0.5, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(low_prev, thresholds) is False

    # Too few docs (below min_docs=5)
    few_docs = FieldCluster(
        canonical_name="vendor", description="", value_type="text",
        n_docs_observed=3, prevalence=1.0, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(few_docs, thresholds) is False


def test_value_type_mapping_to_schema_type():
    assert map_value_type_to_schema_type("text") == "string"
    assert map_value_type_to_schema_type("enum") == "string"
    assert map_value_type_to_schema_type("number") == "number"
    assert map_value_type_to_schema_type("date") == "date"
    assert map_value_type_to_schema_type("datetime") == "datetime"
    assert map_value_type_to_schema_type("boolean") == "boolean"
    # Unknown → fallback to string
    assert map_value_type_to_schema_type("bogus") == "string"


def test_promotion_thresholds_from_env():
    with _env(KB_PROMOTION_MIN_DOCS="20"):
        t = PromotionThresholds.from_env()
        assert t.min_docs == 20
    with _env(KB_PROMOTION_MIN_DOCS=None):
        t = PromotionThresholds.from_env()
        assert t.min_docs == 1  # default lowered to 1 in PR5 so single-doc
                                # demo corpus exercises L4 closed-world path
