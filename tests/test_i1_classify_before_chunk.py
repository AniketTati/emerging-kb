"""I1 — classify-before-chunk: the classifier emits router-valid doc_types,
and the router maps them to the type-aware chunker (the whole point: a
bank_statement must chunk row-aware, not via the generic hierarchical
splitter that the NULL-doc_type path fell back to)."""

from __future__ import annotations

import asyncio

import pytest

from kb.chunking.doc_type_router import known_doc_types, select_chunker
from kb.classification import (
    IdentityDocTypeClassifier,
    make_doc_type_classifier,
)


def test_known_doc_types_includes_tabular():
    vocab = known_doc_types()
    assert "bank_statement" in vocab
    assert "invoice" in vocab


@pytest.mark.asyncio
async def test_router_routes_tabular_to_row_chunker():
    # The fix's payoff: a classified bank_statement → row_per_leaf, whereas
    # the old NULL-doc_type path (markdown) fell back to hierarchical.
    typed = await select_chunker(
        None, workspace_id="w", doc_type="bank_statement",
        mime_type="text/markdown",
    )
    assert typed.kind == "row_per_leaf"

    untyped = await select_chunker(
        None, workspace_id="w", doc_type=None, mime_type="text/markdown",
    )
    assert untyped.kind == "hierarchical"  # what we were stuck with pre-I1


@pytest.mark.asyncio
async def test_identity_classifier_returns_unknown():
    assert await IdentityDocTypeClassifier().classify(text="anything") == "unknown"


def test_factory_returns_a_classifier():
    c = make_doc_type_classifier()
    assert hasattr(c, "classify")


@pytest.mark.asyncio
async def test_gemini_classifier_rejects_hallucinated_label(monkeypatch):
    # A label outside the router vocabulary must be coerced to 'unknown' so
    # it can't silently mis-route.
    from kb.classification import GeminiDocTypeClassifier

    class _FakeResp:
        text = '{"doc_type": "totally_made_up_type"}'

    class _FakeModels:
        async def generate_content(self, **kw):
            return _FakeResp()

    class _FakeAio:
        models = _FakeModels()

    class _FakeClient:
        aio = _FakeAio()

    clf = GeminiDocTypeClassifier(client=_FakeClient())
    out = await clf.classify(text="some doc")
    assert out == "unknown"


@pytest.mark.asyncio
async def test_gemini_classifier_accepts_known_label():
    from kb.classification import GeminiDocTypeClassifier

    class _FakeResp:
        text = '{"doc_type": "bank_statement"}'

    class _FakeModels:
        async def generate_content(self, **kw):
            return _FakeResp()

    class _FakeAio:
        models = _FakeModels()

    class _FakeClient:
        aio = _FakeAio()

    clf = GeminiDocTypeClassifier(client=_FakeClient())
    assert await clf.classify(text="...") == "bank_statement"
