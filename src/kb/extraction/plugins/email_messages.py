"""Phase 5c / PR8 — email-message atomic-unit plugin.

Splits .eml files (or any file classified as `email_thread`) into one
atomic unit per message in the thread. Each unit captures sender / date /
subject / body_preview — enough for retrieval to surface "which message
said X" and for the per-corpus anomaly scorer to flag outliers (e.g. a
thread with 20 messages in a workspace where threads usually have 3).

No LLM call — uses regex-based reply-marker detection. Works on:

  - Single-message .eml files (whole body → one message unit, populated
    from the file's parsed headers in layout_json)
  - Thread digests (Gmail-style "On X wrote:" quotation, Outlook-style
    "-----Original Message-----" separators, top-quoted "From:" headers)

The per-message split is intentionally lossy — Wave A goal is just to
surface that the file has N discrete messages so retrieval can cite the
right one. Wave B can graft proper MIME-tree parsing on top if needed.
"""

from __future__ import annotations

import re
from typing import Any

from kb.extraction.plugins import AtomicUnit, FileMeta


UNIT_TYPE = "email_message"


# Reply / quote markers. Order is purely for documentation — the
# splitter takes the earliest match across all of them per iteration.
#
# We deliberately do NOT include a bare-headers ("From:\nTo:\nSent:")
# pattern because it false-fires immediately AFTER the "Original
# Message" separator (which is always followed by a header block in
# Outlook quotes) and produces a phantom header-only segment. The two
# patterns below cover ~all real-world reply quoting (Gmail's "On X
# wrote:" + Outlook's "----- Original Message -----").
_QUOTE_MARKERS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^On\s.{1,200}?wrote:\s*$", re.MULTILINE | re.IGNORECASE),
]


def _split_thread(body: str) -> list[str]:
    """Split the body into chronological-newest-first message segments.

    Returns a list of message bodies (the first element is the
    most-recent / top message; subsequent elements are quoted older
    messages, in reverse-chronological order — same shape the user sees
    in their mail client). Empty body → empty list.
    """
    if not body or not body.strip():
        return []

    # Find the earliest match of ANY marker — that's where the next
    # quoted message starts. Recurse on the tail to split deeper threads.
    # Cap iterations at 20 (sanity bound — even forwarded threads
    # rarely exceed 10 messages, and we don't want pathological inputs
    # to runaway).
    segments: list[str] = []
    remaining = body
    for _ in range(20):
        # If `remaining` STARTS with a marker (true for every iteration
        # after the first), advance the search offset past the leading
        # marker line — otherwise the next `search` call would find that
        # same marker at position 0 and we'd loop forever producing
        # empty segments.
        search_offset = 0
        for marker in _QUOTE_MARKERS:
            head = marker.match(remaining)
            if head is not None:
                search_offset = head.end()
                break

        earliest_match: re.Match[str] | None = None
        for marker in _QUOTE_MARKERS:
            m = marker.search(remaining, search_offset)
            if m is None:
                continue
            if earliest_match is None or m.start() < earliest_match.start():
                earliest_match = m

        if earliest_match is None:
            segments.append(remaining.strip())
            break

        segments.append(remaining[: earliest_match.start()].strip())
        remaining = remaining[earliest_match.start():]
        # The next iteration's "current" segment starts WITH the marker
        # so we can re-extract sender headers from it (see search_offset
        # bump above).
    else:
        # Hit the iteration cap — capture whatever's left so we don't
        # silently drop content.
        segments.append(remaining.strip())

    # Drop empty segments (e.g. when the body started with a marker).
    return [s for s in segments if s]


# Per-message header regexes used to re-derive sender/subject/date from
# a quoted-segment's leading "From:" / "Subject:" / "Sent:" lines.
_PER_MESSAGE_HEADER_PATTERNS = {
    "sender": re.compile(r"^From:\s*(.+)$", re.MULTILINE | re.IGNORECASE),
    "recipients": re.compile(r"^To:\s*(.+)$", re.MULTILINE | re.IGNORECASE),
    "subject": re.compile(r"^Subject:\s*(.+)$", re.MULTILINE | re.IGNORECASE),
    "date": re.compile(r"^(?:Sent|Date):\s*(.+)$", re.MULTILINE | re.IGNORECASE),
}


