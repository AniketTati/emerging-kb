"""I4 — top-k entity-match selection (the fix for top-1-only missing a true
match that's the 2nd/3rd nearest neighbour)."""

from __future__ import annotations

import pytest

from kb.identity.resolve import select_entity_match


async def _judge_yes(name):
    return True


async def _judge_no(name):
    return False


@pytest.mark.asyncio
async def test_auto_match_first_above_high():
    cands = [("e1", "Acme", 0.95), ("e2", "Acme Corp", 0.93)]
    out = await select_entity_match(cands, judge_same=_judge_no)
    assert out == ("e1", "embedding", 0.95)


@pytest.mark.asyncio
async def test_picks_second_nearest_when_judge_approves_it():
    async def judge(name):
        return name == "Vertex Industries Ltd"

    cands = [
        ("e1", "Vertex Foods", 0.88),
        ("e2", "Vertex Industries Ltd", 0.86),
    ]
    out = await select_entity_match(cands, judge_same=judge)
    assert out == ("e2", "llm_judge", 0.86)


@pytest.mark.asyncio
async def test_judge_veto_yields_no_match():
    cands = [("e1", "Vertex Foods", 0.88)]
    out = await select_entity_match(cands, judge_same=_judge_no)
    assert out is None


@pytest.mark.asyncio
async def test_stops_below_low_threshold():
    cands = [("e1", "x", 0.50), ("e2", "y", 0.40)]
    out = await select_entity_match(cands, judge_same=_judge_yes)
    assert out is None


@pytest.mark.asyncio
async def test_empty_candidates():
    assert await select_entity_match([], judge_same=_judge_yes) is None


@pytest.mark.asyncio
async def test_judge_exception_treated_as_no_match():
    async def judge_boom(name):
        raise RuntimeError("judge down")

    cands = [("e1", "x", 0.88)]
    out = await select_entity_match(cands, judge_same=judge_boom)
    assert out is None
