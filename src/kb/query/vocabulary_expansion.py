"""Wave A close-up — Design 6 query-time vocabulary expansion.

Architecture §6 step 2.5: tokenize the (rewritten) queries, look up
`domain_vocabulary` for synonyms + acronym expansions, and augment the
BM25 channel's query string so retrieval matches both the user's
phrasing and the canonical synonyms.

Pre-fix: `domain_vocabulary` table existed (migration 0021) and the
discovery pipeline was scaffolded (`extraction/vocabulary.py`), but the
table was never populated AND no query-time lookup ran. Result: when
the user asks for "the non-compete" but the doc says "non-competition
clause", retrieval ranks the doc lower than it should.

This module:
  - Tokenizes the query into terms (lowercase, alphanumeric runs).
  - Looks up `domain_vocabulary` rows where `lower(canonical_term)`
    matches a token OR a token appears in `synonyms[]`.
  - Returns an augmented query that ORs the original with each
    discovered synonym group, plus inline acronym expansions
    ("GST" → "GST OR Goods and Services Tax").

Best-effort: when the lookup errors (missing table, RLS denied, etc.)
we return the original query unchanged. Vocabulary expansion is a
recall booster, not a correctness invariant — failure here must
never break a chat call.
"""

from __future__ import annotations

import os
import re
from typing import Any


# Lowercase alphanumeric tokens, hyphen + underscore allowed inside.
# Picks up "non-compete", "msa", "q1", but skips punctuation.
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_\-]{1,40}", re.IGNORECASE)


def _tokenize(query: str) -> list[str]:
    """Lowercase alphanumeric tokens (≥ 2 chars) for vocab lookup."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _TOKEN_RE.finditer(query or ""):
        t = m.group(0).lower()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _default_domain_id(workspace_id: str) -> str:
    """Pick the domain scope for vocab lookup. Matches the worker's
    discovery-side fallback so reads + writes land in the same bucket.

    Order:
      1. KB_DEFAULT_DOMAIN env var (operator override).
      2. workspace-scoped sentinel `workspace:<uuid>`.
    """
    explicit = os.environ.get("KB_DEFAULT_DOMAIN")
    return explicit if explicit else f"workspace:{workspace_id}"


async def expand_query_with_vocabulary(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    domain_id: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return (augmented_query, expansions[]) for the BM25 channel.

    `expansions[]` records each (canonical_term, synonyms_used) hit so
    the plan inspector can surface "expanded `non_compete` (vocab
    v_88)" exactly as architecture §6 step 2.5 describes.

    Strategy:
      * Tokenize the input query.
      * SELECT rows where lower(canonical_term) = ANY(tokens) OR
        synonyms && tokens — pulls matches by either side of the pair.
      * Build OR groups: each row's canonical_term + every synonym
        joined with " OR ".
      * Concatenate the original query + every group, separated by " ".
        Tantivy treats the result as an OR-of-terms which gives the
        recall boost without needing to re-rank.

    Best-effort: if anything fails, return the original query + empty
    expansions list.
    """
    if not (query or "").strip() or conn is None:
        return query, []

    tokens = _tokenize(query)
    if not tokens:
        return query, []

    domain = domain_id or _default_domain_id(workspace_id)

    # SAVEPOINT-wrap so a missing table (older deployment) doesn't
    # poison the outer txn the orchestrator uses for everything else.
    sp_open = False
    try:
        await conn.execute("SAVEPOINT vocab_expansion_lookup")
        sp_open = True
    except Exception:
        pass

    rows: list[tuple] = []
    try:
        cur = await conn.execute(
            """
            SELECT canonical_term, synonyms, acronym_of, expansion
              FROM domain_vocabulary
             WHERE domain_id = %s
               AND active = true
               AND (
                   lower(canonical_term) = ANY(%s)
                   OR synonyms && %s::text[]
               )
            """,
            (domain, tokens, tokens),
        )
        rows = await cur.fetchall()
        if sp_open:
            try:
                await conn.execute(
                    "RELEASE SAVEPOINT vocab_expansion_lookup",
                )
            except Exception:
                pass
    except Exception:
        if sp_open:
            try:
                await conn.execute(
                    "ROLLBACK TO SAVEPOINT vocab_expansion_lookup",
                )
                await conn.execute(
                    "RELEASE SAVEPOINT vocab_expansion_lookup",
                )
            except Exception:
                pass
        return query, []

    if not rows:
        return query, []

    expansions: list[dict[str, Any]] = []
    or_groups: list[str] = []

    for canonical, synonyms, acronym_of, expansion in rows:
        members: list[str] = []
        if canonical:
            members.append(canonical)
        for s in (synonyms or []):
            if s and s not in members:
                members.append(s)
        # For acronym entries (canonical="GST", expansion="Goods and
        # Services Tax"), also include the expansion as a member so
        # docs that wrote out the full form get matched.
        if expansion and expansion not in members:
            members.append(expansion)

        if len(members) < 2:
            # No actual expansion to do — single term either way.
            continue

        # Quote multi-word members so Tantivy treats them as phrases.
        # Single-word members stay bare for BM25 fan-out.
        quoted = []
        for m in members:
            if " " in m.strip():
                quoted.append(f'"{m}"')
            else:
                quoted.append(m)
        or_groups.append("(" + " OR ".join(quoted) + ")")
        expansions.append({
            "canonical_term": canonical,
            "synonyms": list(synonyms or []),
            "acronym_of": acronym_of,
            "expansion": expansion,
        })

    if not or_groups:
        return query, []

    augmented = query + " " + " ".join(or_groups)
    return augmented, expansions
