"""Email parser — stdlib `email` module, one `raw_pages` row per message.

Phase 2b. Per build_tracker §5.6 decisions:
- #2: one page per email.
- #4: text = "From: …\\nTo: …\\nCc: …\\nSubject: …\\nDate: …\\n\\n<body>".
      Body prefers text/plain parts; falls back to text/html stripped via
      stdlib html.parser.
- #5: attachments → `layout_json.attachments[]` metadata only (filename,
      content_type, size_bytes). NOT recursively parsed.
- #6: magic-byte detection — first 200 bytes contain a header-like pattern
      `^[A-Z][a-zA-Z-]+:\\s`.
"""

from __future__ import annotations

import email
import re
from email.message import EmailMessage
from email.policy import default
from html.parser import HTMLParser
from typing import Any

from kb.parsers import Page, ParsedDocument, ParseError


_EMAIL_MIMES = {"message/rfc822"}

# Match an RFC 5322-ish header at the start: capital letter, then
# letters/hyphens, then a colon, then a space — covers From:, To:,
# Subject:, Received:, Message-ID:, etc.
_HEADER_PATTERN = re.compile(rb"^[A-Z][a-zA-Z-]+:\s", re.MULTILINE)


class _HTMLStripper(HTMLParser):
    """Minimal stdlib HTML → plain text. No external deps."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def _strip_html(html: str) -> str:
    parser = _HTMLStripper()
    parser.feed(html)
    return parser.get_text()


class EmailParser:
    """RFC 5322 message → one page with headers + body; attachments metadata-only."""

    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        if mime_type in _EMAIL_MIMES:
            return True
        # Header-pattern magic — first ~200 bytes contain `From:` / `Subject:` etc.
        return bool(_HEADER_PATTERN.search(magic_bytes[:200]))

    async def parse(
        self, file_bytes: bytes, *, file_id: str, workspace_id: str
    ) -> ParsedDocument:
        try:
            msg: EmailMessage = email.message_from_bytes(file_bytes, policy=default)
        except Exception as exc:
            raise ParseError(f"email parsing failed on file={file_id}: {exc}") from exc

        body, attachments = self._extract_body_and_attachments(msg)
        headers_block = self._format_headers(msg)
        text = f"{headers_block}\n\n{body}" if body else headers_block

        return ParsedDocument(pages=[Page(
            page_number=1,
            text=text,
            layout_json={
                "headers": {k: msg.get(k, "") for k in
                            ("From", "To", "Cc", "Subject", "Date", "Message-ID")},
                "attachments": attachments,
            },
        )])

    def _format_headers(self, msg: EmailMessage) -> str:
        """Render the headers we care about as `Key: Value\\n…`."""
        lines: list[str] = []
        for key in ("From", "To", "Cc", "Subject", "Date"):
            val = msg.get(key)
            if val:
                lines.append(f"{key}: {val}")
        return "\n".join(lines)

    def _extract_body_and_attachments(
        self, msg: EmailMessage
    ) -> tuple[str, list[dict[str, Any]]]:
        """Walk parts; collect text/plain body, fallback to text/html stripped;
        record attachment metadata."""
        plain_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[dict[str, Any]] = []

        # `walk()` traverses multipart trees; non-multipart returns just itself.
        for part in msg.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            filename = part.get_filename()

            if "attachment" in disposition or (filename and content_type not in ("text/plain", "text/html")):
                # Treat as attachment — metadata only.
                payload = part.get_payload(decode=True) or b""
                attachments.append({
                    "filename": filename or "<unnamed>",
                    "content_type": content_type,
                    "size_bytes": len(payload),
                })
                continue

            if content_type == "text/plain":
                try:
                    plain_parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    plain_parts.append(payload.decode("utf-8", errors="replace"))
            elif content_type == "text/html":
                try:
                    html_parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    html_parts.append(payload.decode("utf-8", errors="replace"))

        if plain_parts:
            body = "\n\n".join(p.strip() for p in plain_parts if p.strip())
        elif html_parts:
            body = "\n\n".join(_strip_html(h) for h in html_parts)
        else:
            body = ""

        return body, attachments
