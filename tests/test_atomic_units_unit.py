"""Phase 5c — atomic-unit unit tests (no DB, no real LLM)."""

from __future__ import annotations

import pytest

from kb.extraction.anomaly import (
    compute_centroid,
    score_unit,
    score_units_jit,
)
from kb.extraction.plugins import FileMeta, dispatch
from kb.extraction.plugins.clauses import _parse_clauses
from kb.extraction.plugins.transactions import _parse_transactions
from kb.extraction.plugins.rows import RowsPlugin


# ===========================================================================
# Plugin dispatch
# ===========================================================================


def test_dispatch_xlsx_mime_routes_to_rows():
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type=None, name="vendors.xlsx",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "row"


def test_dispatch_contract_doc_type_routes_to_clauses():
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/pdf",
        inferred_doc_type="legal_contract", name="msa.pdf",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "clause"


def test_dispatch_bank_statement_routes_to_transactions():
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/pdf",
        inferred_doc_type="bank_statement", name="stmt.pdf",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "transaction"


def test_dispatch_bank_statement_xlsx_routes_to_transactions_not_rows():
    """Regression for the order-priority bug: previously the dispatcher
    ran RowsPlugin first (matched on xlsx MIME), so a bank statement
    UPLOADED AS XLSX always got the structural row plugin instead of
    TransactionsPlugin. Result: 21 generic `row` units with raw
    cells[] arrays instead of semantic `transaction` units with
    {date, debit, credit, balance} parameters — and Q-mode queries
    like 'sum the transactions' couldn't compile against the data.

    Fix: semantic plugins (transactions / clauses / email_messages)
    now run BEFORE structural rows. Verified here against the exact
    shape that hit the bug in production.
    """
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type="bank_statement", name="bank-statement.xlsx",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "transaction", (
        f"Expected transaction (semantic), got {plugin.UNIT_TYPE!r} "
        "— RowsPlugin is still pre-empting TransactionsPlugin"
    )


def test_dispatch_unknown_doctype_xlsx_falls_back_to_rows():
    """The other side of the order fix: a generic xlsx (e.g. employee
    roster, custom report) with no semantic doc_type still routes to
    RowsPlugin. Order semantic-first does NOT mean structural-never."""
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type=None, name="vendors.xlsx",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "row"


def test_dispatch_unknown_doctype_returns_none():
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/pdf",
        inferred_doc_type="unknown", name="x.pdf",
    )
    assert dispatch(fm) is None


def test_dispatch_fuzzy_contract_match():
    """heuristic substring match — 'service_agreement' contains 'agreement'."""
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/pdf",
        inferred_doc_type="custom_service_agreement", name="sa.pdf",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "clause"


# PR8 dispatcher coverage — broader clauses-matcher synonyms +
# email_messages plugin priority over generic_items + generic_items
# fallback for prose doc_types.


@pytest.mark.parametrize("doc_type", [
    "offer_letter",                    # employment_offer_letter shorthand
    "employment_offer_letter",
    "master_services_agreement",       # MSA — narrow match pre-PR8
    "subscription_agreement",          # SaaS
    "mutual_nda",
    "addendum",
    "statement_of_work",
    "license_agreement",
    "lease_agreement",
])
def test_dispatch_pr8_broader_clauses_synonyms(doc_type):
    """All these classifications should reach the clauses plugin now —
    previously only `legal_contract` / `contract` / `agreement` /
    `nda` / `employment` substring matched."""
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/pdf",
        inferred_doc_type=doc_type, name=f"{doc_type}.pdf",
    )
    plugin = dispatch(fm)
    assert plugin is not None, f"no plugin matched for {doc_type}"
    assert plugin.UNIT_TYPE == "clause"


def test_dispatch_eml_routes_to_email_messages():
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="message/rfc822",
        inferred_doc_type="email_thread", name="thread.eml",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "email_message"


def test_dispatch_email_thread_doctype_on_txt_routes_to_email_messages():
    """A .txt file classified as email_thread should also route here
    (pasted-thread case)."""
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="text/plain",
        inferred_doc_type="email_thread", name="thread.txt",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "email_message"


@pytest.mark.parametrize("doc_type", [
    "incident_postmortem",
    "performance_review",
    "press_release",
    "case_study",
    "bug_report",
    "rfc",
    "job_posting",
    "resume",
    "lab_report",
    "financial_report",
    "meeting_minutes",
    "vendor_evaluation",
])
def test_dispatch_prose_doctypes_route_to_generic_items(doc_type):
    """Long-tail prose doc_types now route to generic_items instead
    of returning None (which caused 0 atomic_units)."""
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="text/markdown",
        inferred_doc_type=doc_type, name=f"{doc_type}.md",
    )
    plugin = dispatch(fm)
    assert plugin is not None, f"no plugin matched for {doc_type}"
    # generic_items reports unit_type='item' at the top level even
    # though per-row unit_type comes from the LLM's item_type field.
    assert plugin.UNIT_TYPE == "item"


