"""PR8 — generic-items atomic-unit plugin tests.

Focuses on the parser (`_parse_items`) and matcher (no LLM mocking
required for those) plus a smoke test of the prompt-builder per
doc-type hint table. The Gemini call itself is covered by the
end-to-end test in test_atomic_units_worker.py via the fake-extractor
pattern that exists for the other LLM plugins.
"""

from __future__ import annotations

import json

import pytest

from kb.extraction.plugins import FileMeta
from kb.extraction.plugins.generic_items import (
    GenericItemsPlugin,
    _DOC_TYPE_HINTS,
    _build_user_prompt,
    _parse_items,
)


# ---------------------------------------------------------------------------
# Parser — _parse_items
# ---------------------------------------------------------------------------


def test_parse_items_basic_shape():
    raw = json.dumps({"items": [
        {"item_type": "action_item", "title": "Patch IAM",
         "summary": "Update staging IAM policy.", "actor": "alice"},
        {"item_type": "lesson_learned", "title": "Add IAM lint",
         "summary": "Run an IAM linter in CI."},
    ]})
    out = _parse_items(raw)
    assert len(out) == 2
    assert out[0]["item_type"] == "action_item"
    assert out[0]["title"] == "Patch IAM"
    assert out[0]["actor"] == "alice"
    assert out[1]["item_type"] == "lesson_learned"


def test_parse_items_drops_bad_shape():
    raw = json.dumps({"items": [
        {"item_type": "kpi", "title": "Revenue", "value": 1_000_000},
        "not a dict",                          # wrong shape
        {"item_type": "", "summary": "no type"},  # missing → fallback
    ]})
    out = _parse_items(raw)
    assert len(out) == 2
    assert out[0]["item_type"] == "kpi"
    assert out[0]["value"] == 1_000_000
    # Missing item_type falls back to UNIT_TYPE ("item"), not dropped.
    assert out[1]["item_type"] == "item"
    assert out[1]["summary"] == "no type"


def test_parse_items_normalizes_item_type_to_snake_case():
    raw = json.dumps({"items": [
        {"item_type": "Action Item", "title": "X"},
        {"item_type": "ROOT  CAUSE", "title": "Y"},
    ]})
    out = _parse_items(raw)
    assert out[0]["item_type"] == "action_item"
    assert out[1]["item_type"] == "root__cause"  # collapses spaces only


def test_parse_items_handles_truncated_output():
    """Tolerates Gemini cutting off mid-stream — recovers complete items."""
    raw = (
        '{"items": ['
        '{"item_type": "kpi", "title": "Revenue", "value": 1000000},'
        '{"item_type": "kpi", "title": "Margin", "value": 0.42},'
        '{"item_type": "kpi", "title": "Churn",'  # truncated mid-element
    )
    out = _parse_items(raw)
    assert len(out) == 2
    assert out[0]["title"] == "Revenue"
    assert out[1]["title"] == "Margin"


def test_parse_items_strips_code_fences():
    raw = '```json\n{"items": [{"item_type": "decision", "title": "Go"}]}\n```'
    out = _parse_items(raw)
    assert len(out) == 1
    assert out[0]["item_type"] == "decision"


# ---------------------------------------------------------------------------
# Prompt builder — sanity-check that doc-type hints are injected
# ---------------------------------------------------------------------------


def test_prompt_includes_hint_for_known_doctype():
    p = _build_user_prompt("incident_postmortem", "INCIDENT REPORT\n\n…")
    assert "incident_postmortem" in p
    assert "timeline_entry" in p
    assert "action_item" in p
    assert "<doc>" in p


def test_prompt_falls_back_to_generic_hint_for_unknown_doctype():
    p = _build_user_prompt("obscure_doc_format_xyz", "body")
    # No hint table entry → generic instruction to invent labels.
    assert "obscure_doc_format_xyz" in p
    assert "snake_case item_type label" in p
    assert "<doc>\nbody\n</doc>" in p


def test_doc_type_hints_table_covers_all_motivating_demo_doctypes():
    """Audit: the demo corpus we care about should all have a hint
    (better extraction than the generic fallback)."""
    must_cover = {
        "incident_postmortem", "performance_review", "press_release",
        "case_study", "bug_report", "rfc", "job_posting", "resume",
        "lab_report", "financial_report", "meeting_minutes",
        "vendor_evaluation", "discharge_summary",
        "explanation_of_benefits", "invoice", "offer_letter",
    }
    missing = must_cover - set(_DOC_TYPE_HINTS.keys())
    assert not missing, f"missing hints for: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_type,expected", [
    # Prose doc_types that need fallback coverage
    ("incident_postmortem", True),
    ("performance_review", True),
    ("press_release", True),
    ("case_study", True),
    ("lab_report", True),
    ("invoice", True),
    # Skipped — handled by other plugins
    ("legal_contract", False),
    ("bank_statement", False),
    ("email_thread", False),
    ("price_sheet", False),
    # Skipped — uninformative classifications
    ("unknown", False),
    ("document", False),
    ("other", False),
    (None, False),
    ("", False),
])
def test_matches_per_doc_type(doc_type, expected):
    plugin = GenericItemsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="text/markdown",
        inferred_doc_type=doc_type, name="x.md",
    )
    assert plugin.matches(fm) is expected


def test_matches_skips_xlsx_mime_regardless_of_doctype():
    plugin = GenericItemsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type="financial_report",   # would otherwise match
        name="kpi.xlsx",
    )
    assert plugin.matches(fm) is False


def test_matches_skips_eml_mime_regardless_of_doctype():
    plugin = GenericItemsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="message/rfc822",
        inferred_doc_type="case_study",         # would otherwise match
        name="weird.eml",
    )
    assert plugin.matches(fm) is False


# ---------------------------------------------------------------------------
# extract() degrades gracefully when no API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_no_api_key_returns_empty(monkeypatch):
    monkeypatch.delenv("KB_GEMINI_API_KEY", raising=False)
    plugin = GenericItemsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="text/markdown",
        inferred_doc_type="press_release", name="pr.md",
    )
    units = await plugin.extract(
        file_meta=fm,
        doc_text="NorthWind Capital Today Announces…",
        raw_pages=[(1, "body", {})],
    )
    assert units == []


@pytest.mark.asyncio
async def test_extract_empty_doc_text_returns_empty(monkeypatch):
    monkeypatch.setenv("KB_GEMINI_API_KEY", "fake")
    plugin = GenericItemsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="text/markdown",
        inferred_doc_type="press_release", name="pr.md",
    )
    units = await plugin.extract(
        file_meta=fm, doc_text="   \n\n  ", raw_pages=[],
    )
    assert units == []
