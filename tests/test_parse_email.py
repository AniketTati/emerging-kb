"""Phase 2b — email parser unit tests.

RED at G3: imports from `kb.parsers.email_parser` land at G4.

Spec: tests/specs/phase_2b.md §4.2.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


_PLAIN = Path(__file__).parent / "fixtures" / "tiny.eml"
_ATTACHED = Path(__file__).parent / "fixtures" / "tiny_with_attachment.eml"


async def test_email_parses_one_page():
    """An email is one conceptual document → 1 page (decision #2)."""
    from kb.parsers.email_parser import EmailParser

    doc = await EmailParser().parse(
        _PLAIN.read_bytes(), file_id="t", workspace_id="ws"
    )
    assert len(doc.pages) == 1
    assert doc.pages[0].page_number == 1


async def test_email_text_includes_headers_and_body():
    """Page text starts with `From: …\\n…\\nSubject: …\\n\\n<body>` (decision #4)."""
    from kb.parsers.email_parser import EmailParser

    doc = await EmailParser().parse(
        _PLAIN.read_bytes(), file_id="t", workspace_id="ws"
    )
    text = doc.pages[0].text
    assert "From: a@example.com" in text
    assert "Subject: hi" in text
    assert "hello world body" in text
    # Headers and body separated by blank line
    assert "\n\n" in text


async def test_email_attachments_listed_in_layout_json():
    """Attachments appear in layout_json — metadata only, no recursive parsing
    (decision #5)."""
    from kb.parsers.email_parser import EmailParser

    doc = await EmailParser().parse(
        _ATTACHED.read_bytes(), file_id="t", workspace_id="ws"
    )
    layout = doc.pages[0].layout_json
    attachments = layout.get("attachments", [])
    assert len(attachments) >= 1
    att = attachments[0]
    assert set(att.keys()) >= {"filename", "content_type", "size_bytes"}
    assert isinstance(att["size_bytes"], int)


async def test_email_html_only_body_stripped():
    """An email with only a text/html body part returns stripped plain text
    (no HTML tags)."""
    from kb.parsers.email_parser import EmailParser

    html_only = (
        b"From: c@example.com\r\n"
        b"To: d@example.com\r\n"
        b"Subject: html only\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><p>Hello <b>world</b>!</p></body></html>\r\n"
    )
    doc = await EmailParser().parse(html_only, file_id="t", workspace_id="ws")
    text = doc.pages[0].text
    # Tags stripped — no '<' or '>' in the body section
    body = text.split("\n\n", 1)[1] if "\n\n" in text else text
    assert "<" not in body
    assert ">" not in body
    assert "Hello" in body
    assert "world" in body


async def test_email_magic_detection_via_header_pattern():
    """When Content-Type is missing, recognize email by RFC822 header pattern."""
    from kb.parsers.email_parser import EmailParser

    parser = EmailParser()
    assert parser.can_handle(
        mime_type="application/octet-stream",
        magic_bytes=b"From: a@example.com\nSubject: hi\n\nbody",
    ) is True
    assert parser.can_handle(
        mime_type="message/rfc822", magic_bytes=b"",
    ) is True
    # PDF magic must NOT match
    assert parser.can_handle(
        mime_type="application/pdf", magic_bytes=b"%PDF-1.4",
    ) is False
