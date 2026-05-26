"""B3 / WA-7 + WA-8 — pure-function unit tests.

Covers:
  - kb.query.citations: pick_modality + build_ref + build_citation for
    each modality; FileMetaForCitation enrichment; distinct_modalities
  - kb.query.faithfulness: split_sentences, verdict_from_score,
    should_regenerate, IdentityFaithfulnessGate, HeuristicFaithfulnessGate,
    factory dispatch, MAX_REGENERATIONS contract
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from kb.query.citations import (
    CITATION_MODALITIES,
    FileMetaForCitation,
    RichCitation,
    build_citation,
    build_ref,
    distinct_modalities,
    pick_modality,
)
from kb.query.faithfulness import (
    FAITHFULNESS_VERDICTS,
    LOW_CONFIDENCE_THRESHOLD,
    MAX_REGENERATIONS,
    PASS_THRESHOLD,
    HeuristicFaithfulnessGate,
    HHEMFaithfulnessGate,
    IdentityFaithfulnessGate,
    make_faithfulness_gate,
    should_regenerate,
    split_sentences,
    verdict_from_score,
)
from kb.query.rrf import Hit


pytestmark = pytest.mark.asyncio


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
# Helpers
# ===========================================================================


def _hit(
    *,
    id: str = "h1",
    kind: str = "chunk",
    score: float = 0.5,
    snippet: str = "snippet text",
    **md,
) -> Hit:
    return Hit(id=id, kind=kind, score=score, snippet=snippet, metadata=md)


def _meta(
    *,
    file_id: str = "f1",
    mime_type: str | None = "application/pdf",
    inferred_doc_type: str | None = None,
    name: str | None = "doc.pdf",
    source_authority: float | None = 0.7,
    doc_status: str | None = "live",
    chain_id: str | None = None,
) -> FileMetaForCitation:
    return FileMetaForCitation(
        file_id=file_id,
        mime_type=mime_type,
        inferred_doc_type=inferred_doc_type,
        name=name,
        source_authority=source_authority,
        doc_status=doc_status,
        chain_id=chain_id,
    )


# ===========================================================================
# Constants
# ===========================================================================


def test_citation_modalities_are_design_5_twelve():
    assert set(CITATION_MODALITIES) == {
        "pdf_span", "pdf_bbox",
        "xlsx_row", "xlsx_cell",
        "image_bbox", "ocr_span",
        "email_message",
        "raptor_summary", "aggregate", "atomic_unit",
        "entity_ref", "chain_ref",
    }


def test_faithfulness_verdicts_are_four_states():
    assert set(FAITHFULNESS_VERDICTS) == {
        "pass", "low_confidence", "refused", "skipped",
    }


def test_threshold_invariants():
    assert 0.0 < LOW_CONFIDENCE_THRESHOLD < PASS_THRESHOLD <= 1.0
    assert MAX_REGENERATIONS >= 1


# ===========================================================================
# pick_modality — precedence
# ===========================================================================


def test_pick_modality_atomic_unit_wins_over_pdf_span():
    h = _hit(kind="atomic_unit", file_id="f1", unit_type="clause")
    assert pick_modality(h, _meta()) == "atomic_unit"


def test_pick_modality_raptor_node_returns_raptor_summary():
    h = _hit(kind="raptor_node", level=2)
    assert pick_modality(h, _meta()) == "raptor_summary"


def test_pick_modality_xlsx_chunk_returns_xlsx_row():
    h = _hit(kind="chunk", file_id="f1")
    meta = _meta(
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        name="vendors.xlsx",
    )
    assert pick_modality(h, meta) == "xlsx_row"


def test_pick_modality_email_chunk_returns_email_message():
    h = _hit(kind="chunk")
    meta = _meta(mime_type="message/rfc822", name="thread.eml")
    assert pick_modality(h, meta) == "email_message"


def test_pick_modality_email_doctype_classification():
    """Email classification flag also triggers email_message."""
    h = _hit(kind="chunk")
    meta = _meta(mime_type="text/plain", inferred_doc_type="email")
    assert pick_modality(h, meta) == "email_message"


def test_pick_modality_entity_ref_when_matched_mention_present():
    """mentions_exact channel sets matched_mention metadata."""
    h = _hit(kind="chunk", matched_mention="ACME Corp", entity_id="ent-1")
    assert pick_modality(h, _meta()) == "entity_ref"


def test_pick_modality_aggregate_when_explicit_flag():
    h = _hit(kind="chunk", aggregate=True)
    assert pick_modality(h, _meta()) == "aggregate"


def test_pick_modality_falls_back_to_pdf_span_for_plain_pdf_chunk():
    h = _hit(kind="chunk", file_id="f1")
    assert pick_modality(h, _meta()) == "pdf_span"


def test_pick_modality_none_meta_safe():
    """No file_meta → still returns a modality (pdf_span fallback)."""
    h = _hit(kind="chunk")
    assert pick_modality(h, None) == "pdf_span"


# ===========================================================================
# build_ref — shape per modality
# ===========================================================================


def test_pdf_span_ref_extracts_first_page_from_source_page_numbers():
    h = _hit(source_page_numbers=[7, 8], char_start=1248, char_end=1392)
    ref = build_ref("pdf_span", h, _meta())
    # R2 — `source_chunk_id` added to ref shape (None when the resolver
    # didn't find a narrower span). The char_start/end from md are
    # honored as the fallback when source_char_start/end isn't set.
    assert ref == {
        "page": 7, "char_start": 1248, "char_end": 1392,
        "source_chunk_id": None,
    }


def test_pdf_span_ref_prefers_source_char_range_over_md_char_range():
    """R2 — when the worker's source-resolver located a narrower span
    inside the chunk (PR2), prefer it. md.char_start is the whole-chunk
    bound; source_char_start is the exact extracted-text bound."""
    h = _hit(
        source_page_numbers=[3],
        char_start=0, char_end=2400,
        source_chunk_id="c1-uuid",
        source_char_start=410, source_char_end=420,
    )
    ref = build_ref("pdf_span", h, _meta())
    assert ref == {
        "page": 3, "char_start": 410, "char_end": 420,
        "source_chunk_id": "c1-uuid",
    }


def test_xlsx_row_ref_carries_sheet_and_row():
    h = _hit(sheet_name="Q2 Vendors", row_index=482, row_hash="0xabc")
    ref = build_ref("xlsx_row", h, _meta())
    assert ref["sheet"] == "Q2 Vendors"
    assert ref["row_index"] == 482
    assert ref["row_hash"] == "0xabc"
    assert ref["key_cols"] == {}


def test_atomic_unit_ref_uses_hit_id_as_unit_id():
    h = _hit(id="u-42", kind="atomic_unit", unit_type="clause", file_id="f1")
    ref = build_ref("atomic_unit", h, _meta(file_id="f1"))
    assert ref["unit_id"] == "u-42"
    assert ref["unit_type"] == "clause"
    assert ref["doc_id"] == "f1"


def test_raptor_summary_ref_carries_node_id_and_level():
    h = _hit(id="node-3", kind="raptor_node", level=2, scope="per_doc")
    ref = build_ref("raptor_summary", h, _meta())
    assert ref["node_id"] == "node-3"
    assert ref["level"] == 2
    assert ref["scope"] == "per_doc"


def test_email_message_ref_falls_back_to_chain_id_when_no_thread_id():
    h = _hit(message_id="msg-1", char_start=0, char_end=384)
    meta = _meta(chain_id="ch-1")
    ref = build_ref("email_message", h, meta)
    assert ref["thread_id"] == "ch-1"
    assert ref["message_id"] == "msg-1"


def test_entity_ref_carries_alias_used_from_matched_mention():
    h = _hit(matched_mention="M. Ambani", entity_id="ent-1")
    ref = build_ref("entity_ref", h, _meta())
    assert ref["entity_id"] == "ent-1"
    assert ref["alias_used"] == "M. Ambani"


def test_chain_ref_pulls_chain_id_from_meta():
    h = _hit()
    meta = _meta(chain_id="ch-99")
    ref = build_ref("chain_ref", h, meta)
    assert ref["chain_id"] == "ch-99"


def test_unknown_modality_falls_back_to_pdf_span_builder():
    """Forward-compat: an invented future modality won't crash."""
    h = _hit(source_page_numbers=[3])
    ref = build_ref("future_modality_xyz", h, _meta())
    # falls back to _pdf_span_ref shape
    assert "page" in ref
    assert ref["page"] == 3


