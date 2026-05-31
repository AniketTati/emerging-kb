"""I2 — semantic field convergence (EDC: embedding-block + LLM-judge merge).

The exact-match clusterer leaves `total_cost` and `total_amount` as two
separate canonical fields forever. `converge_clusters_semantic` should merge
them when (a) their embeddings are close AND (b) the judge confirms the same
concept — while leaving genuinely-different fields (`vendor_name`) alone.
"""

from __future__ import annotations

import pytest

from kb.extraction.promotion import FieldCluster, converge_clusters_semantic


def _c(name: str, prevalence: float = 1.0, n: int = 5) -> FieldCluster:
    return FieldCluster(
        canonical_name=name, description=f"the {name}", value_type="number",
        n_docs_observed=n, prevalence=prevalence, stability=1.0,
        value_type_confidence=1.0,
    )


# Fake embedder: same vector for the two "total" variants, orthogonal for vendor.
_VECS = {
    "total_cost: the total_cost": [1.0, 0.0, 0.0],
    "total_amount: the total_amount": [0.99, 0.01, 0.0],
    "vendor_name: the vendor_name": [0.0, 0.0, 1.0],
}


async def _embed(texts):
    return [_VECS[t] for t in texts]


async def _judge_merge_totals(a: FieldCluster, b: FieldCluster) -> bool:
    names = {a.canonical_name, b.canonical_name}
    return names == {"total_cost", "total_amount"}


@pytest.mark.asyncio
async def test_merges_semantically_equivalent_fields():
    clusters = [_c("total_cost", 0.9), _c("total_amount", 0.6), _c("vendor_name")]
    out = await converge_clusters_semantic(
        clusters, embed_fn=_embed, judge_fn=_judge_merge_totals,
        sim_threshold=0.86,
    )
    names = {c.canonical_name for c in out}
    assert names == {"total_cost", "vendor_name"}  # totals merged into one
    merged = next(c for c in out if c.canonical_name == "total_cost")
    assert merged.n_docs_observed == 10  # 5 + 5 summed


@pytest.mark.asyncio
async def test_judge_veto_prevents_merge():
    # Embeddings close, but judge says NO → stay separate.
    async def judge_no(a, b):
        return False

    clusters = [_c("total_cost", 0.9), _c("total_amount", 0.6), _c("vendor_name")]
    out = await converge_clusters_semantic(
        clusters, embed_fn=_embed, judge_fn=judge_no, sim_threshold=0.86,
    )
    assert len({c.canonical_name for c in out}) == 3  # nothing merged


@pytest.mark.asyncio
async def test_blocking_threshold_skips_dissimilar():
    # Judge would say yes to anything, but vendor is far → never a candidate.
    async def judge_yes(a, b):
        return True

    clusters = [_c("total_cost", 0.9), _c("vendor_name")]
    out = await converge_clusters_semantic(
        clusters, embed_fn=_embed, judge_fn=judge_yes, sim_threshold=0.86,
    )
    assert len(out) == 2  # cosine ~0 < 0.86 → not even judged


@pytest.mark.asyncio
async def test_single_cluster_noop():
    out = await converge_clusters_semantic(
        [_c("total_cost")], embed_fn=_embed, judge_fn=_judge_merge_totals,
    )
    assert len(out) == 1


# ---------------------------------------------------------------------------
# FIX 4 — user-declared schema names anchor the convergence
# ---------------------------------------------------------------------------


_ANCHOR_VECS = {
    # emergent variants + a declared anchor, all near each other
    "all_in_rate: the all_in_rate": [1.0, 0.0, 0.0],
    "post_amendment_rate: the post_amendment_rate": [0.99, 0.01, 0.0],
    # seeded anchor has empty description → text strips to just the name
    "interest_rate": [0.985, 0.015, 0.0],
    # emergent interest_rate (with description) → full "name: desc" text
    "interest_rate: the interest_rate": [0.985, 0.015, 0.0],
    "vendor_name: the vendor_name": [0.0, 0.0, 1.0],
}


async def _embed_anchor(texts):
    return [_ANCHOR_VECS[t] for t in texts]


async def _judge_all_rates(a, b):
    rate_names = {"all_in_rate", "post_amendment_rate", "interest_rate"}
    return a.canonical_name in rate_names and b.canonical_name in rate_names


@pytest.mark.asyncio
async def test_emergent_variants_adopt_declared_anchor_name():
    # interest_rate is user-declared (not emergent). all_in_rate (3 docs) +
    # post_amendment_rate (2 docs) should merge and adopt the DECLARED name.
    clusters = [_c("all_in_rate", 0.5, n=3), _c("post_amendment_rate", 0.33, n=2)]
    out = await converge_clusters_semantic(
        clusters, embed_fn=_embed_anchor, judge_fn=_judge_all_rates,
        sim_threshold=0.86, anchor_names={"interest_rate"},
    )
    names = {c.canonical_name for c in out}
    assert names == {"interest_rate"}              # declared name won
    merged = out[0]
    assert merged.n_docs_observed == 5             # 3 + 2 emergent (anchor adds 0)


@pytest.mark.asyncio
async def test_lone_declared_anchor_is_dropped():
    # A declared field with no emergent variant must NOT become a 0-doc cluster
    # (it's already a schema field by declaration).
    clusters = [_c("vendor_name", 1.0, n=4)]
    out = await converge_clusters_semantic(
        clusters, embed_fn=_embed_anchor, judge_fn=_judge_all_rates,
        sim_threshold=0.86, anchor_names={"interest_rate"},
    )
    assert {c.canonical_name for c in out} == {"vendor_name"}


@pytest.mark.asyncio
async def test_anchor_wins_even_when_less_prevalent():
    # The most-prevalent emergent spelling would normally win the canonical
    # name; an anchor overrides that even at lower prevalence.
    clusters = [_c("all_in_rate", 0.9, n=9), _c("interest_rate", 0.1, n=1)]
    out = await converge_clusters_semantic(
        clusters, embed_fn=_embed_anchor, judge_fn=_judge_all_rates,
        sim_threshold=0.86, anchor_names={"interest_rate"},
    )
    assert {c.canonical_name for c in out} == {"interest_rate"}
