"""PR8 — email-messages atomic-unit plugin tests (no LLM, no DB)."""

from __future__ import annotations

import pytest

from kb.extraction.plugins import FileMeta
from kb.extraction.plugins.email_messages import (
    EmailMessagesPlugin,
    _derive_headers,
    _split_thread,
    _strip_header_block,
)


# ---------------------------------------------------------------------------
# Thread splitting
# ---------------------------------------------------------------------------


def test_split_thread_single_message_returns_one_segment():
    body = "Hi team, please review the attached doc.\n\nThanks,\nAlice"
    assert _split_thread(body) == [body.strip()]


def test_split_thread_handles_empty_body():
    assert _split_thread("") == []
    assert _split_thread("   \n\n   ") == []


def test_split_thread_gmail_style_quoted_reply():
    body = (
        "Yes, that works for me. Let's go with option B.\n\n"
        "On Tue, Mar 15, 2026 at 10:32 AM Alice <alice@northwind.io> wrote:\n"
        "> Should we ship Friday or wait until Monday?\n"
        "> -- Alice"
    )
    parts = _split_thread(body)
    assert len(parts) == 2
    assert parts[0].startswith("Yes, that works")
    assert parts[1].startswith("On Tue, Mar 15")


def test_split_thread_outlook_original_message_separator():
    body = (
        "Looping in legal on this.\n\n"
        "-----Original Message-----\n"
        "From: Bob <bob@vertex.com>\n"
        "Sent: Monday, March 14, 2026 4:12 PM\n"
        "To: Alice <alice@northwind.io>\n"
        "Subject: Re: Q2 contract renewal\n\n"
        "Alice — attaching the markup. Big change is in §3.\n"
    )
    parts = _split_thread(body)
    assert len(parts) == 2
    assert parts[0].startswith("Looping in legal")
    assert "Original Message" in parts[1]


def test_split_thread_recursive_three_messages():
    """Reply on top of a reply on top of an original — three segments."""
    body = (
        "Confirmed.\n\n"
        "On Mon, Bob wrote:\n"
        "Sounds good — pushing now.\n\n"
        "On Sun, Alice wrote:\n"
        "Ready to deploy?\n"
    )
    parts = _split_thread(body)
    assert len(parts) == 3
    assert parts[0].startswith("Confirmed")
    assert parts[1].startswith("On Mon")
    assert parts[2].startswith("On Sun")


# ---------------------------------------------------------------------------
# Header derivation
# ---------------------------------------------------------------------------


def test_derive_headers_from_outlook_block():
    segment = (
        "-----Original Message-----\n"
        "From: bob@vertex.com\n"
        "Sent: Monday, March 14, 2026 4:12 PM\n"
        "To: alice@northwind.io\n"
        "Subject: Re: Q2 contract renewal\n\n"
        "Body text."
    )
    headers = _derive_headers(segment)
    assert headers.get("sender") == "bob@vertex.com"
    assert headers.get("recipients") == "alice@northwind.io"
    assert headers.get("subject") == "Re: Q2 contract renewal"
    assert headers.get("date") == "Monday, March 14, 2026 4:12 PM"


def test_strip_header_block_removes_leading_headers():
    segment = (
        "From: bob@vertex.com\n"
        "To: alice@northwind.io\n"
        "Subject: Re: status\n\n"
        "Body line 1.\nBody line 2."
    )
    body = _strip_header_block(segment)
    assert body == "Body line 1.\nBody line 2."


def test_strip_header_block_no_headers_returns_unchanged():
    segment = "Just a body line.\nAnother line."
    assert _strip_header_block(segment) == segment.strip()


# ---------------------------------------------------------------------------
# Plugin matcher
# ---------------------------------------------------------------------------


def test_matches_eml_mime():
    plugin = EmailMessagesPlugin()
    fm = FileMeta(file_id="x", workspace_id="w",
                  mime_type="message/rfc822",
                  inferred_doc_type="email", name="m.eml")
    assert plugin.matches(fm) is True


def test_matches_email_thread_doctype_on_txt():
    plugin = EmailMessagesPlugin()
    fm = FileMeta(file_id="x", workspace_id="w",
                  mime_type="text/plain",
                  inferred_doc_type="email_thread", name="thread.txt")
    assert plugin.matches(fm) is True


def test_matches_skips_xlsx():
    plugin = EmailMessagesPlugin()
    fm = FileMeta(file_id="x", workspace_id="w",
                  mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  inferred_doc_type="bank_statement", name="s.xlsx")
    assert plugin.matches(fm) is False


# ---------------------------------------------------------------------------
# End-to-end extract — uses real raw_pages shape from the email parser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_three_message_thread_yields_three_units():
    plugin = EmailMessagesPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="message/rfc822", inferred_doc_type="email",
        name="thread.eml",
    )
    raw_pages = [(
        1,
        # Top message body
        "Yes, ship Friday.\n\n"
        "On Wed, Bob wrote:\n"
        "Are we shipping Friday?\n\n"
        "On Tue, Alice wrote:\n"
        "Original question — when do we ship?\n",
        {"headers": {
            "from": "alice@northwind.io",
            "to": ["bob@vertex.com"],
            "subject": "Re: Re: Ship date",
            "date": "2026-03-15T10:32:00Z",
            "message_id": "<m3@northwind.io>",
        }},
    )]
    units = await plugin.extract(file_meta=fm, doc_text="", raw_pages=raw_pages)
    assert len(units) == 3
    assert all(u.unit_type == "email_message" for u in units)
    # Top message inherits envelope headers.
    assert units[0].parameters["sender"] == "alice@northwind.io"
    assert units[0].parameters["subject"] == "Re: Re: Ship date"
    assert units[0].parameters["message_index"] == 0
    # Quoted messages get derived headers (only "Wed, Bob wrote:" form
    # — no From: line in the quoted segment, so we don't derive sender
    # from it; the body_preview still captures the message content).
    assert units[1].parameters["message_index"] == 1
    assert "body_preview" in units[1].parameters
    assert units[2].parameters["message_index"] == 2


@pytest.mark.asyncio
async def test_extract_single_message_no_quote_markers():
    plugin = EmailMessagesPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="message/rfc822", inferred_doc_type="email",
        name="m.eml",
    )
    raw_pages = [(
        1,
        "Quick FYI — server restart at 3pm.",
        {"headers": {
            "from": "ops@northwind.io",
            "subject": "FYI: maintenance",
            "to": ["all@northwind.io"],
        }},
    )]
    units = await plugin.extract(file_meta=fm, doc_text="", raw_pages=raw_pages)
    assert len(units) == 1
    assert units[0].parameters["sender"] == "ops@northwind.io"
    assert units[0].parameters["subject"] == "FYI: maintenance"
    assert units[0].parameters["recipients"] == ["all@northwind.io"]
    assert units[0].parameters["summary"].startswith("Quick FYI")


@pytest.mark.asyncio
async def test_extract_empty_body_returns_no_units():
    plugin = EmailMessagesPlugin()
    fm = FileMeta(
        file_id="x", workspace_id="w",
        mime_type="message/rfc822", inferred_doc_type="email",
        name="m.eml",
    )
    raw_pages = [(1, "   \n\n   ", {"headers": {}})]
    units = await plugin.extract(file_meta=fm, doc_text="", raw_pages=raw_pages)
    assert units == []
