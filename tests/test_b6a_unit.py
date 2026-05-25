"""B6a / WA-12 — pure-function unit tests for conversation memory.

Covers:
  - kb.query.context_resolver: heuristic + Identity resolver behavior;
    looks_like_anaphora / refinement; _parse_resolution_json tolerance;
    factory selection
  - kb.domain.chat_memory: ChatContext.to_dict shape; _summarize_answer
    truncation
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from kb.domain.chat_memory import (
    ChatContext,
    DEFAULT_HOT_TURNS,
    _summarize_answer,
)
from kb.query.context_resolver import (
    AnaphoraSubstitution,
    ContextResolution,
    GeminiContextResolver,
    IdentityContextResolver,
    _parse_resolution_json,
    looks_like_anaphora,
    looks_like_refinement,
    make_context_resolver,
)


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
# Constants
# ===========================================================================


def test_default_hot_turns_is_six():
    """MTRAG K=6 saturation point."""
    assert DEFAULT_HOT_TURNS == 6


# ===========================================================================
# ChatContext
# ===========================================================================


def test_chat_context_to_dict_round_trip():
    ctx = ChatContext(
        session_id="s1",
        last_turn_id="t2",
        carry_forward_entities=("e1", "e2"),
        carry_forward_filters={"doc_type": ["contract"]},
        prior_result_set_id=None,
        older_turn_summary="prior chats summarized...",
        last_k_verbatim_turns=(
            {"turn_index": 0, "user_query": "hi", "answer_summary": "hello"},
        ),
    )
    d = ctx.to_dict()
    assert d["session_id"] == "s1"
    assert d["carry_forward_entities"] == ["e1", "e2"]
    assert d["carry_forward_filters"] == {"doc_type": ["contract"]}
    assert d["last_k_verbatim_turns"][0]["user_query"] == "hi"


# ===========================================================================
# _summarize_answer
# ===========================================================================


def test_summarize_answer_preserves_short_answers():
    s = _summarize_answer("Short.")
    assert s == "Short."


def test_summarize_answer_truncates_long_answers():
    answer = "x" * 1000
    s = _summarize_answer(answer)
    assert s is not None
    assert s.endswith("...")
    # max_chars=240 default
    assert len(s) <= 240 + 3


def test_summarize_answer_handles_none():
    assert _summarize_answer(None) is None
    assert _summarize_answer("") is None


# ===========================================================================
# looks_like_anaphora / looks_like_refinement
# ===========================================================================


@pytest.mark.parametrize("query,expected", [
    ("Tell me about his loans.", True),
    ("What did she say?", True),
    ("It seemed important.", True),
    ("The same vendor again.", True),
    ("What about them?", True),
    ("Prior version of that contract.", True),
    ("What's the total spend?", False),
    ("Indemnification cap on contract X.", False),
])
def test_looks_like_anaphora(query, expected):
    assert looks_like_anaphora(query) is expected


@pytest.mark.parametrize("query,expected", [
    ("Just the ones in petrochem.", True),
    ("Filter by Q2 2025.", True),
    ("Only the contracts above $10M.", True),
    ("From the prior result set.", True),
    ("Of the prior matches show me 2026.", True),
    ("What's the total spend?", False),
])
def test_looks_like_refinement(query, expected):
    assert looks_like_refinement(query) is expected


# ===========================================================================
# IdentityContextResolver
# ===========================================================================


async def test_identity_resolver_returns_query_unchanged_when_no_context():
    r = IdentityContextResolver()
    out = await r.resolve("What about his loans?", None)
    assert out.resolved_query == "What about his loans?"
    assert out.anaphora_resolved == ()


async def test_identity_resolver_returns_query_unchanged_when_no_anaphora():
    """No pronoun + non-empty context → pass-through."""
    r = IdentityContextResolver()
    ctx = ChatContext(
        session_id="s1",
        last_turn_id=None,
        carry_forward_entities=(),
        carry_forward_filters={},
        prior_result_set_id=None,
        older_turn_summary="",
        last_k_verbatim_turns=(
            {"turn_index": 0, "user_query": "x", "answer_summary": "y"},
        ),
    )
    out = await r.resolve("What's the indemnification cap?", ctx)
    assert out.resolved_query == "What's the indemnification cap?"


async def test_identity_resolver_appends_hint_on_anaphora():
    r = IdentityContextResolver()
    ctx = ChatContext(
        session_id="s1",
        last_turn_id="t0",
        carry_forward_entities=("ent-1",),
        carry_forward_filters={},
        prior_result_set_id=None,
        older_turn_summary="",
        last_k_verbatim_turns=(
            {
                "turn_index": 0,
                "user_query": "Who is Mr. Sharma?",
                "answer_summary": "Mr. Sharma is the CFO of ACME.",
            },
        ),
    )
    out = await r.resolve("What about his loans?", ctx)
    # Resolved query includes the context hint.
    assert "Mr. Sharma" in out.resolved_query
    assert len(out.anaphora_resolved) == 1


async def test_identity_resolver_detects_refinement():
    r = IdentityContextResolver()
    ctx = ChatContext(
        session_id="s1",
        last_turn_id="t0",
        carry_forward_entities=(),
        carry_forward_filters={},
        prior_result_set_id=None,
        older_turn_summary="",
        last_k_verbatim_turns=(
            {"turn_index": 0, "user_query": "Show contracts.",
             "answer_summary": "Found 12 contracts."},
        ),
    )
    out = await r.resolve("Just the ones above $10M.", ctx)
    assert out.refinement_of_prior is True


async def test_identity_resolver_empty_query():
    r = IdentityContextResolver()
    out = await r.resolve("", None)
    assert out.resolved_query == ""
    assert out.notes == "empty_query"


# ===========================================================================
# _parse_resolution_json
# ===========================================================================


def test_parse_resolution_happy_path():
    out = _parse_resolution_json(
        '{"resolved_query": "X about Mr. Sharma", '
        '"anaphora_resolved": [{"from": "his", "to": "Mr. Sharma"}], '
        '"new_entities": ["ent-1"], "new_filters": {"date": "Q2-2025"}, '
        '"refinement_of_prior": true}',
        fallback_query="X about him",
    )
    assert out.resolved_query == "X about Mr. Sharma"
    assert out.anaphora_resolved[0].from_text == "his"
    assert out.anaphora_resolved[0].to_text == "Mr. Sharma"
    assert out.new_entities == ("ent-1",)
    assert out.new_filters == {"date": "Q2-2025"}
    assert out.refinement_of_prior is True


def test_parse_resolution_strips_code_fence():
    out = _parse_resolution_json(
        '```json\n{"resolved_query": "Y"}\n```',
        fallback_query="X",
    )
    assert out.resolved_query == "Y"


def test_parse_resolution_falls_back_on_bad_json():
    out = _parse_resolution_json("not json", fallback_query="X")
    assert out.resolved_query == "X"
    assert out.notes == "parse_error"


def test_parse_resolution_empty_resolved_query_falls_back():
    out = _parse_resolution_json(
        '{"resolved_query": ""}', fallback_query="original",
    )
    assert out.resolved_query == "original"


def test_parse_resolution_skips_malformed_anaphora_items():
    out = _parse_resolution_json(
        '{"resolved_query": "X", "anaphora_resolved": [{"from": "x"}, '
        '{"from": "a", "to": "b"}]}',
        fallback_query="X",
    )
    # First item missing "to" is skipped; second item is kept.
    assert len(out.anaphora_resolved) == 1
    assert out.anaphora_resolved[0].from_text == "a"


# ===========================================================================
# Factory
# ===========================================================================


def test_factory_default_is_identity():
    with _env(KB_CONTEXT_RESOLVER=None, KB_GEMINI_API_KEY=None):
        r = make_context_resolver()
        assert isinstance(r, IdentityContextResolver)


def test_factory_auto_is_identity_without_key():
    with _env(KB_CONTEXT_RESOLVER="auto", KB_GEMINI_API_KEY=None):
        r = make_context_resolver()
        assert isinstance(r, IdentityContextResolver)


def test_factory_unknown_raises():
    with _env(KB_CONTEXT_RESOLVER="bogus"):
        with pytest.raises(ValueError):
            make_context_resolver()


def test_factory_gemini_without_key_raises():
    with _env(KB_CONTEXT_RESOLVER="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError):
            make_context_resolver()
