"""Canonical-entity merge — shared, tested primitive.

The merge mechanism (repoint mention_to_entity + relationships + graph_edges
+ fact_conflicts to the survivor, recompute mention_count, stamp
`merged_into` on losers) was first written inline in
`scripts/dedup_canonical_entities.py` (Bug D Tier-1 #4). #19 lifts it here so
BOTH the manual dedup script AND the automatic corpus-finalization reconcile
sweep (`reconcile_workspace_entities_impl`) share one implementation.

Soft-merge, not delete: losers stay in the table with `merged_into = survivor`
so the audit trail survives; read paths filter `WHERE merged_into IS NULL`
(see migration 0046 + q_planner/compiler active-filter).

Also holds the cheap token-overlap clustering heuristics that narrow the
LLM-judge input (pure functions; the script re-exports them for its tests).
"""

from __future__ import annotations

import re
from collections import defaultdict

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Heuristic clustering — cheap signals to narrow the LLM-judge input.
# ---------------------------------------------------------------------------

# Common legal-suffix noise — stripped before token comparison so
# "Acme Pvt Ltd" and "Acme Limited" share their core token set.
_NOISE_SUFFIX_TOKENS = frozenset({
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "co",
    "company", "corp", "corporation", "llp", "llc", "plc", "gmbh", "sa",
    "the", "and", "&", "of",
})

# Per-token punctuation we strip before comparison.
_TOKEN_STRIP_RE = re.compile(r"[^\w]+")


def _normalize_for_cluster(name: str) -> tuple[str, ...]:
    """Tokenize + lowercase + strip noise so 'Mahalaxmi Infrastructure Pvt Ltd'
    and 'Mahalaxmi Infra' both reduce to a comparable token set.

    Returns a tuple of tokens (in original order, deduplicated).
    """
    tokens = []
    seen = set()
    for raw in name.lower().split():
        tok = _TOKEN_STRIP_RE.sub("", raw)
        if not tok or tok in _NOISE_SUFFIX_TOKENS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return tuple(tokens)


def _token_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Jaccard similarity over normalized token sets."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def cluster_candidates(
    rows: list[dict],
    *,
    min_jaccard: float = 0.5,
) -> list[list[dict]]:
    """Group rows that share enough normalized tokens to be worth
    asking the LLM about. Uses simple union-find over a single pass.

    Each input row is dict with at least: id, canonical_name, mention_count.
    """
    # Normalize once
    for r in rows:
        r["_tokens"] = _normalize_for_cluster(r["canonical_name"])

    # Quick reject: rows with empty token set (single noise word, all
    # punctuation, etc.) can't be matched safely.
    candidates = [r for r in rows if r["_tokens"]]

    # Union-find
    parent = {r["id"]: r["id"] for r in candidates}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # O(n^2) within a single entity_type — fine for typical workspace
    # sizes (hundreds, not millions). If you push past 10k+ per type
    # split by leading token first.
    for i, ri in enumerate(candidates):
        for rj in candidates[i + 1:]:
            # Require shared first significant token (cheap pre-filter
            # to avoid quadratic LLM cost on obviously-different rows).
            if ri["_tokens"][0] != rj["_tokens"][0]:
                continue
            if _token_overlap(ri["_tokens"], rj["_tokens"]) >= min_jaccard:
                union(ri["id"], rj["id"])

    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in candidates:
        clusters[find(r["id"])].append(r)
    return [c for c in clusters.values() if len(c) >= 2]


# Backwards-compatible alias — scripts/dedup_canonical_entities.py and its
# unit tests reference the underscore-prefixed name.
_cluster_candidates = cluster_candidates


# ---------------------------------------------------------------------------
# The merge itself.
# ---------------------------------------------------------------------------


