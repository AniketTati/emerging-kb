"""B1 / WA-5 — Personalized PageRank for HippoRAG-style graph traversal.

Pure-function. Consumed by WA-10's T-mode planner (architecture §6
step 3 mode T — graph traversal from seed entities).

Algorithm (standard PPR):

  Initialize visit prob: p[seed] = 1/|seeds| for each seed entity,
  0 elsewhere.

  Repeat for `iterations` steps:
    new_p[v] = alpha * seed_dist[v] + (1 - alpha) * SUM_{u in N(v)} (
      p[u] * weight(u, v) / out_weight(u)
    )
    p = normalize(new_p)

  Return scored entities sorted by p descending.

Wave A defaults (from config/defaults.yaml on the WA-10 side):
  alpha = 0.15  (restart probability — 15% is HippoRAG paper's default)
  iterations = 20

NetworkX is the standard library for this but pulling in a graph-theory
dep for one function is overkill — we implement directly. ~100 lines.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


# Adjacency map shape: {entity_id: [(neighbor_id, weight), ...]}. Used
# both by callers building the graph in-memory and as the contract the
# repo's fetch_adjacency_for_ppr returns.
Adjacency = dict[str, list[tuple[str, float]]]


@dataclass(frozen=True)
class PPRResult:
    """One PPR-scored entity."""
    entity_id: str
    score: float


def personalized_pagerank(
    *,
    adjacency: Adjacency,
    seed_entity_ids: list[str],
    alpha: float = 0.15,
    iterations: int = 20,
    top_k: int | None = None,
    tolerance: float = 1e-6,
) -> list[PPRResult]:
    """Run Personalized PageRank over `adjacency` seeded on
    `seed_entity_ids`. Returns entities ordered by PPR score descending.

    Edge weights are treated as transition probabilities after row-
    normalization (out-weight from each node). Disconnected nodes
    contribute zero. Seeds get the restart-probability boost.

    Empty inputs → empty result (no exception).
    """
    if not adjacency or not seed_entity_ids:
        return []

    # Restrict seed set to entities that exist in the adjacency map; PPR
    # over a non-existent seed degenerates to uniform random walk on the
    # rest of the graph, which is not what callers want.
    valid_seeds = [s for s in seed_entity_ids if s in adjacency]
    if not valid_seeds:
        return []

    # Pre-compute the out-weight (sum of edge weights) per node — used
    # for the normalized transition probability.
    out_weight: dict[str, float] = {
        node: sum(w for _, w in neighbors) or 1.0
        for node, neighbors in adjacency.items()
    }

    # Seed distribution: uniform over valid_seeds.
    seed_mass = 1.0 / len(valid_seeds)
    seed_dist: dict[str, float] = defaultdict(float)
    for s in valid_seeds:
        seed_dist[s] = seed_mass

    # Initial visit probability = seed distribution.
    visit: dict[str, float] = defaultdict(float)
    for s in valid_seeds:
        visit[s] = seed_mass

    for _ in range(iterations):
        new_visit: dict[str, float] = defaultdict(float)
        # Restart contribution.
        for s, prob in seed_dist.items():
            new_visit[s] += alpha * prob
        # Walk contribution: each node distributes (1-alpha)*p[u] across
        # its neighbors, weighted by edge weight / out_weight.
        for u, p_u in visit.items():
            if p_u <= 0:
                continue
            neighbors = adjacency.get(u, ())
            if not neighbors:
                # Dangling node — sink mass back into seeds (standard
                # PageRank handling of dead-ends).
                for s, prob in seed_dist.items():
                    new_visit[s] += (1 - alpha) * p_u * prob
                continue
            total_w = out_weight[u]
            for v, w in neighbors:
                new_visit[v] += (1 - alpha) * p_u * (w / total_w)
        # Normalize (defends against numerical drift across iterations).
        total = sum(new_visit.values())
        if total <= 0:
            break
        scaled = {k: v / total for k, v in new_visit.items()}
        # Convergence check.
        delta = sum(abs(scaled.get(k, 0.0) - visit.get(k, 0.0)) for k in set(scaled) | set(visit))
        visit = defaultdict(float, scaled)
        if delta < tolerance:
            break

    ranked = sorted(visit.items(), key=lambda kv: kv[1], reverse=True)
    if top_k is not None:
        ranked = ranked[:top_k]
    return [PPRResult(entity_id=eid, score=score) for eid, score in ranked]


def build_adjacency_from_edges(
    edges: list[tuple[str, str, float]],
) -> Adjacency:
    """Helper: convert (src, dst, weight) triples into an undirected
    adjacency map. Used by tests + by callers that want to build the
    graph in-memory from an edge list."""
    adj: Adjacency = {}
    for src, dst, weight in edges:
        if src == dst:
            continue
        adj.setdefault(src, []).append((dst, weight))
        adj.setdefault(dst, []).append((src, weight))
    return adj
