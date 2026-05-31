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
    normalize_unit_key,
    should_promote,
    _normalize_field_name,
)


def test_normalize_unit_key_collapses_spelling_variants():
    # FIX 4 — separator / spacing / case / plural variants of the SAME table
    # name share a key.
    k = normalize_unit_key("transaction_listing")
    assert normalize_unit_key("transactionlisting") == k
    assert normalize_unit_key("Transaction Listing") == k
    assert normalize_unit_key("transaction-listing") == k
    # plurals of a word collapse to singular
    assert normalize_unit_key("Transactions") == normalize_unit_key("transaction")
    assert normalize_unit_key("line_items") == normalize_unit_key("LineItem")
    # ...but DIFFERENT concepts keep DIFFERENT keys (no semantic reduction)
    assert normalize_unit_key("transactionlisting") != normalize_unit_key("transaction")
    # Latin/Greek already-singular endings are left alone
    assert normalize_unit_key("status") == "status"
    assert normalize_unit_key("basis") == "basis"


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
    # FIX 5 — count-based promotion. min_docs is the absolute floor (1),
    # promote_count is the "repeats enough times" bar (2); prevalence is now
    # an OR alternative (keeps first-doc seeding), not a dominating AND-gate.
    thresholds = PromotionThresholds(prevalence=0.8, stability=0.9,
                                     value_type_confidence=0.9, min_docs=1,
                                     promote_count=2)
    # High prevalence (e.g. first doc of a type) → promoted.
    good = FieldCluster(
        canonical_name="vendor", description="", value_type="text",
        n_docs_observed=8, prevalence=0.95, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(good, thresholds) is True

    # THE FIX 5 WIN: low prevalence but repeats ≥ promote_count → promoted.
    # (interest_rate_all_in in 3/6 loans, prevalence 0.50, used to be rejected.)
    repeats_low_prev = FieldCluster(
        canonical_name="interest_rate_all_in", description="", value_type="number",
        n_docs_observed=3, prevalence=0.5, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(repeats_low_prev, thresholds) is True

    # Noise: seen once, low prevalence, below promote_count → rejected.
    one_off = FieldCluster(
        canonical_name="random_field", description="", value_type="text",
        n_docs_observed=1, prevalence=0.16, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(one_off, thresholds) is False

    # Type-unstable field still gated even when it repeats.
    unstable = FieldCluster(
        canonical_name="amount", description="", value_type="number",
        n_docs_observed=4, prevalence=0.5, stability=0.5,
        value_type_confidence=0.5,
    )
    assert should_promote(unstable, thresholds) is False


def test_should_promote_count_threshold_tunable():
    # Raising promote_count makes a 2-doc field no longer auto-promote.
    cluster = FieldCluster(
        canonical_name="fee", description="", value_type="number",
        n_docs_observed=2, prevalence=0.3, stability=1.0,
        value_type_confidence=1.0,
    )
    assert should_promote(
        cluster,
        PromotionThresholds(prevalence=0.8, stability=0.9,
                            value_type_confidence=0.9, min_docs=1, promote_count=2),
    ) is True
    assert should_promote(
        cluster,
        PromotionThresholds(prevalence=0.8, stability=0.9,
                            value_type_confidence=0.9, min_docs=1, promote_count=3),
    ) is False


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


def test_doc_root_name_for_pascalcase_conversion():
    from kb.extraction.promotion import doc_root_name_for, sub_entity_name_for

    # snake_case → PascalCase
    assert doc_root_name_for("bank_statement") == "BankStatement"
    assert doc_root_name_for("master_services_agreement") == "MasterServicesAgreement"
    assert doc_root_name_for("email_thread") == "EmailThread"

    # Hyphen + space variants normalize through the same path.
    assert doc_root_name_for("bank-statement") == "BankStatement"
    assert doc_root_name_for("Bank Statement") == "BankStatement"

    # Unknown / empty / None-ish all fall back to "Doc" so no
    # nameless types ever land in the schema.
    assert doc_root_name_for("unknown") == "Doc"
    assert doc_root_name_for("UNKNOWN") == "Doc"
    assert doc_root_name_for("") == "Doc"

    # Sub-entity uses the same convention.
    assert sub_entity_name_for("transaction") == "Transaction"
    assert sub_entity_name_for("line_item") == "LineItem"
    assert sub_entity_name_for("row") == "Row"
