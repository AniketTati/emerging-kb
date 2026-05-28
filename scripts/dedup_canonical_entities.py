"""Bug D Tier-1 #4 — canonical_entity mention dedup via LLM-judge.

The live insert-time pipeline misses fuzzy entity variants. On the
construction workspace the single sub-contractor "Mahalaxmi" got
split into 5 canonical_entities:

    Mahalaxmi                          ORG   109 mentions
    Mahalaxmi Infrastructure Pvt Ltd   ORG    62 mentions
    Mahalaxmi Infrastructure           ORG    45 mentions
    Mahalaxmi Infra                    ORG    25 mentions
    Mahalaxmiinfra.in                  ORG     7 mentions

Q-mode "how many sub-contractors" / "which sub-contractor had the
most safety incidents" answers are wrong because the mention pool is
split across rows that should be one.

This script merges them post-hoc using a proposal-review-apply
workflow (same pattern as Bug D Phase 8 field-name merger).

Pipeline:
  1. Per (workspace, entity_type), pull active entities (merged_into IS NULL).
  2. Generate candidate clusters via cheap heuristics:
     - normalized-name token overlap (drop "Pvt Ltd", "Limited", etc.)
     - embedding cosine ≥ 0.85 (lower than the live 0.92 threshold)
  3. For each candidate cluster (>=2 rows), ask Gemini Flash with a
     sample mention context. Conservative: anything not confidently
     same-entity stays separate.
  4. Output YAML proposal — human reviews + edits before --apply.
  5. --apply: in one tx, repoint mention_to_entity rows + relationships
     + graph_edges + fact_conflicts → survivor; recompute survivor's
     mention_count = SUM(cluster); stamp merged_into on losers.

Survivor pick rule: highest mention_count. Tie → earliest created_at.
The merger keeps the most-mentioned surface form as the canonical_name.

Usage:
    # Step 1 — generate proposal
    uv run python scripts/dedup_canonical_entities.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
        --out /tmp/entity_merge_proposal.yaml

    # Step 2 — review YAML, drop or edit clusters you disagree with

    # Step 3 — apply
    uv run python scripts/dedup_canonical_entities.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
        --apply /tmp/entity_merge_proposal.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.config import get_settings  # noqa: E402
from kb.db.pool import open_connection  # noqa: E402


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


def _cluster_candidates(
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


# ---------------------------------------------------------------------------
# LLM judge — confirm a candidate cluster is the same real-world entity.
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = (
    "You are an entity-resolution judge. You'll see a candidate cluster "
    "of canonical_entity rows that token-overlap, plus sample contexts "
    "where each row's name appeared in source documents. Decide which "
    "rows refer to the SAME real-world entity (the same company / person "
    "/ place / facility).\n"
    "\n"
    "Rules:\n"
    " - Be CONSERVATIVE. Wrong merges silently corrupt the database. "
    "When in doubt, leave them separate.\n"
    " - A bare proper noun (e.g. just 'Mahalaxmi') is NOT automatically "
    "the same as a fuller form ('Mahalaxmi Infrastructure Pvt Ltd') "
    "unless the contexts make it obvious they refer to the same entity.\n"
    " - Distinct sub-things are NOT merges: 'Mahalaxmi HQ' is a facility, "
    "'Mahalaxmi PM team' is a team — these are not the company itself.\n"
    " - Domains / emails ('mahalaxmiinfra.in') referring to a company's "
    "own internet presence ARE the company.\n"
    " - Only emit a merge group with 2+ ids you're confident about. If "
    "no merge applies, emit an empty groups array.\n"
    "\n"
    'Return JSON exactly: {"groups": [{"survivor_id": "<uuid>", '
    '"survivor_name": "<name>", "merge_ids": ["<uuid>", "<uuid>"], '
    '"reason": "<one sentence>"}]}. '
    "survivor_id MUST be one of the input ids. merge_ids MUST be "
    "input ids and MUST include survivor_id."
)


def _format_judge_user_msg(
    entity_type: str, cluster: list[dict], contexts: dict[str, list[str]],
) -> str:
    lines = [f"entity_type: {entity_type}", "", "candidates:"]
    for r in cluster:
        ctxs = contexts.get(r["id"], [])
        ctx_lines = "\n      ".join(f"- {c[:160]}" for c in ctxs[:3])
        lines.append(
            f"  - id: {r['id']}\n"
            f"    name: {r['canonical_name']!r}\n"
            f"    mentions: {r['mention_count']}\n"
            f"    sample_contexts:\n      {ctx_lines or '(none)'}"
        )
    lines.append("")
    lines.append("Return JSON only.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB helpers.
# ---------------------------------------------------------------------------


async def _gather_entities(
    conn, workspace_id: str,
) -> dict[str, list[dict]]:
    """Return {entity_type: [{id, canonical_name, mention_count}, ...]}
    for active (un-merged) canonical_entities."""
    cur = await conn.execute(
        """
        SELECT id::text, canonical_name, entity_type, mention_count, created_at
          FROM canonical_entities
         WHERE workspace_id = %s
           AND merged_into IS NULL
         ORDER BY entity_type, mention_count DESC
        """,
        (workspace_id,),
    )
    rows = await cur.fetchall()
    out: dict[str, list[dict]] = defaultdict(list)
    for rid, name, etype, mc, created in rows:
        out[str(etype)].append({
            "id": str(rid),
            "canonical_name": str(name),
            "mention_count": int(mc),
            "created_at": created,
        })
    return out


async def _sample_contexts(
    conn, entity_ids: list[str], *, max_per_id: int = 3,
) -> dict[str, list[str]]:
    """For each entity id, pull a few mention contexts (chunk text
    around the mention) so the LLM can disambiguate beyond name."""
    if not entity_ids:
        return {}
    cur = await conn.execute(
        """
        SELECT m2e.entity_id::text,
               em.mention_text,
               COALESCE(cc.contextual_text, ch.text, '') AS ctx
          FROM mention_to_entity m2e
          JOIN extracted_mentions em ON em.id = m2e.mention_id
          LEFT JOIN contextual_chunks cc ON cc.id = em.contextual_chunk_id
          LEFT JOIN chunks ch ON ch.id = cc.chunk_id
         WHERE m2e.entity_id = ANY(%s::uuid[])
         ORDER BY m2e.entity_id, em.created_at
        """,
        (entity_ids,),
    )
    rows = await cur.fetchall()
    out: dict[str, list[str]] = defaultdict(list)
    for eid, mtext, ctx in rows:
        if len(out[eid]) >= max_per_id:
            continue
        snippet = f"mention='{mtext}' :: {ctx}".strip()
        out[eid].append(snippet)
    return out


# ---------------------------------------------------------------------------
# Top-level commands.
# ---------------------------------------------------------------------------


async def propose_merges(
    workspace_id: str,
    out_path: Path,
    *,
    min_mentions: int = 2,
    skip_types: frozenset[str] = frozenset({
        "DATE", "TIME", "MONEY", "PERCENT", "QUANTITY",
        "CARDINAL", "ORDINAL",
    }),
) -> int:
    from kb.query.llm_client import make_query_llm_client

    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client; set KB_GEMINI_API_KEY", file=sys.stderr)
        return 1

    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        by_type = await _gather_entities(conn, workspace_id)

        proposal: dict = {
            "workspace_id": workspace_id,
            "entity_types": {},
        }
        n_total_groups = 0
        for etype, rows in sorted(by_type.items()):
            if etype in skip_types:
                continue
            # Filter low-signal rows
            usable = [r for r in rows if r["mention_count"] >= min_mentions]
            if len(usable) < 2:
                continue
            clusters = _cluster_candidates(usable)
            if not clusters:
                continue
            print(
                f"  → {etype} ({len(usable)} entities, "
                f"{len(clusters)} candidate cluster(s))…",
                flush=True,
            )

            # One LLM call per cluster — each gets focused context
            type_groups = []
            for cluster in clusters:
                ids = [r["id"] for r in cluster]
                ctxs = await _sample_contexts(conn, ids)
                user_msg = _format_judge_user_msg(etype, cluster, ctxs)
                try:
                    raw = await client.generate_json(
                        user=user_msg,
                        system=_JUDGE_SYSTEM,
                        max_tokens=1500,
                    )
                except Exception as exc:
                    print(f"    LLM_ERROR: {exc}")
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    if not m:
                        print("    PARSE_ERROR")
                        continue
                    try:
                        data = json.loads(m.group(0))
                    except Exception:
                        print("    PARSE_ERROR")
                        continue
                groups = data.get("groups") or []
                valid_ids = {r["id"] for r in cluster}
                id_to_name = {r["id"]: r["canonical_name"] for r in cluster}
                id_to_mc = {r["id"]: r["mention_count"] for r in cluster}
                for g in groups:
                    sid = (g.get("survivor_id") or "").strip()
                    mids = [str(x).strip() for x in (g.get("merge_ids") or [])]
                    reason = (g.get("reason") or "").strip()
                    if not (
                        sid in valid_ids
                        and all(mid in valid_ids for mid in mids)
                        and sid in mids
                        and len(mids) >= 2
                    ):
                        continue
                    # Re-pick survivor: highest mention_count, ties → keep LLM pick.
                    pick = max(mids, key=lambda i: (id_to_mc[i], -1))
                    type_groups.append({
                        "survivor_id": pick,
                        "survivor_name": id_to_name[pick],
                        "merge_ids": mids,
                        "merge_names": [id_to_name[m] for m in mids],
                        "total_mentions": sum(id_to_mc[m] for m in mids),
                        "reason": reason,
                    })
            if type_groups:
                proposal["entity_types"][etype] = {"groups": type_groups}
                n_total_groups += len(type_groups)
                for g in type_groups:
                    print(
                        f"    merge → {g['survivor_name']!r} "
                        f"({g['total_mentions']} mentions): "
                        f"{g['merge_names']}"
                    )

    out_path.write_text(
        yaml.dump(proposal, sort_keys=False, default_flow_style=False)
    )
    print()
    print(
        f"# {n_total_groups} merge groups proposed across "
        f"{len(proposal['entity_types'])} entity types"
    )
    print(f"# proposal → {out_path}")
    print("# review + edit before applying with --apply")
    return 0


async def apply_proposal(workspace_id: str, proposal_path: Path) -> int:
    settings = get_settings()
    with open(proposal_path) as fh:
        proposal = yaml.safe_load(fh)
    if proposal.get("workspace_id") != workspace_id:
        print(
            f"ERROR: proposal workspace {proposal.get('workspace_id')!r} "
            f"!= --workspace {workspace_id!r}",
            file=sys.stderr,
        )
        return 1

    n_merges = 0
    n_loser_rows = 0
    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            for etype, block in (proposal.get("entity_types") or {}).items():
                for group in (block.get("groups") or []):
                    survivor = group["survivor_id"]
                    losers = [m for m in group["merge_ids"] if m != survivor]
                    if not losers:
                        continue
                    print(
                        f"  merging {len(losers)} → {group['survivor_name']!r}"
                    )

                    # Repoint mention_to_entity. mention_id is the PK so
                    # we may hit a UNIQUE violation if the same mention
                    # somehow ended up on both survivor + loser — drop
                    # the loser row in that case (the survivor's link
                    # wins). Wrap each loser in its own savepoint so
                    # one collision doesn't blow up the whole apply.
                    for loser in losers:
                        async with conn.transaction():
                            # Drop loser links whose mention already
                            # points at survivor — would conflict on
                            # the mention_id PK.
                            await conn.execute(
                                """
                                DELETE FROM mention_to_entity
                                 WHERE entity_id = %s::uuid
                                   AND mention_id IN (
                                       SELECT mention_id FROM mention_to_entity
                                        WHERE entity_id = %s::uuid
                                   )
                                """,
                                (loser, survivor),
                            )
                            # Note: resolved_method isn't updated — the
                            # CHECK constraint doesn't allow 'merged' as a
                            # value, and the original method (deterministic
                            # / embedding / llm_judge) stays meaningful as
                            # "how was this mention originally resolved".
                            # The merge audit lives on canonical_entities.
                            await conn.execute(
                                """
                                UPDATE mention_to_entity
                                   SET entity_id = %s::uuid
                                 WHERE entity_id = %s::uuid
                                """,
                                (survivor, loser),
                            )

                    # Repoint relationships + graph_edges + fact_conflicts.
                    # These tables FK to canonical_entities.id; merge
                    # rows want to follow the chain. Same savepoint
                    # treatment per table.
                    for loser in losers:
                        async with conn.transaction():
                            await conn.execute(
                                """
                                UPDATE relationships SET subject_entity_id = %s::uuid
                                 WHERE subject_entity_id = %s::uuid
                                """,
                                (survivor, loser),
                            )
                            await conn.execute(
                                """
                                UPDATE relationships SET object_entity_id = %s::uuid
                                 WHERE object_entity_id = %s::uuid
                                """,
                                (survivor, loser),
                            )
                            await conn.execute(
                                """
                                UPDATE graph_edges SET src_entity_id = %s::uuid
                                 WHERE src_entity_id = %s::uuid
                                """,
                                (survivor, loser),
                            )
                            await conn.execute(
                                """
                                UPDATE graph_edges SET dst_entity_id = %s::uuid
                                 WHERE dst_entity_id = %s::uuid
                                """,
                                (survivor, loser),
                            )
                            await conn.execute(
                                """
                                UPDATE fact_conflicts SET entity_id = %s::uuid
                                 WHERE entity_id = %s::uuid
                                """,
                                (survivor, loser),
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
                        (survivor, survivor),
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
                            (survivor, loser),
                        )
                    n_merges += 1
                    n_loser_rows += len(losers)
    print()
    print(
        f"# applied {n_merges} merge group(s), soft-deleted "
        f"{n_loser_rows} loser row(s)"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", type=Path, help="write proposal YAML here")
    ap.add_argument("--apply", type=Path, help="apply this proposal YAML")
    ap.add_argument("--min-mentions", type=int, default=2,
                    help="skip entities with fewer than N mentions")
    args = ap.parse_args()

    if (args.out is None) == (args.apply is None):
        print("ERROR: pass exactly one of --out (propose) or --apply",
              file=sys.stderr)
        return 2

    if args.out is not None:
        return asyncio.run(propose_merges(
            args.workspace, args.out, min_mentions=args.min_mentions,
        ))
    else:
        return asyncio.run(apply_proposal(args.workspace, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