# ===========================================================================
# build_citation — full envelope
# ===========================================================================


def test_build_citation_default_pdf_span_envelope():
    h = _hit(file_id="f1", source_page_numbers=[7])
    meta = _meta(source_authority=0.85, doc_status="live", chain_id="c1")
    c = build_citation(h, meta)
    assert isinstance(c, RichCitation)
    assert c.modality == "pdf_span"
    assert c.file_id == "f1"
    assert c.authority == 0.85
    assert c.doc_status == "live"
    assert c.chain_id == "c1"
    assert c.ref["page"] == 7


def test_build_citation_atomic_unit_confidence_uses_score():
    h = _hit(id="u-1", kind="atomic_unit", score=0.62, unit_type="clause")
    c = build_citation(h, _meta())
    assert c.modality == "atomic_unit"
    assert c.confidence == pytest.approx(0.62)


def test_build_citation_xlsx_row_confidence_is_1_per_design():
    h = _hit(kind="chunk", score=0.42, sheet_name="A", row_index=1)
    meta = _meta(
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    c = build_citation(h, meta)
    assert c.modality == "xlsx_row"
    assert c.confidence == 1.0


def test_build_citation_no_meta_still_emits_envelope():
    h = _hit(kind="chunk", file_id="f1", source_page_numbers=[2])
    c = build_citation(h, None)
    assert c.file_id == "f1"
    assert c.modality == "pdf_span"
    assert c.authority is None
    assert c.ref["page"] == 2


def test_build_citation_dict_payload_serializable():
    h = _hit(kind="chunk", file_id="f1")
    c = build_citation(h, _meta())
    payload = c.to_dict()
    # Keys present
    for k in [
        "hit_id", "kind", "file_id", "snippet_preview", "score",
        "modality", "ref", "label", "authority", "doc_status",
        "chain_id", "confidence",
    ]:
        assert k in payload


# ===========================================================================
# distinct_modalities
# ===========================================================================


def test_distinct_modalities_preserves_order():
    cs = [
        RichCitation("h1", "chunk", "f1", "s", 0.1, "pdf_span", {}),
        RichCitation("h2", "atomic_unit", "f1", "s", 0.2, "atomic_unit", {}),
        RichCitation("h3", "chunk", "f1", "s", 0.3, "pdf_span", {}),  # dup
        RichCitation("h4", "chunk", "f2", "s", 0.4, "xlsx_row", {}),
    ]
    assert distinct_modalities(cs) == ["pdf_span", "atomic_unit", "xlsx_row"]


def test_distinct_modalities_empty():
    assert distinct_modalities([]) == []


# ===========================================================================
# Faithfulness — split_sentences
# ===========================================================================


def test_split_sentences_basic_terminal_punct():
    out = split_sentences("First claim. Second claim! Third?")
    assert out == ["First claim.", "Second claim!", "Third?"]


def test_split_sentences_handles_quoted_starts():
    out = split_sentences('First. "Second one."')
    assert out == ['First.', '"Second one."']


def test_split_sentences_no_terminal_punct_returns_whole_text():
    out = split_sentences("just a fragment")
    assert out == ["just a fragment"]


def test_split_sentences_empty_returns_empty_list():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ===========================================================================
# Faithfulness — verdict_from_score
# ===========================================================================


def test_verdict_from_score_pass_at_threshold():
    assert verdict_from_score(PASS_THRESHOLD) == "pass"
    assert verdict_from_score(0.99) == "pass"


def test_verdict_from_score_low_confidence_band():
    assert verdict_from_score(LOW_CONFIDENCE_THRESHOLD) == "low_confidence"
    assert verdict_from_score(0.6) == "low_confidence"


def test_verdict_from_score_refused_below_band():
    assert verdict_from_score(0.0) == "refused"
    assert verdict_from_score(LOW_CONFIDENCE_THRESHOLD - 0.01) == "refused"


# ===========================================================================
# Faithfulness — should_regenerate
# ===========================================================================


def test_should_regenerate_only_for_refused_with_retries_left():
    assert should_regenerate("refused", 0) is True
    assert should_regenerate("refused", MAX_REGENERATIONS - 1) is True
    assert should_regenerate("refused", MAX_REGENERATIONS) is False


def test_should_regenerate_never_for_pass_or_low_confidence():
    assert should_regenerate("pass", 0) is False
    assert should_regenerate("low_confidence", 0) is False
    assert should_regenerate("skipped", 0) is False


# ===========================================================================
# Faithfulness gates — Identity
# ===========================================================================


async def test_identity_gate_always_passes_when_answer_present():
    g = IdentityFaithfulnessGate()
    r = await g.assess("any answer", ["any snippet"])
    assert r.verdict == "pass"
    assert r.score == 1.0
    assert r.model_id == "identity"


async def test_identity_gate_skips_empty_answer():
    g = IdentityFaithfulnessGate()
    r = await g.assess("", [])
    assert r.verdict == "skipped"


# ===========================================================================
# Faithfulness gates — Heuristic
# ===========================================================================


async def test_heuristic_gate_full_overlap_passes():
    g = HeuristicFaithfulnessGate()
    r = await g.assess(
        "Indemnification cap is twenty five million dollars.",
        ["The indemnification cap is twenty five million dollars."],
    )
    assert r.verdict == "pass"
    assert r.score >= PASS_THRESHOLD


async def test_heuristic_gate_no_overlap_refused():
    g = HeuristicFaithfulnessGate()
    r = await g.assess(
        "Pineapple unicorns dance silently.",
        ["The indemnification cap is twenty five million dollars."],
    )
    assert r.verdict == "refused"


async def test_heuristic_gate_no_snippets_refused():
    g = HeuristicFaithfulnessGate()
    r = await g.assess("Some answer.", [])
    assert r.verdict == "refused"
    assert r.notes is not None and "no citation" in r.notes


async def test_heuristic_gate_empty_answer_skipped():
    g = HeuristicFaithfulnessGate()
    r = await g.assess("", ["snippet"])
    assert r.verdict == "skipped"


async def test_heuristic_gate_per_claim_scores_returned():
    g = HeuristicFaithfulnessGate()
    r = await g.assess(
        "Cap is twenty five million. Term is five years.",
        ["The cap is twenty five million dollars."],
    )
    assert len(r.per_claim_scores) == 2
    # First sentence has high overlap, second has low (no 'term' / 'years').
    assert r.per_claim_scores[0] > r.per_claim_scores[1]


async def test_heuristic_gate_neutral_for_no_content_words():
    """A sentence made of only stop-words ('Is it the?') has no content
    tokens after filtering → per-claim 1.0 (neutral, no claim to verify)."""
    g = HeuristicFaithfulnessGate()
    r = await g.assess("Is it the?", ["any snippet"])
    assert r.per_claim_scores[0] == 1.0


# ===========================================================================
# Faithfulness gates — HHEM (no model, just constructor + skipped path)
# ===========================================================================


async def test_hhem_gate_skipped_for_empty_answer():
    g = HHEMFaithfulnessGate(model=None)
    r = await g.assess("", ["snippet"])
    assert r.verdict == "skipped"


async def test_hhem_gate_refused_when_no_snippets():
    """No snippets to ground against → refused without invoking the model."""
    g = HHEMFaithfulnessGate(model=None)
    r = await g.assess("some answer", [])
    assert r.verdict == "refused"


async def test_hhem_gate_with_injected_predictor():
    """Inject a fake model with .predict() to exercise the happy path."""
    class FakeModel:
        def predict(self, pairs):
            return [0.9 for _ in pairs]

    g = HHEMFaithfulnessGate(model=FakeModel())
    r = await g.assess(
        "Cap is twenty five million.",
        ["The cap is twenty five million dollars."],
    )
    assert r.verdict == "pass"
    assert r.score == pytest.approx(0.9)


async def test_hhem_gate_fail_safe_when_predict_throws():
    class BrokenModel:
        def predict(self, pairs):
            raise RuntimeError("model crashed")

    g = HHEMFaithfulnessGate(model=BrokenModel())
    r = await g.assess("answer", ["snippet"])
    assert r.verdict == "pass"
    assert r.notes is not None and "predict failed" in r.notes


# ===========================================================================
# Faithfulness factory
# ===========================================================================


def test_factory_default_is_identity():
    with _env(KB_FAITHFULNESS_GATE=None):
        g = make_faithfulness_gate()
        assert isinstance(g, IdentityFaithfulnessGate)


def test_factory_auto_is_identity():
    with _env(KB_FAITHFULNESS_GATE="auto"):
        g = make_faithfulness_gate()
        assert isinstance(g, IdentityFaithfulnessGate)


def test_factory_heuristic():
    with _env(KB_FAITHFULNESS_GATE="heuristic"):
        g = make_faithfulness_gate()
        assert isinstance(g, HeuristicFaithfulnessGate)


def test_factory_hhem_constructs_without_loading_model():
    """The factory must NOT load the 600MB checkpoint at import time."""
    with _env(KB_FAITHFULNESS_GATE="hhem"):
        g = make_faithfulness_gate()
        assert isinstance(g, HHEMFaithfulnessGate)


def test_factory_unknown_raises():
    with _env(KB_FAITHFULNESS_GATE="bogus"):
        with pytest.raises(ValueError):
            make_faithfulness_gate()
