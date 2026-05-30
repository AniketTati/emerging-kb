"""I7 — transient-failure retry helpers in kb/llm_batching."""

from __future__ import annotations

import pytest

from kb.llm_batching import is_transient, run_batched, with_retry


def test_is_transient_classification():
    assert is_transient(RuntimeError("429 Too Many Requests"))
    assert is_transient(RuntimeError("RESOURCE_EXHAUSTED"))
    assert is_transient(RuntimeError("deadline exceeded / timeout"))
    assert is_transient(RuntimeError("503 Service Unavailable"))
    # permanent
    assert not is_transient(RuntimeError("401 invalid api key"))
    assert not is_transient(ValueError("batch arity mismatch"))


@pytest.mark.asyncio
async def test_with_retry_recovers_after_transient():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 rate limit")
        return "ok"

    out = await with_retry(flaky, attempts=3, base_delay_s=0.0)
    assert out == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_with_retry_gives_up_after_attempts():
    async def always_429():
        raise RuntimeError("429")

    with pytest.raises(RuntimeError, match="429"):
        await with_retry(always_429, attempts=2, base_delay_s=0.0)


@pytest.mark.asyncio
async def test_with_retry_does_not_retry_permanent():
    calls = {"n": 0}

    async def permanent():
        calls["n"] += 1
        raise RuntimeError("401 invalid key")

    with pytest.raises(RuntimeError, match="401"):
        await with_retry(permanent, attempts=3, base_delay_s=0.0)
    assert calls["n"] == 1  # no retries on permanent error


@pytest.mark.asyncio
async def test_run_batched_retries_transient_batch_then_succeeds():
    state = {"batch_calls": 0}

    async def batch(xs):
        state["batch_calls"] += 1
        if state["batch_calls"] == 1:
            raise RuntimeError("429 rate limit")  # transient → retry
        return [x * 2 for x in xs]

    async def item(x):
        return x * 2

    out = await run_batched([1, 2, 3], batch_call=batch, item_call=item,
                            batch_size=3)
    assert out == [2, 4, 6]
    assert state["batch_calls"] == 2  # retried, did NOT fall to per-item
