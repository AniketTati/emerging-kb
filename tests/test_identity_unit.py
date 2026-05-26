"""Phase 7 — identity resolution unit tests (no DB)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest

from kb.identity.judge import (
    GeminiIdentityJudge,
    NoopIdentityJudge,
    _parse_judgment,
    make_identity_judge,
)
from kb.identity.resolve import (
    EMBEDDING_HIGH_THRESHOLD,
    EMBEDDING_LOW_THRESHOLD,
    NOISE_MENTION_TYPES,
    ResolutionResult,
    is_noise_mention_type,
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


def test_thresholds_are_sensible():
    assert 0.0 <= EMBEDDING_LOW_THRESHOLD < EMBEDDING_HIGH_THRESHOLD <= 1.0


def test_parse_judgment_true():
    assert _parse_judgment('{"same": true, "confidence": 0.9}') is True


def test_parse_judgment_false():
    assert _parse_judgment('{"same": false}') is False


def test_parse_judgment_missing_key_defaults_false():
    assert _parse_judgment('{"confidence": 0.9}') is False


def test_parse_judgment_invalid_returns_false():
    assert _parse_judgment("not json") is False


def test_parse_judgment_handles_code_fence():
    raw = "```json\n" + json.dumps({"same": True}) + "\n```"
    assert _parse_judgment(raw) is True


@pytest.mark.asyncio
async def test_noop_judge_always_false():
    judge = NoopIdentityJudge()
    out = await judge.same_entity(
        text_a="ACME Corp", type_a="ORG",
        text_b="ACME Corporation", type_b="ORG",
    )
    assert out is False


def test_factory_selector_matrix():
    with _env(KB_IDENTITY_JUDGE="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_identity_judge(), NoopIdentityJudge)
    with _env(KB_IDENTITY_JUDGE="auto", KB_GEMINI_API_KEY="fake", KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_identity_judge(), GeminiIdentityJudge)
    with _env(KB_IDENTITY_JUDGE="identity"):
        assert isinstance(make_identity_judge(), NoopIdentityJudge)
    with _env(KB_IDENTITY_JUDGE="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError, match="KB_IDENTITY_JUDGE=gemini"):
            make_identity_judge()
    with _env(KB_IDENTITY_JUDGE="bogus"):
        with pytest.raises(ValueError, match="Unknown KB_IDENTITY_JUDGE"):
            make_identity_judge()


def test_resolution_result_dataclass():
    r = ResolutionResult(entity_id="x", confidence=0.95, method="embedding", created_new=False)
    assert r.entity_id == "x"
    assert r.confidence == 0.95
    assert r.method == "embedding"
    assert r.created_new is False


# ===========================================================================
# Additional parser edge cases (§5.14 #6 — robust to LLM output variance)
# ===========================================================================


def test_parse_judgment_top_level_array_returns_false():
    """LLM erroneously returns a JSON array instead of dict — be defensive."""
    assert _parse_judgment('[{"same": true}]') is False


def test_parse_judgment_string_true_is_truthy_via_bool():
    """LLM stringifies the value; bool('false') is True (Python). Documented
    behavior — strict mode would parse; Wave A trusts the JSON shape."""
    # Our parser does bool(data.get("same", False)); 'true' string is truthy.
    assert _parse_judgment('{"same": "true"}') is True


def test_parse_judgment_explicit_false():
    """High-confidence false stays false (we ignore confidence)."""
    assert _parse_judgment('{"same": false, "confidence": 0.99}') is False


def test_parse_judgment_extra_keys_ignored():
    """Forward-compat — Wave B may add explanation field, parser must not break."""
    raw = '{"same": true, "explanation": "both refer to ACME Corp", "extra": 42}'
    assert _parse_judgment(raw) is True


# ===========================================================================
# Mocked Gemini judge — end-to-end on a fake response
# ===========================================================================


class _FakeResponse:
    def __init__(self, raw_text: str):
        self.candidates = [
            type("C", (), {
                "content": type("Ct", (), {
                    "parts": [type("P", (), {"text": raw_text})]
                })
            })
        ]
        self.usage_metadata = None


class _FakeModels:
    def __init__(self, raw_text: str, capture: dict):
        self._raw_text = raw_text
        self._capture = capture

    async def generate_content(self, **kwargs):
        self._capture.update(kwargs)
        return _FakeResponse(self._raw_text)


class _FakeAio:
    def __init__(self, raw_text: str, capture: dict):
        self.models = _FakeModels(raw_text, capture)


class _FakeClient:
    def __init__(self, raw_text: str):
        self.last_kwargs: dict = {}
        self.aio = _FakeAio(raw_text, self.last_kwargs)


@pytest.mark.asyncio
async def test_gemini_judge_returns_true_on_same_response():
    import json
    judge = GeminiIdentityJudge(client=_FakeClient(json.dumps({"same": True, "confidence": 0.95})))
    result = await judge.same_entity(
        text_a="ACME Corp", type_a="ORG",
        text_b="ACME Corporation", type_b="ORG",
    )
    assert result is True


@pytest.mark.asyncio
async def test_gemini_judge_returns_false_on_different_response():
    import json
    judge = GeminiIdentityJudge(client=_FakeClient(json.dumps({"same": False})))
    result = await judge.same_entity(
        text_a="ACME Corp", type_a="ORG", text_b="Globex Inc", type_b="ORG",
    )
    assert result is False


@pytest.mark.asyncio
async def test_gemini_judge_returns_false_on_api_error():
    """Resilience — if the LLM call fails, treat as 'different' (safe default)."""

    class _ErrorClient:
        class _Aio:
            class _Models:
                async def generate_content(self, **kwargs):
                    raise RuntimeError("API down")
            models = _Models()
        aio = _Aio()

    judge = GeminiIdentityJudge(client=_ErrorClient())
    result = await judge.same_entity(
        text_a="A", type_a="ORG", text_b="B", type_b="ORG",
    )
    assert result is False


# ---------------------------------------------------------------------------
# R4 — noise mention-type predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mention_type", [
    "CARDINAL", "QUANTITY", "DATE", "MONEY",
    "ORDINAL", "PERCENT", "TIME",
])
def test_is_noise_mention_type_known_types(mention_type):
    assert is_noise_mention_type(mention_type) is True


@pytest.mark.parametrize("mention_type", [
    "ORG", "PERSON", "PRODUCT", "GPE",
    "WORK_OF_ART", "LAW", "LOC", "EVENT", "NORP", "LANGUAGE",
])
def test_is_noise_mention_type_signal_types(mention_type):
    assert is_noise_mention_type(mention_type) is False


def test_is_noise_mention_type_case_tolerant():
    assert is_noise_mention_type("cardinal") is True
    assert is_noise_mention_type("Quantity") is True
    assert is_noise_mention_type("  MONEY  ") is True


def test_is_noise_mention_type_handles_none_and_empty():
    assert is_noise_mention_type(None) is False
    assert is_noise_mention_type("") is False


def test_noise_mention_types_covers_full_spacy_numeric_set():
    """Sanity-check the constant — spaCy's English NER ships seven
    numeric/temporal labels. All seven must be in the noise set so
    none silently leak past the resolver's Stage-0 skip."""
    spacy_numeric_labels = {
        "CARDINAL", "QUANTITY", "DATE", "MONEY",
        "ORDINAL", "PERCENT", "TIME",
    }
    assert spacy_numeric_labels <= NOISE_MENTION_TYPES