def test_dispatch_unknown_doctype_still_returns_none():
    """generic_items must NOT match when classification failed —
    otherwise we'd burn LLM tokens on every misclassified file."""
    fm = FileMeta(
        file_id="x", workspace_id="w", mime_type="application/pdf",
        inferred_doc_type="unknown", name="x.pdf",
    )
    assert dispatch(fm) is None


def test_dispatch_xlsx_doctype_still_routes_to_rows_not_generic():
    """The structural row plugin takes priority over generic_items
    even when the doc_type label is generic-prose-like."""
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type="financial_report", name="kpi.xlsx",
    )
    plugin = dispatch(fm)
    assert plugin is not None
    assert plugin.UNIT_TYPE == "row"


# ===========================================================================
# Rows plugin — no LLM
# ===========================================================================


@pytest.mark.asyncio
async def test_rows_plugin_extracts_rows_from_xlsx_text():
    plugin = RowsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type=None, name="vendors.xlsx",
    )
    raw_pages = [
        (1, "# Sheet: Vendors\nname\taddress\tphone\nACME\t123 Main\t555-1234\nXYZ Co\t456 Oak\t555-5678",
         {"sheet_name": "Vendors", "rows": 3, "cols": 3}),
    ]
    units = await plugin.extract(file_meta=fm, doc_text="", raw_pages=raw_pages)
    assert len(units) == 2
    assert units[0].unit_type == "row"
    assert units[0].parameters["sheet_name"] == "Vendors"
    assert units[0].parameters["row_index"] == 1
    assert units[0].parameters["cells"] == ["ACME", "123 Main", "555-1234"]
    assert units[0].parameters["header"] == ["name", "address", "phone"]


@pytest.mark.asyncio
async def test_rows_plugin_handles_empty_sheet():
    plugin = RowsPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        inferred_doc_type=None, name="empty.xlsx",
    )
    raw_pages = [(1, "# Sheet: Empty", {"sheet_name": "Empty", "rows": 0, "cols": 0})]
    units = await plugin.extract(file_meta=fm, doc_text="", raw_pages=raw_pages)
    assert units == []


# ===========================================================================
# Clauses + transactions parsers
# ===========================================================================


def test_parse_clauses_filters_bad_rows():
    raw = '{"clauses": [{"clause_type": "Payment Terms"}, {}, "string", {"clause_type": "termination", "term_months": 12}]}'
    clauses = _parse_clauses(raw)
    assert len(clauses) == 2
    assert clauses[0]["clause_type"] == "payment_terms"
    assert clauses[1]["term_months"] == 12


def test_parse_transactions_coerces_amount_to_float():
    raw = '{"transactions": [{"date": "2024-01-15", "amount": "1250.50", "type": "debit"}]}'
    txns = _parse_transactions(raw)
    assert len(txns) == 1
    assert txns[0]["amount"] == 1250.50


def test_parse_transactions_drops_uncoercible_amount():
    raw = '{"transactions": [{"date": "2024-01-15", "amount": "not-a-number"}]}'
    txns = _parse_transactions(raw)
    assert txns == []


# ===========================================================================
# Anomaly scoring
# ===========================================================================


def test_compute_centroid_numeric_and_categorical():
    units = [
        {"amount": 100.0, "currency": "USD"},
        {"amount": 200.0, "currency": "USD"},
        {"amount": 150.0, "currency": "EUR"},
    ]
    numeric, categorical = compute_centroid(units)
    assert "amount" in numeric
    mean, std = numeric["amount"]
    assert mean == 150.0
    assert std > 0  # 3 distinct values
    assert categorical["currency"] == {"USD": 2, "EUR": 1}


def test_score_unit_high_zscore_for_outlier():
    historical = [
        {"payment_due_days": 30},
        {"payment_due_days": 30},
        {"payment_due_days": 30},
        {"payment_due_days": 30},
        {"payment_due_days": 35},
    ]
    numeric, categorical = compute_centroid(historical)
    # An outlier — 4 hours, way different
    outlier = {"payment_due_days": 0.17}  # 4 hours ≈ 0.17 days
    score = score_unit(outlier, numeric, categorical)
    assert score is not None
    assert score > 1.0, f"expected outlier to have score > 1.0; got {score}"


def test_score_unit_categorical_new_value_high_score():
    historical = [
        {"clause_type": "payment_terms"},
        {"clause_type": "payment_terms"},
        {"clause_type": "termination"},
    ]
    numeric, categorical = compute_centroid(historical)
    score = score_unit({"clause_type": "indemnification"}, numeric, categorical)
    assert score == 1.0  # never seen → 1 - 0/3 = 1.0


def test_score_unit_returns_none_for_no_overlap():
    """Unit has no parameters in common with historical → score = None."""
    historical = [{"a": 1}, {"a": 2}]
    numeric, categorical = compute_centroid(historical)
    score = score_unit({"b": 1}, numeric, categorical)
    assert score is None


def test_score_units_jit_handles_empty_history():
    """First-ever unit: historical empty → all scores None."""
    new = [{"x": 1}]
    scores = score_units_jit(new, [])
    assert scores == [None]
