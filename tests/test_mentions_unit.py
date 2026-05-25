"""Phase 5a — mention extraction unit tests (no DB, no real LLM).

Covers §5.12.1 decisions #2/#3/#4. Mirrors the
`test_contextualization_gemini_unit.py` mock pattern from 3b-bis.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest

from kb.extraction.mentions import (
    GeminiMentionExtractor,
    IdentityMentionExtractor,
    MentionExtractionError,
    ONTONOTES_18_TYPES,
    _parse_mentions_json,
    make_mention_extractor,
)


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
# Decision #2 — OntoNotes-18 type filtering
# ===========================================================================


def test_parse_mentions_filters_unknown_types():
    """LLM may hallucinate types like 'CITY' or 'AMOUNT'. Filter them out."""
    raw = json.dumps({
        "mentions": [
            {"text": "ACME", "type": "ORG", "start": 0, "end": 4, "confidence": 0.9},
            {"text": "Paris", "type": "CITY"},  # hallucinated type
            {"text": "$100", "type": "MONEY"},
        ]
    })
    mentions = _parse_mentions_json(raw)
    types = [m.mention_type for m in mentions]
    assert types == ["ORG", "MONEY"], f"expected only valid types; got {types}"


def test_parse_mentions_strips_code_fences():
    """Gemini sometimes wraps JSON in ```json ... ``` fences."""
    raw = "```json\n" + json.dumps({"mentions": [{"text": "ACME", "type": "ORG"}]}) + "\n```"
    mentions = _parse_mentions_json(raw)
    assert len(mentions) == 1
    assert mentions[0].mention_text == "ACME"


def test_parse_mentions_drops_empty_text():
    raw = json.dumps({
        "mentions": [
            {"text": "", "type": "ORG"},
            {"text": "   ", "type": "ORG"},
            {"text": "ACME", "type": "ORG"},
        ]
    })
    mentions = _parse_mentions_json(raw)
    assert len(mentions) == 1
    assert mentions[0].mention_text == "ACME"


def test_parse_mentions_handles_nullable_offsets():
    """Decision #4: start/end/confidence nullable."""
    raw = json.dumps({
        "mentions": [
            {"text": "ACME", "type": "ORG"},  # no offsets, no confidence
        ]
    })
    mentions = _parse_mentions_json(raw)
    assert len(mentions) == 1
    assert mentions[0].start_offset is None
    assert mentions[0].end_offset is None
    assert mentions[0].confidence is None


def test_parse_mentions_invalid_json_returns_empty():
    # PR4: parser now uses json_recovery which silently returns []
    # rather than raising. Truncated/malformed input is treated as
    # "no salvageable items" so the worker keeps processing other
    # chunks instead of failing the whole file.
    assert _parse_mentions_json("not valid json {{") == []


# ===========================================================================
# Decision #3 — Identity fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_extractor_returns_empty_list():
    extractor = IdentityMentionExtractor()
    result = await extractor.extract(doc_text="anything", chunk_text="hello world")
    assert result.mentions == []
    assert result.model_id == "identity"
    assert result.input_token_count == 0
    assert result.output_token_count == 0


# ===========================================================================
# Decision #3 — factory selector matrix
# ===========================================================================


def test_factory_selector_matrix():
    # auto + no keys → Identity
    with _env(KB_MENTIONS_EXTRACTOR="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_mention_extractor(), IdentityMentionExtractor)

    # auto + Gemini key → Gemini-first
    with _env(KB_MENTIONS_EXTRACTOR="auto", KB_GEMINI_API_KEY="fake-gemini-key", KB_ANTHROPIC_API_KEY=None):
        extractor = make_mention_extractor()
        assert isinstance(extractor, GeminiMentionExtractor)

    # auto + Anthropic key only → Anthropic
    with _env(KB_MENTIONS_EXTRACTOR="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY="fake-anthropic-key"):
        extractor = make_mention_extractor()
        from kb.extraction.mentions import AnthropicMentionExtractor
        assert isinstance(extractor, AnthropicMentionExtractor)

    # explicit identity
    with _env(KB_MENTIONS_EXTRACTOR="identity"):
        assert isinstance(make_mention_extractor(), IdentityMentionExtractor)

    # explicit gemini without key → loud fail
    with _env(KB_MENTIONS_EXTRACTOR="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError, match="KB_MENTIONS_EXTRACTOR=gemini"):
            make_mention_extractor()

    # bogus selector → loud fail
    with _env(KB_MENTIONS_EXTRACTOR="bogus"):
        with pytest.raises(ValueError, match="Unknown KB_MENTIONS_EXTRACTOR"):
            make_mention_extractor()


# ===========================================================================
# OntoNotes-18 set integrity
# ===========================================================================


def test_ontonotes_18_set_has_18_types():
    assert len(ONTONOTES_18_TYPES) == 18
    assert "PERSON" in ONTONOTES_18_TYPES
    assert "MONEY" in ONTONOTES_18_TYPES
    assert "WORK_OF_ART" in ONTONOTES_18_TYPES


# ===========================================================================
# Mocked Gemini path — verify the parser pipeline works end-to-end on a
# fake response (no network call)
# ===========================================================================


class _FakeGeminiResponse:
    def __init__(self, raw_text: str, prompt_tokens: int = 100, candidates_tokens: int = 50):
        self.candidates = [
            type("C", (), {
                "content": type("Ct", (), {
                    "parts": [type("P", (), {"text": raw_text})]
                })
            })
        ]
        self.usage_metadata = type("U", (), {
            "prompt_token_count": prompt_tokens,
            "candidates_token_count": candidates_tokens,
        })


class _FakeModels:
    def __init__(self, raw_text: str, capture: dict):
        self._raw_text = raw_text
        self._capture = capture

    async def generate_content(self, **kwargs):
        self._capture.update(kwargs)
        return _FakeGeminiResponse(self._raw_text)


class _FakeAio:
    def __init__(self, raw_text: str, capture: dict):
        self.models = _FakeModels(raw_text, capture)


class _FakeGeminiClient:
    def __init__(self, raw_text: str):
        self._raw_text = raw_text
        self.last_kwargs: dict[str, Any] = {}
        self.aio = _FakeAio(raw_text, self.last_kwargs)


@pytest.mark.asyncio
async def test_gemini_extractor_parses_response_into_mentions():
    raw = json.dumps({
        "mentions": [
            {"text": "Acme Corp", "type": "ORG", "start": 0, "end": 9, "confidence": 0.95},
            {"text": "2024-01-15", "type": "DATE"},
        ]
    })
    extractor = GeminiMentionExtractor(client=_FakeGeminiClient(raw), model="gemini-2.5-flash")
    result = await extractor.extract(doc_text="doc", chunk_text="Acme Corp filed in 2024-01-15")
    assert len(result.mentions) == 2
    assert result.mentions[0].mention_text == "Acme Corp"
    assert result.mentions[0].mention_type == "ORG"
    assert result.mentions[1].mention_text == "2024-01-15"
    assert result.mentions[1].mention_type == "DATE"
    assert result.model_id == "gemini-2.5-flash"
    assert result.input_token_count == 100
    assert result.output_token_count == 50