async def merge_entity_group(
    conn: Connection,
    *,
    workspace_id: str,
    survivor_id: str,
    loser_ids: list[str],
) -> int:
    """Soft-merge `loser_ids` into `survivor_id` within one workspace.

    MUST run inside a transaction the caller controls (the survivor count
    recompute + loser tombstoning need to commit atomically with the
    repoints). `app.workspace_id` must already be set on the connection.

    Repoints, in order, with per-loser savepoints so one collision can't
    abort the batch:
      - mention_to_entity (PK mention_id; drop loser link if the mention
        already points at the survivor),
      - relationships (UNIQUE subject/object/predicate + CHECK subject<>object
        → drop self-loops + would-be duplicates before repointing),
      - graph_edges (same shape),
      - fact_conflicts (plain repoint).
    Then recomputes survivor.mention_count from live links and stamps
    `merged_into = survivor` on each loser.

    Returns the number of losers merged (0 if loser_ids is empty).
    """
    losers = [lid for lid in loser_ids if lid != survivor_id]
    if not losers:
        return 0

    for loser in losers:
        async with conn.transaction():
            # mention_to_entity — drop loser links whose mention already
            # points at survivor (would conflict on the mention_id PK),
            # then repoint the rest.
            await conn.execute(
                """
                DELETE FROM mention_to_entity
                 WHERE entity_id = %s::uuid
                   AND mention_id IN (
                       SELECT mention_id FROM mention_to_entity
                        WHERE entity_id = %s::uuid
                   )
                """,
                (loser, survivor_id),
            )
            await conn.execute(
                """
                UPDATE mention_to_entity
                   SET entity_id = %s::uuid
                 WHERE entity_id = %s::uuid
                """,
                (survivor_id, loser),
            )

    for loser in losers:
        async with conn.transaction():
            # relationships UNIQUE(workspace_id, subject, object, predicate)
            # AND CHECK (subject <> object). Drop survivor<->loser direct
            # edges first to avoid self-loops.
            await conn.execute(
                """
                DELETE FROM relationships
                 WHERE workspace_id = %s::uuid
                   AND ((subject_entity_id = %s::uuid AND object_entity_id = %s::uuid)
                     OR (subject_entity_id = %s::uuid AND object_entity_id = %s::uuid))
                """,
                (workspace_id, survivor_id, loser, loser, survivor_id),
            )
            # Drop loser-edges that would collide with survivor-edges after repoint.
            await conn.execute(
                """
                DELETE FROM relationships loser
                 USING relationships keep
                 WHERE loser.workspace_id = %s::uuid
                   AND loser.subject_entity_id = %s::uuid
                   AND keep.workspace_id = loser.workspace_id
                   AND keep.subject_entity_id = %s::uuid
                   AND keep.object_entity_id = loser.object_entity_id
                   AND keep.predicate = loser.predicate
                """,
                (workspace_id, loser, survivor_id),
            )
            await conn.execute(
                """
                DELETE FROM relationships loser
                 USING relationships keep
                 WHERE loser.workspace_id = %s::uuid
                   AND loser.object_entity_id = %s::uuid
                   AND keep.workspace_id = loser.workspace_id
                   AND keep.object_entity_id = %s::uuid
                   AND keep.subject_entity_id = loser.subject_entity_id
                   AND keep.predicate = loser.predicate
                """,
                (workspace_id, loser, survivor_id),
            )
            await conn.execute(
                """
                DELETE FROM relationships
                 WHERE workspace_id = %s::uuid
                   AND subject_entity_id = %s::uuid
                   AND object_entity_id = %s::uuid
                """,
                (workspace_id, loser, loser),
            )
            await conn.execute(
                """
                UPDATE relationships SET subject_entity_id = %s::uuid
                 WHERE subject_entity_id = %s::uuid
                """,
                (survivor_id, loser),
            )
            await conn.execute(
                """
                UPDATE relationships SET object_entity_id = %s::uuid
                 WHERE object_entity_id = %s::uuid
                """,
                (survivor_id, loser),
            )
            # graph_edges UNIQUE(workspace_id, src, dst, edge_kind)
            # AND CHECK (src <> dst). Drop direct survivor<->loser edges
            # (self-loops after repoint), then would-be duplicates.
            await conn.execute(
                """
                DELETE FROM graph_edges
                 WHERE workspace_id = %s::uuid
                   AND ((src_entity_id = %s::uuid AND dst_entity_id = %s::uuid)
                     OR (src_entity_id = %s::uuid AND dst_entity_id = %s::uuid))
                """,
                (workspace_id, survivor_id, loser, loser, survivor_id),
            )
            await conn.execute(
                """
                DELETE FROM graph_edges loser
                 USING graph_edges keep
                 WHERE loser.workspace_id = %s::uuid
                   AND loser.src_entity_id = %s::uuid
                   AND keep.workspace_id = loser.workspace_id
                   AND keep.src_entity_id = %s::uuid
                   AND keep.dst_entity_id = loser.dst_entity_id
                   AND keep.edge_kind = loser.edge_kind
                """,
                (workspace_id, loser, survivor_id),
            )
            await conn.execute(
                """
                DELETE FROM graph_edges loser
                 USING graph_edges keep
                 WHERE loser.workspace_id = %s::uuid
                   AND loser.dst_entity_id = %s::uuid
                   AND keep.workspace_id = loser.workspace_id
                   AND keep.dst_entity_id = %s::uuid
                   AND keep.src_entity_id = loser.src_entity_id
                   AND keep.edge_kind = loser.edge_kind
                """,
                (workspace_id, loser, survivor_id),
            )
            await conn.execute(
                """
                DELETE FROM graph_edges
                 WHERE workspace_id = %s::uuid
                   AND src_entity_id = %s::uuid
                   AND dst_entity_id = %s::uuid
                """,
                (workspace_id, loser, loser),
            )
            await conn.execute(
                """
                UPDATE graph_edges SET src_entity_id = %s::uuid
                 WHERE src_entity_id = %s::uuid
                """,
                (survivor_id, loser),
            )
            await conn.execute(
                """
                UPDATE graph_edges SET dst_entity_id = %s::uuid
                 WHERE dst_entity_id = %s::uuid
                """,
                (survivor_id, loser),
            )
            await conn.execute(
                """
                UPDATE fact_conflicts SET entity_id = %s::uuid
                 WHERE entity_id = %s::uuid
                """,
                (survivor_id, loser),
            )

    # Recompute survivor.mention_count from current state.
    await conn.execute(
        """
        UPDATE canonical_entities
           SET mention_count = (
                   SELECT count(*) FROM mention_to_entity
                    WHERE entity_id = %s::uuid
               ),
               updated_at = now()
         WHERE id = %s::uuid
        """,
        (survivor_id, survivor_id),
    )

    # Soft-delete the losers.
    for loser in losers:
        await conn.execute(
            """
            UPDATE canonical_entities
               SET merged_into = %s::uuid,
                   merged_at = now(),
                   updated_at = now()
             WHERE id = %s::uuid
            """,
            (survivor_id, loser),
        )

    return len(losers)
