"""S1 — generic LLM batching primitive (one reusable helper for every site).

Ingestion makes one LLM call per chunk/entity (contextualize, mentions,
triples, schema-entities, identity judge) — linear in chunks and the #1
throughput/cost wall. This helper processes a list of items through **batched**
LLM calls (N items per call) with a **per-item fallback** whenever a batch
raises or returns the wrong arity, so a single malformed batch never loses or
misaligns items. Order is preserved: `result[i]` corresponds to `items[i]`.

Used by the ingestion stages AND by the new batched LLM-judge calls in I2
(field-merge) and I4 (entity top-k) so they're batched from the start.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

_LOG = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


# ---------------------------------------------------------------------------
# I7 — transient-failure retry
# ---------------------------------------------------------------------------

_TRANSIENT_MARKERS = (
    "429", "rate limit", "too many requests", "resource_exhausted",
    "timeout", "timed out", "503", "502", "504", "500",
    "service unavailable", "unavailable", "deadline", "connection",
)


def is_transient(exc: Exception) -> bool:
    """True for retryable LLM/network errors (rate limit, timeout, 5xx).
    Permanent errors (bad key/model, 4xx other than 429, arity/parse
    mismatch) are NOT retried — retrying just wastes the budget."""
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


async def with_retry(
    fn: Callable[[], Awaitable[R]],
    *,
    attempts: int = 3,
    base_delay_s: float = 2.0,
    label: str = "llm call",
) -> R:
    """Run `fn`, retrying TRANSIENT failures with exponential backoff. The
    final attempt's exception (or the first permanent one) propagates so the
    caller's existing error handling decides what to do (e.g. mark failed)."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not is_transient(exc) or attempt == attempts - 1:
                raise
            delay = base_delay_s * (2 ** attempt)
            _LOG.warning(
                "%s transient failure (attempt %d/%d): %s — retrying in %.0fs",
                label, attempt + 1, attempts, exc, delay,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def _chunked(items: list[T], size: int):
    for i in range(0, len(items), size):
        yield i, items[i : i + size]


async def run_batched(
    items: list[T],
    *,
    batch_call: Callable[[list[T]], Awaitable[list[R]]],
    item_call: Callable[[T], Awaitable[R]],
    batch_size: int = 12,
    max_concurrency: int = 4,
) -> list[R]:
    """Process `items` via batched LLM calls, preserving order.

    Args:
      batch_call: sends a batch of items in ONE LLM call, returns one result
        per item (same order, same length).
      item_call: the single-item fallback used when a batch raises or returns
        a length that doesn't match its input (so a bad batch degrades to the
        old per-item behaviour for that batch only — never the whole doc).
      batch_size: items per LLM call.
      max_concurrency: batches in flight at once.

    Returns: `list[R]` of len(items); `result[i]` ↔ `items[i]`.
    """
    if not items:
        return []

    sem = asyncio.Semaphore(max(1, max_concurrency))
    results: list[R | None] = [None] * len(items)

    async def _do(offset: int, batch: list[T]) -> None:
        async with sem:
            try:
                out = await with_retry(lambda: batch_call(batch), label="batch_call")
                if not isinstance(out, list) or len(out) != len(batch):
                    raise ValueError(
                        f"batch arity mismatch: got "
                        f"{len(out) if isinstance(out, list) else type(out).__name__} "
                        f"for batch of {len(batch)}"
                    )
            except Exception as exc:  # noqa: BLE001
                # Per-item fallback for THIS batch only. If an item itself
                # raises, that propagates (caller's existing error handling
                # decides what to do — e.g. mark the file failed).
                _LOG.warning(
                    "batch call failed (%s); falling back to per-item for "
                    "%d items", exc, len(batch),
                )
                out = list(await asyncio.gather(*(
                    with_retry(lambda it=it: item_call(it), label="item_call")
                    for it in batch
                )))
            for j, r in enumerate(out):
                results[offset + j] = r

    await asyncio.gather(*(
        _do(off, batch) for off, batch in _chunked(items, batch_size)
    ))
    return results  # type: ignore[return-value]
