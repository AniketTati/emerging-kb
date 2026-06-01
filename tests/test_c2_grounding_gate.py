"""C2 — grounding refusal gate.

`grounding_gate_refuses` makes the CRAG (relevance) and faithfulness gates
AGREE: a low_confidence answer that retrieval also didn't support
(CRAG < threshold) is an out-of-corpus / false-premise hallucination and
must be refused — while a low_confidence answer backed by strong retrieval
(a real paraphrase) still ships.
"""

from __future__ import annotations

from kb.query.orchestrator import (
    grounding_gate_refuses,
    keep_low_confidence_answer_visible,
)

THRESH = 0.5


def _keep(mode, verdict, crag, has_content=True):
    return keep_low_confidence_answer_visible(
        mode=mode, faithfulness_verdict=verdict,
        crag_score=crag, answer_has_content=has_content,
    )


def test_synthesis_mode_low_confidence_stays_visible():
    # G-mode (workspace summary) carries a structurally-low CRAG; a
    # low_confidence verdict must NOT be escalated to a hidden refusal.
    assert _keep("G", "low_confidence", 0.0) is True
    assert _keep("S", "low_confidence", 0.0) is True
    assert _keep("T", "low_confidence", 0.2) is True


def test_h_mode_low_confidence_weak_crag_still_refuses():
    # H-mode out-of-corpus: low_confidence + weak CRAG stays a refusal.
    assert _keep("H", "low_confidence", 0.0) is False


def test_strong_crag_keeps_visible_any_mode():
    assert _keep("H", "refused", 0.8) is True
    assert _keep("G", "low_confidence", 0.9) is True


def test_genuine_refused_verdict_not_softened_by_synthesis_branch():
    # A non-H mode whose gate said 'refused' (real hallucination) + weak
    # CRAG is NOT kept visible — only the high-CRAG branch can override that.
    assert _keep("T", "refused", 0.0) is False
    assert _keep("G", "refused", 0.49) is False


def test_no_content_never_visible():
    assert _keep("G", "low_confidence", 0.9, has_content=False) is False


def test_refused_verdict_always_refuses():
    assert grounding_gate_refuses("refused", 0.0, THRESH) is True
    assert grounding_gate_refuses("refused", 0.9, THRESH) is True


def test_low_confidence_with_weak_retrieval_refuses():
    # out-of-corpus: low_confidence + CRAG below threshold (q027: 0.0/0.19)
    assert grounding_gate_refuses("low_confidence", 0.0, THRESH) is True
    assert grounding_gate_refuses("low_confidence", 0.49, THRESH) is True


def test_low_confidence_with_strong_retrieval_ships():
    # paraphrase of a real passage: retrieval supported it → keep the answer
    assert grounding_gate_refuses("low_confidence", 0.8, THRESH) is False


def test_low_confidence_at_threshold_does_not_refuse():
    # q009 boundary: CRAG exactly at the neutral default (0.5 == 0.5).
    # Strict `<` means we do NOT force-refuse here — catching entity-
    # substitution at the default needs grounded relevance, not a threshold.
    assert grounding_gate_refuses("low_confidence", 0.5, THRESH) is False


def test_pass_and_skipped_never_refuse():
    assert grounding_gate_refuses("pass", 0.0, THRESH) is False
    assert grounding_gate_refuses("skipped", 0.0, THRESH) is False
    assert grounding_gate_refuses(None, 0.0, THRESH) is False
