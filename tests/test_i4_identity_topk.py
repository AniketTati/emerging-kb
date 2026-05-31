"""I4 — top-k entity-match selection (the fix for top-1-only missing a true
match that's the 2nd/3rd nearest neighbour)."""

from __future__ import annotations

import pytest

from kb.identity.resolve import _is_name_alias, select_entity_match


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


# ---------------------------------------------------------------------------
# FIX 7 — alias routing (HDFC ⊂ HDFC BANK) below the low threshold
# ---------------------------------------------------------------------------


def test_name_alias_prefix_only():
    assert _is_name_alias("HDFC", "HDFC BANK") is True
    assert _is_name_alias("HDFC BANK", "HDFC") is True
    assert _is_name_alias("Apollo Hospitals", "Apollo Hospitals Pune") is True
    # precise: shared first token but diverging → NOT an alias
    assert _is_name_alias("Bank of America", "Bank of India") is False
    assert _is_name_alias("Apollo Tyres", "Apollo Hospitals") is False
    assert _is_name_alias("HDFC", "HDFC") is False  # identical, not a variant


@pytest.mark.asyncio
async def test_alias_below_low_is_judged_when_mention_name_given():
    # "HDFC" vs "HDFC BANK" embeds at 0.80 (below low 0.85) but is an obvious
    # variant → with mention_name set, the judge sees it and can confirm.
    cands = [("e1", "HDFC BANK", 0.80)]
    out = await select_entity_match(
        cands, judge_same=_judge_yes, mention_name="HDFC",
    )
    assert out == ("e1", "llm_judge", 0.80)


@pytest.mark.asyncio
async def test_alias_below_low_ignored_without_mention_name():
    # Legacy behavior preserved: no mention_name → stop at the sub-low candidate.
    cands = [("e1", "HDFC BANK", 0.80)]
    out = await select_entity_match(cands, judge_same=_judge_yes)
    assert out is None


@pytest.mark.asyncio
async def test_non_alias_below_low_not_judged_even_in_alias_mode():
    # A sub-low candidate that is NOT a name variant is never judged.
    calls = {"n": 0}

    async def judge(name):
        calls["n"] += 1
        return True

    cands = [("e1", "Tata Steel", 0.70)]
    out = await select_entity_match(cands, judge_same=judge, mention_name="HDFC")
    assert out is None
    assert calls["n"] == 0
