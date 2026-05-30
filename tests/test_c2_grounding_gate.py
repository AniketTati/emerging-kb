"""C2 — grounding refusal gate.

`grounding_gate_refuses` makes the CRAG (relevance) and faithfulness gates
AGREE: a low_confidence answer that retrieval also didn't support
(CRAG < threshold) is an out-of-corpus / false-premise hallucination and
must be refused — while a low_confidence answer backed by strong retrieval
(a real paraphrase) still ships.
"""

from __future__ import annotations

from kb.query.orchestrator import grounding_gate_refuses

THRESH = 0.5


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
