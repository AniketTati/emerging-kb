"""#19 / I2 — field-merge judge (EDC convergence judge_fn).

Mirrors the identity-judge contract: env-selected factory, fail-closed JSON
parsing, and an identity fallback that never merges (safe default when no LLM
key is present).
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from kb.extraction.field_judge import (
    NoopFieldMergeJudge,
    _parse_judgment,
    make_field_merge_judge,
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


def test_auto_selects_noop_without_keys():
    with _env(KB_FIELD_JUDGE=None, KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        judge = make_field_merge_judge()
    assert isinstance(judge, NoopFieldMergeJudge)


def test_explicit_identity_selector():
    with _env(KB_FIELD_JUDGE="identity"):
        assert isinstance(make_field_merge_judge(), NoopFieldMergeJudge)


def test_unknown_selector_raises():
    with _env(KB_FIELD_JUDGE="bogus"):
        with pytest.raises(ValueError):
            make_field_merge_judge()


def test_gemini_selector_without_key_raises():
    with _env(KB_FIELD_JUDGE="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError):
            make_field_merge_judge()


@pytest.mark.asyncio
async def test_noop_never_merges():
    judge = NoopFieldMergeJudge()
    out = await judge.same_field(
        name_a="total_cost", desc_a="contract value", type_a="number",
        name_b="total_amount", desc_b="contract value", type_b="number",
    )
    assert out is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"same": true, "confidence": 0.9}', True),
        ('{"same": false}', False),
        ('```json\n{"same": true}\n```', True),
        ("not json at all", False),
        ('["not", "a", "dict"]', False),
        ("{}", False),
    ],
)
def test_parse_judgment(raw, expected):
    assert _parse_judgment(raw) is expected
