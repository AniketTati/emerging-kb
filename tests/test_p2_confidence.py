"""P2 — every answer carries one confidence signal (high/medium/low) + a
short reason, derived from the grounding gates (faithfulness + CRAG)."""

from __future__ import annotations

from kb.query.generate import Citation, GenerationResult
from kb.query.orchestrator import ChatResult, derive_answer_confidence

THRESH = 0.5


def test_high_when_grounded_and_confident_retrieval():
    level, reason = derive_answer_confidence(
        refused=False, faithfulness_verdict="pass",
        faithfulness_score=0.9, crag_score=0.8, crag_threshold=THRESH)
    assert level == "high" and reason


def test_low_when_refused():
    level, _ = derive_answer_confidence(
        refused=True, faithfulness_verdict=None,
        faithfulness_score=None, crag_score=0.0, crag_threshold=THRESH)
    assert level == "low"


def test_low_when_ungrounded_and_weak():
    level, _ = derive_answer_confidence(
        refused=False, faithfulness_verdict="low_confidence",
        faithfulness_score=0.2, crag_score=0.1, crag_threshold=THRESH)
    assert level == "low"


def test_medium_when_mixed():
    level, _ = derive_answer_confidence(
        refused=False, faithfulness_verdict="low_confidence",
        faithfulness_score=0.4, crag_score=0.8, crag_threshold=THRESH)
    assert level == "medium"


def test_chatresult_autopopulates_confidence():
    gen = GenerationResult(answer="x", citations=[], refused=False,
                           refusal_reason=None)
    r = ChatResult(query_id="q", query="?", generation=gen,
                   faithfulness_verdict="pass", crag_score=0.9)
    assert r.confidence == "high"
    assert isinstance(r.confidence_reason, str) and r.confidence_reason

    gen2 = GenerationResult(answer="", citations=[], refused=True,
                            refusal_reason="no_hits")
    r2 = ChatResult(query_id="q", query="?", generation=gen2, crag_score=0.0)
    assert r2.confidence == "low"
