"""WA-3 / Design 3 — doc-chain detector unit tests (pure-function)."""

from __future__ import annotations

import pytest

from kb.extraction.doc_chains import (
    ChainCandidate,
    DetectionInput,
    SiblingFile,
    _detect_circular_corrigendum,
    _detect_contract_chain,
    _detect_drawing_revision,
    _detect_email_thread,
    _has_amendment_language,
    _jaccard_similarity,
    _normalize_contract_title,
    _normalize_subject,
    _parse_revision_filename,
    _sender_recipient_overlap,
    detect_chain,
)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("Re: Q3 forecast", "q3 forecast"),
    ("Fwd: Re: Re: deal terms", "deal terms"),
    ("[EXTERNAL] Re: invoice", "invoice"),
    ("AW: einen Vorschlag", "einen vorschlag"),
    ("normal subject", "normal subject"),
    (None, ""),
])
def test_normalize_subject(raw, expected):
    assert _normalize_subject(raw) == expected


@pytest.mark.parametrize("raw,expected_contains", [
    ("Vendor XYZ Supply Agreement Amendment 2", "vendor xyz supply agreement"),
    ("Vendor XYZ Supply Agreement v3", "vendor xyz supply agreement"),
    ("Vendor XYZ Supply Agreement Side Letter 2024", "vendor xyz supply agreement"),
])
def test_normalize_contract_title_strips_amendment_markers(raw, expected_contains):
    out = _normalize_contract_title(raw)
    assert expected_contains in out
    for marker in ("amendment", "side letter", "v3", "version", "2024"):
        assert marker not in out


def test_jaccard_basic():
    assert _jaccard_similarity("a b c", "a b d") == pytest.approx(0.5)
    assert _jaccard_similarity("a b c", "a b c") == 1.0
    assert _jaccard_similarity("a b", "c d") == 0.0
    assert _jaccard_similarity("", "anything") == 0.0


def test_has_amendment_language():
    assert _has_amendment_language("This Amendment No. 2 to the Original ...") is True
    assert _has_amendment_language("This side letter modifies ...") is True
    assert _has_amendment_language("Regular contract text, nothing special.") is False
    assert _has_amendment_language(None) is False


@pytest.mark.parametrize("name,expected", [
    ("drawing_C7_RevA.pdf", ("drawing_c7", "A")),
    ("Drawing-C7-v2.dwg", ("drawing-c7", "2")),
    ("plan_R03.pdf", ("plan", "03")),
    ("design Rev 1.pdf", ("design", "1")),
    ("contract.pdf", None),
    ("", None),
])
def test_parse_revision_filename(name, expected):
    assert _parse_revision_filename(name) == expected


def test_sender_recipient_overlap():
    a = "alice@x.com"
    arec = ("bob@x.com", "carol@x.com")
    b = "bob@x.com"
    brec = ("alice@x.com", "dave@x.com")
    # union = {alice, bob, carol, dave}, intersection = {alice, bob}
    assert _sender_recipient_overlap(a, arec, b, brec) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Email-thread detection
# ---------------------------------------------------------------------------


def test_email_first_in_thread_creates_chain_key_on_message_id():
    result = _detect_email_thread(DetectionInput(
        file_id="f1",
        name="email1.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m1@enron.com>",
        email_subject="Mexico deal",
        siblings=(),
    ))
    assert result is not None
    assert result.chain_type == "email_thread"
    assert result.chain_key == "msgid:<m1@enron.com>"
    assert result.role == "original"
    assert result.version_index == 0


def test_email_without_message_id_does_not_anchor():
    result = _detect_email_thread(DetectionInput(
        file_id="f1",
        name="email.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id=None,
        siblings=(),
    ))
    assert result is None


def test_email_reply_via_in_reply_to_links_to_parent():
    parent = SiblingFile(
        file_id="f-parent",
        name="email1.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m1@enron.com>",
        email_subject="Mexico deal",
        email_sender="alice@enron.com",
        email_recipients=("bob@enron.com",),
    )
    result = _detect_email_thread(DetectionInput(
        file_id="f-reply",
        name="email2.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m2@enron.com>",
        email_in_reply_to="<m1@enron.com>",
        email_subject="Re: Mexico deal",
        siblings=(parent,),
    ))
    assert result is not None
    assert result.role == "reply"
    assert result.parent_doc_id == "f-parent"
    assert result.confidence >= 0.95


