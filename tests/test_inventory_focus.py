"""Query-review fix 6 — inventory answers the SPECIFIC count when the query
names a doc-type ("how many bank statements" → "You have 8 bank statements"),
instead of always dumping the whole-workspace table.
"""

from __future__ import annotations

from kb.query.inventory import _match_query_doc_type, _render_markdown, _TypeRow


def _rows() -> list[_TypeRow]:
    return [
        _TypeRow("bank_statement", 8, ["a.md"], ["1"]),
        _TypeRow("loan_agreement", 6, ["b.md"], ["2"]),
        _TypeRow("annual_report", 3, ["c.md"], ["3"]),
        _TypeRow(None, 2, ["d.md"], ["4"]),  # unclassified
    ]


def test_matches_specific_doc_type_with_plural():
    assert _match_query_doc_type("how many bank statements do I have", _rows()).doc_type == "bank_statement"
    assert _match_query_doc_type("how many loan agreements", _rows()).doc_type == "loan_agreement"
    assert _match_query_doc_type("count the annual reports", _rows()).doc_type == "annual_report"


def test_no_match_for_generic_or_absent_type():
    assert _match_query_doc_type("what types of documents do I have", _rows()) is None
    assert _match_query_doc_type("how many documents", _rows()) is None
    assert _match_query_doc_type("how many invoices do I have", _rows()) is None  # not in ws
    assert _match_query_doc_type("", _rows()) is None
    assert _match_query_doc_type(None, _rows()) is None


def test_most_specific_match_wins():
    rows = [
        _TypeRow("report", 4, ["x.md"], ["1"]),
        _TypeRow("annual_report", 3, ["y.md"], ["2"]),
    ]
    # 'annual report' (2 words) beats 'report' (1 word)
    assert _match_query_doc_type("how many annual reports", rows).doc_type == "annual_report"


def test_focused_render_headlines_specific_count():
    rows = _rows()
    md = _render_markdown(rows, focus=rows[0])
    assert md.splitlines()[0] == "You have **8 bank statements** in this workspace."
    assert "Full breakdown:" in md
    assert "| Type | Count | Files |" in md  # table still present for context


def test_singular_count_no_plural_s():
    md = _render_markdown([_TypeRow("nda", 1, ["x.md"], ["1"])], focus=_TypeRow("nda", 1, ["x.md"], ["1"]))
    assert md.splitlines()[0] == "You have **1 nda** in this workspace."


def test_generic_render_unchanged():
    md = _render_markdown(_rows())
    assert md.splitlines()[0].startswith("You have **19 documents** across **3 document types**")
