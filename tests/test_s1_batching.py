"""S1 — llm_batching.run_batched primitive + contextualize_batch.

The primitive must: preserve order, fall back to per-item when a batch raises
or returns the wrong arity, and never lose/misalign items.
"""

from __future__ import annotations

import asyncio

import pytest

from kb.llm_batching import run_batched


async def _double_batch(xs):
    return [x * 2 for x in xs]


async def _double_item(x):
    return x * 2


@pytest.mark.asyncio
async def test_preserves_order_and_values():
    out = await run_batched(
        list(range(10)), batch_call=_double_batch, item_call=_double_item,
        batch_size=3, max_concurrency=2,
    )
    assert out == [x * 2 for x in range(10)]


@pytest.mark.asyncio
async def test_empty():
    assert await run_batched([], batch_call=_double_batch, item_call=_double_item) == []


@pytest.mark.asyncio
async def test_falls_back_on_batch_exception():
    calls = {"item": 0}

    async def bad_batch(xs):
        raise RuntimeError("batch boom")

    async def item(x):
        calls["item"] += 1
        return x + 1

    out = await run_batched([1, 2, 3], batch_call=bad_batch, item_call=item,
                            batch_size=3)
    assert out == [2, 3, 4]
    assert calls["item"] == 3  # fell back to per-item


@pytest.mark.asyncio
async def test_falls_back_on_arity_mismatch():
    async def short_batch(xs):
        return xs[:-1]  # wrong length → must fall back

    async def item(x):
        return x

    out = await run_batched([5, 6, 7], batch_call=short_batch, item_call=item,
                            batch_size=3)
    assert out == [5, 6, 7]


@pytest.mark.asyncio
async def test_per_batch_fallback_is_isolated():
    # batch 1 (0,1,2) ok via batch; batch 2 (3,4,5) raises → per-item.
    async def batch(xs):
        if 3 in xs:
            raise RuntimeError("only second batch fails")
        return [x * 10 for x in xs]

    async def item(x):
        return x * 100

    out = await run_batched([0, 1, 2, 3, 4, 5], batch_call=batch,
                            item_call=item, batch_size=3, max_concurrency=2)
    assert out == [0, 10, 20, 300, 400, 500]


@pytest.mark.asyncio
async def test_identity_contextualize_batch():
    from kb.contextualization import IdentityContextualizer
    ctx = IdentityContextualizer()
    out = await ctx.contextualize_batch(doc_text="D", chunk_texts=["a", "b"])
    assert [c.contextual_text for c in out] == ["a", "b"]
    assert all(c.model_id == "identity" for c in out)


def test_parse_json_str_array_tolerates_fences():
    from kb.contextualization import _parse_json_str_array
    assert _parse_json_str_array('["x", "y"]') == ["x", "y"]
    assert _parse_json_str_array('```json\n["x", "y"]\n```') == ["x", "y"]