def test_email_forward_role_via_subject_prefix():
    parent = SiblingFile(
        file_id="f-parent",
        name="email1.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m1@enron.com>",
        email_subject="Mexico deal",
        email_sender="alice@enron.com",
        email_recipients=("bob@enron.com",),
    )
    result = _detect_email_thread(DetectionInput(
        file_id="f-fwd",
        name="email-fwd.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<mfwd@enron.com>",
        email_in_reply_to="<m1@enron.com>",
        email_subject="Fwd: Mexico deal",
        siblings=(parent,),
    ))
    assert result is not None
    assert result.role == "forward"


def test_email_subject_overlap_fallback_links():
    parent = SiblingFile(
        file_id="f-parent",
        name="email1.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m1@enron.com>",
        email_subject="Q3 forecast",
        email_sender="alice@x.com",
        email_recipients=("bob@x.com",),
    )
    result = _detect_email_thread(DetectionInput(
        file_id="f-related",
        name="email2.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m2@x.com>",
        email_subject="Re: Q3 forecast",
        email_sender="bob@x.com",            # was recipient before
        email_recipients=("alice@x.com",),   # was sender before — 100% overlap
        siblings=(parent,),
    ))
    assert result is not None
    assert result.role == "reply"
    # Subject-overlap fallback hits the 0.75 confidence band.
    assert 0.7 <= result.confidence <= 0.9


def test_email_ignores_non_email_mime():
    assert _detect_email_thread(DetectionInput(
        file_id="f1",
        name="contract.pdf",
        mime_type="application/pdf",
        inferred_doc_type="contract",
    )) is None


# ---------------------------------------------------------------------------
# Contract-chain detection
# ---------------------------------------------------------------------------


def test_contract_chain_links_amendment_to_original():
    original = SiblingFile(
        file_id="f-orig",
        name="vertex_supply.pdf",
        mime_type="application/pdf",
        inferred_doc_type="executed_contract",
        title_text="Vertex Logistics Supply Agreement",
    )
    result = _detect_contract_chain(DetectionInput(
        file_id="f-amend",
        name="vertex_supply_amendment_2.pdf",
        mime_type="application/pdf",
        inferred_doc_type="executed_contract",
        title_text="This Amendment No. 2 to the Vertex Logistics Supply Agreement",
        siblings=(original,),
    ))
    assert result is not None
    assert result.chain_type == "contract_chain"
    assert result.role == "amendment"
    assert result.sibling_member_ids == ("f-orig",)
    assert result.confidence >= 0.7


def test_contract_chain_side_letter_role():
    original = SiblingFile(
        file_id="f-orig",
        name="enron_epe.pdf",
        mime_type="application/pdf",
        inferred_doc_type="executed_contract",
        title_text="Enron Power Supply Agreement",
    )
    result = _detect_contract_chain(DetectionInput(
        file_id="f-sl",
        name="enron_epe_side_letter.pdf",
        mime_type="application/pdf",
        inferred_doc_type="executed_contract",
        title_text="Enron Power Supply Side Letter",
        siblings=(original,),
    ))
    assert result is not None
    assert result.role == "side_letter"


def test_contract_chain_no_match_when_titles_disjoint():
    sib = SiblingFile(
        file_id="f-other",
        name="other.pdf",
        mime_type="application/pdf",
        inferred_doc_type="executed_contract",
        title_text="Indemnification Side Agreement Alpha",
    )
    result = _detect_contract_chain(DetectionInput(
        file_id="f-self",
        name="unrelated.pdf",
        mime_type="application/pdf",
        inferred_doc_type="executed_contract",
        title_text="Generic Procurement Master Service Agreement",
        siblings=(sib,),
    ))
    assert result is None


def test_contract_chain_skipped_for_non_contract_doc_types():
    assert _detect_contract_chain(DetectionInput(
        file_id="f-self",
        name="random.pdf",
        mime_type="application/pdf",
        inferred_doc_type="invoice",
        title_text="something",
        siblings=(),
    )) is None


# ---------------------------------------------------------------------------
# Drawing-revision detection
# ---------------------------------------------------------------------------


