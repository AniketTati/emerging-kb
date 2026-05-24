"""Phase 2c — parse-output quality scoring + escalation decision.

Pure functions; no DB, no LLM, no async. Used by the worker
(`parse_file_impl`) to decide whether Docling's output is good enough or
needs Gemini OCR re-parse.

Per build_tracker §5.6.1 #10 (three escalation signals) + #12 (provenance
JSON shape).
"""

from __future__ import annotations

from typing import Any

from kb.parsers import ParsedDocument


# ---------------------------------------------------------------------------
# Tuning constants (could become env-configurable later; demo defaults here)
# ---------------------------------------------------------------------------

PRINTABLE_RATIO_THRESHOLD = 0.7   # below this → garbled, escalate
HYBRID_BAD_PAGE_CHAR_FLOOR = 5    # page below this is "empty"
HYBRID_PEER_GOOD_CHAR_FLOOR = 100 # peer above this is "good"


def _printable_ratio(text: str) -> float:
    if not text:
        return 1.0  # empty handled separately; ratio undefined here
    # Printable = chars that survive ascii or are in the unicode printable
    # ranges. Control chars (\x00-\x08, \x0b-\x0c, \x0e-\x1f) count as
    # non-printable. Whitespace (\t \n \r) counts as printable.
    printable = sum(
        1 for ch in text
        if ch.isprintable() or ch in ("\t", "\n", "\r", " ")
    )
    return printable / len(text)


def score_parse_quality(parsed: ParsedDocument) -> float:
    """Single quality score in [0.0, 1.0]. Used for provenance audit + as
    a coarse signal in `should_escalate`. Weights:
    - average printable ratio across pages with content
    - non-empty page ratio (pages with > 0 chars / total pages)
    """
    if not parsed.pages:
        return 0.0
    non_empty_pages = [p for p in parsed.pages if (p.text or "").strip()]
    if not non_empty_pages:
        return 0.0
    avg_printable = sum(
        _printable_ratio(p.text) for p in non_empty_pages
    ) / len(non_empty_pages)
    non_empty_ratio = len(non_empty_pages) / len(parsed.pages)
    # Geometric mean — both signals must be high for a high score.
    return (avg_printable * non_empty_ratio) ** 0.5


def should_escalate(parsed: ParsedDocument) -> tuple[bool, str]:
    """Decide whether the whole-doc Docling output needs Gemini OCR re-parse.

    Signal 1: every page is empty (total chars across all pages == 0).
    Signal 2: extracted text exists but is mostly non-printable (garbled OCR).

    Returns `(should_escalate, reason)`. Reason is human-readable, intended
    for the lifecycle event payload.
    """
    if not parsed.pages:
        return True, "no pages in ParsedDocument"

    total_chars = sum(len((p.text or "").strip()) for p in parsed.pages)
    if total_chars == 0:
        return True, "empty output: no text on any page (every page <= whitespace)"

    # Garbled detection: combine all page text, check printable ratio.
    combined = "".join(p.text or "" for p in parsed.pages)
    ratio = _printable_ratio(combined)
    if ratio < PRINTABLE_RATIO_THRESHOLD:
        return True, (
            f"garbled output: printable_ratio={ratio:.2f} "
            f"(threshold={PRINTABLE_RATIO_THRESHOLD})"
        )

    return False, "quality_ok"


def escalate_per_page(parsed: ParsedDocument) -> list[int]:
    """Identify the subset of pages that need per-page Gemini OCR re-parse.

    Hybrid PDF case: most pages are digital (Docling extracted >100 chars),
    one page is a scan (Docling extracted <5 chars). Re-OCR only the bad
    page(s); keep the good Docling output for the rest.

    Returns 1-indexed page numbers that should be re-OCR'd. Empty list
    means no per-page escalation needed.
    """
    if not parsed.pages:
        return []

    page_chars = [(p.page_number, len((p.text or "").strip())) for p in parsed.pages]
    has_good_peer = any(c >= HYBRID_PEER_GOOD_CHAR_FLOOR for _, c in page_chars)
    if not has_good_peer:
        # No good peer → either all empty (covered by `should_escalate`'s
        # signal 1) or all weak (probably not worth per-page escalation;
        # whole-doc escalation is cleaner).
        return []

    return [
        page_number for page_number, c in page_chars
        if c < HYBRID_BAD_PAGE_CHAR_FLOOR
    ]


def build_provenance(
    *,
    strategy: str,
    forced_parser: str | None,
    tried: list[str],
    chose: str,
    reason: str,
    quality_score: float | None,
) -> dict[str, Any]:
    """Construct the dict that goes into `raw_pages.layout_json.provenance`.

    Shape locked at §5.6.1 #12. Dashboards filter rows by
    `layout_json->'provenance'->>'chose'` for cost + provider attribution.
    """
    return {
        "strategy": strategy,
        "forced_parser": forced_parser,
        "tried": list(tried),
        "chose": chose,
        "reason": reason,
        "quality_score": quality_score,
    }