def _derive_headers(segment: str) -> dict[str, str]:
    """Best-effort extraction of per-quoted-message headers."""
    out: dict[str, str] = {}
    # Only scan the first ~600 chars — headers always live at the top.
    window = segment[:600]
    for key, pat in _PER_MESSAGE_HEADER_PATTERNS.items():
        m = pat.search(window)
        if m:
            out[key] = m.group(1).strip()
    return out


def _strip_header_block(segment: str) -> str:
    """Remove the leading From:/To:/Subject:/Sent: block from a quoted
    segment so the body_preview is the actual message body, not the
    header lines we already pulled out."""
    lines = segment.splitlines()
    out_start = 0
    for i, ln in enumerate(lines[:8]):
        if re.match(r"^(From|To|Cc|Subject|Sent|Date):\s", ln, re.IGNORECASE):
            out_start = i + 1
        elif out_start > 0:
            # First non-header line after a header block — stop.
            break
    return "\n".join(lines[out_start:]).strip()


class EmailMessagesPlugin:
    UNIT_TYPE = UNIT_TYPE

    def matches(self, file_meta: FileMeta) -> bool:
        if (file_meta.mime_type or "").lower() == "message/rfc822":
            return True
        # `email_thread` doc-type classification — covers cases where a
        # .txt or .md file holds a pasted email digest. Cheaper than
        # blanket-matching all prose.
        return (file_meta.inferred_doc_type or "").lower() == "email_thread"

    async def extract(
        self,
        *,
        file_meta: FileMeta,
        doc_text: str,
        raw_pages: list[tuple[int, str, dict]],
    ) -> list[AtomicUnit]:
        if not raw_pages:
            return []

        # Email parser stores the whole body on page 1, with headers on
        # layout_json. For multi-page assemblages (rare), concatenate.
        body_parts: list[str] = []
        top_layout: dict[str, Any] = {}
        for idx, (_page_num, page_text, layout) in enumerate(raw_pages):
            if page_text:
                body_parts.append(page_text)
            if idx == 0 and isinstance(layout, dict):
                top_layout = layout
        full_body = "\n\n".join(body_parts)

        segments = _split_thread(full_body)
        if not segments:
            return []

        top_headers = (top_layout or {}).get("headers") or {}

        units: list[AtomicUnit] = []
        for msg_idx, segment in enumerate(segments):
            params: dict[str, Any] = {"message_index": msg_idx}

            if msg_idx == 0:
                # Top message → headers from the parsed .eml envelope.
                if isinstance(top_headers, dict):
                    if top_headers.get("from"):
                        params["sender"] = top_headers["from"]
                    if top_headers.get("subject"):
                        params["subject"] = top_headers["subject"]
                    if top_headers.get("date"):
                        params["date"] = top_headers["date"]
                    if top_headers.get("to"):
                        recipients = top_headers["to"]
                        if isinstance(recipients, list):
                            params["recipients"] = recipients
                        else:
                            params["recipients"] = [str(recipients)]
                body_for_preview = segment
            else:
                # Quoted older message → re-derive headers from segment text.
                derived = _derive_headers(segment)
                params.update(derived)
                body_for_preview = _strip_header_block(segment)

            preview = (body_for_preview or "").strip()
            if preview:
                # 300 chars: long enough to be useful in retrieval, short
                # enough that the JSON blob doesn't bloat atomic_units
                # rows for a 50-message thread digest.
                params["body_preview"] = preview[:300]
                # Stash the full text so the anomaly scorer can weigh
                # message length against the corpus distribution.
                params["body_length"] = len(preview)
                # Summary doubles as the source-resolver target.
                params["summary"] = preview[:300]

            units.append(AtomicUnit(
                unit_type=UNIT_TYPE,
                parameters=params,
            ))
        return units


PLUGIN = EmailMessagesPlugin()