def test_drawing_revision_links_two_revs_of_same_base():
    rev1 = SiblingFile(
        file_id="f-r1",
        name="bldg_C7_RevA.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
        project_id="proj-1",
    )
    result = _detect_drawing_revision(DetectionInput(
        file_id="f-r2",
        name="bldg_C7_RevB.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
        project_id="proj-1",
        siblings=(rev1,),
    ))
    assert result is not None
    assert result.chain_type == "drawing_revisions"
    assert result.role == "revision"
    assert result.sibling_member_ids == ("f-r1",)


def test_drawing_revision_first_in_chain_still_creates_singleton():
    """A single revision-pattern file creates a chain (future revs join)."""
    result = _detect_drawing_revision(DetectionInput(
        file_id="f-solo",
        name="plan_R01.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
        siblings=(),
    ))
    assert result is not None
    assert result.role == "revision"
    assert result.sibling_member_ids == ()


def test_drawing_revision_rev_token_translates_to_version_index():
    result = _detect_drawing_revision(DetectionInput(
        file_id="f-r3",
        name="plan_R03.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
        siblings=(),
    ))
    assert result is not None
    assert result.version_index == 3


def test_drawing_revision_ignores_non_pattern_filename():
    assert _detect_drawing_revision(DetectionInput(
        file_id="f1",
        name="bldg.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
    )) is None


def test_drawing_revision_skipped_when_project_id_mismatch():
    rev1 = SiblingFile(
        file_id="f-r1",
        name="bldg_C7_RevA.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
        project_id="proj-1",
    )
    result = _detect_drawing_revision(DetectionInput(
        file_id="f-r2",
        name="bldg_C7_RevB.pdf",
        mime_type="application/pdf",
        inferred_doc_type="drawing",
        project_id="proj-2",   # different project!
        siblings=(rev1,),
    ))
    # Still a chain (own singleton), but with no siblings (project mismatch
    # disqualifies the sibling match).
    assert result is not None
    assert result.sibling_member_ids == ()


# ---------------------------------------------------------------------------
# Circular / corrigendum detection
# ---------------------------------------------------------------------------


def test_circular_corrigendum_attaches_to_original():
    original = SiblingFile(
        file_id="f-orig",
        name="gr-2025-001.pdf",
        mime_type="application/pdf",
        inferred_doc_type="circular",
        title_text="Government Circular GR 2025-001 — Vendor Onboarding",
    )
    result = _detect_circular_corrigendum(DetectionInput(
        file_id="f-corr",
        name="gr-2025-001-corr.pdf",
        mime_type="application/pdf",
        inferred_doc_type="circular",
        title_text="Corrigendum to GR 2025-001 — Vendor Onboarding",
        siblings=(original,),
    ))
    assert result is not None
    assert result.chain_type == "circular_chain"
    assert result.role == "corrigendum"


def test_circular_corrigendum_no_header_returns_none():
    result = _detect_circular_corrigendum(DetectionInput(
        file_id="f1",
        name="random.pdf",
        mime_type="application/pdf",
        inferred_doc_type="circular",
        title_text="Random Title",
    ))
    assert result is None


# ---------------------------------------------------------------------------
# Orchestration — first confident match wins
# ---------------------------------------------------------------------------


def test_detect_chain_returns_email_when_applicable():
    result = detect_chain(DetectionInput(
        file_id="f1",
        name="email.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m1@x.com>",
        email_subject="hello",
    ))
    assert result is not None
    assert result.chain_type == "email_thread"


def test_detect_chain_returns_none_when_no_rule_matches():
    """A random unmatched PDF gets no chain."""
    result = detect_chain(DetectionInput(
        file_id="f1",
        name="random.pdf",
        mime_type="application/pdf",
        inferred_doc_type="report",
        title_text="Q3 earnings highlights",
    ))
    assert result is None


def test_detect_chain_priority_email_beats_contract():
    """An email whose subject happens to look contract-y should match
    the email detector first (higher priority)."""
    result = detect_chain(DetectionInput(
        file_id="f1",
        name="email.eml",
        mime_type="message/rfc822",
        inferred_doc_type="email",
        email_message_id="<m1@x.com>",
        email_subject="Vertex Logistics Supply Agreement (executed)",
    ))
    assert result is not None
    assert result.chain_type == "email_thread"
