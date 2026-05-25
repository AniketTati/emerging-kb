"""WA-2 / Design 6 — domain_vocabulary repo.

Three concerns:
  - CRUD: insert / update / soft-disable / read for the Vocabulary UI tab
    + the discovery worker.
  - Query-time lookups: synonyms-of-term + acronym-expand-of-term + HNSW
    embedding fallback. Consumed by WA-9's query expander.
  - Discovery: append-or-merge semantics so the same (domain, term) seen
    again raises `n_docs_observed` + merges new synonyms rather than
    inserting a duplicate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from kb.db.pool import Connection


@dataclass(frozen=True)
class VocabRecord:
    id: str
    domain_id: str
    canonical_term: str
    synonyms: list[str]
    acronym_of: str | None
    expansion: str | None
    definition: str | None
    embedding: list[float] | None
    source: str
    confidence: float
    n_docs_observed: int
    active: bool
    created_at: str
    updated_at: str


def _row_to_record(row: tuple) -> VocabRecord:
    # embedding column is halfvec; psycopg returns it as a string like
    # '[1,2,3]'. We don't parse it back to floats in the read path
    # (callers that want the vector should use the HNSW lookup).
    return VocabRecord(
        id=str(row[0]),
        domain_id=str(row[1]),
        canonical_term=str(row[2]),
        synonyms=list(row[3] or []),
        acronym_of=row[4],
        expansion=row[5],
        definition=row[6],
        embedding=None,  # not parsed back from halfvec string
        source=str(row[7]),
        confidence=float(row[8]),
        n_docs_observed=int(row[9]),
        active=bool(row[10]),
        created_at=row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
        updated_at=row[12].isoformat() if hasattr(row[12], "isoformat") else str(row[12]),
    )


_SELECT_COLUMNS = (
    "id, domain_id, canonical_term, synonyms, acronym_of, expansion, "
    "definition, source, confidence, n_docs_observed, active, "
    "created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_vocabulary(
    conn: Connection,
    *,
    domain_id: str,
    include_inactive: bool = False,
    limit: int = 200,
) -> list[VocabRecord]:
    """All vocab entries for a domain. Used by the Vocabulary UI tab."""
    if include_inactive:
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM domain_vocabulary "
            "WHERE domain_id = %s ORDER BY canonical_term ASC LIMIT %s"
        )
        params: tuple = (domain_id, limit)
    else:
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM domain_vocabulary "
            "WHERE domain_id = %s AND active = true "
            "ORDER BY canonical_term ASC LIMIT %s"
        )
        params = (domain_id, limit)
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return [_row_to_record(r) for r in rows]


async def get_vocabulary(
    conn: Connection,
    *,
    domain_id: str,
    canonical_term: str,
) -> VocabRecord | None:
    """Single-row lookup (case-insensitive)."""
    cur = await conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM domain_vocabulary "
        "WHERE domain_id = %s AND lower(canonical_term) = lower(%s) "
        "AND active = true LIMIT 1",
        (domain_id, canonical_term),
    )
    row = await cur.fetchone()
    return _row_to_record(row) if row else None


async def resolve_synonyms_for_term(
    conn: Connection,
    *,
    domain_id: str,
    term: str,
) -> list[str]:
    """Architecture §6 step 2.5 hot path. Returns the synonyms list for the
    term if a vocab entry exists, otherwise []."""
    cur = await conn.execute(
        "SELECT synonyms FROM domain_vocabulary "
        "WHERE domain_id = %s AND lower(canonical_term) = lower(%s) "
        "AND active = true LIMIT 1",
        (domain_id, term),
    )
    row = await cur.fetchone()
    return list(row[0]) if row and row[0] else []


async def expand_acronym(
    conn: Connection,
    *,
    domain_id: str,
    short_form: str,
) -> str | None:
    """Architecture §6 step 2.5: 'GST' → 'Goods and Services Tax'."""
    cur = await conn.execute(
        "SELECT expansion FROM domain_vocabulary "
        "WHERE domain_id = %s AND lower(canonical_term) = lower(%s) "
        "AND acronym_of IS NOT NULL AND active = true LIMIT 1",
        (domain_id, short_form),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def embedding_similar_terms(
    conn: Connection,
    *,
    domain_id: str,
    query_vector: list[float],
    top_k: int = 5,
    threshold: float = 0.85,
) -> list[tuple[str, str, float]]:
    """HNSW lookup — returns [(id, canonical_term, cosine_similarity), ...]
    above `threshold`. Used by WA-9's soft-expansion path."""
    vector_str = "[" + ",".join(str(float(v)) for v in query_vector) + "]"
    cur = await conn.execute(
        """
        SELECT id::text, canonical_term,
               1 - (embedding <=> %s::halfvec) AS sim
          FROM domain_vocabulary
         WHERE domain_id = %s
           AND embedding IS NOT NULL
           AND active = true
           AND 1 - (embedding <=> %s::halfvec) >= %s
         ORDER BY embedding <=> %s::halfvec ASC
         LIMIT %s
        """,
        (vector_str, domain_id, vector_str, threshold, vector_str, top_k),
    )
    rows = await cur.fetchall()
    return [(str(r[0]), str(r[1]), float(r[2])) for r in rows]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def upsert_vocabulary(
    conn: Connection,
    *,
    domain_id: str,
    canonical_term: str,
    synonyms: Iterable[str] = (),
    acronym_of: str | None = None,
    expansion: str | None = None,
    definition: str | None = None,
    embedding: list[float] | None = None,
    source: str = "discovered",
    confidence: float = 1.0,
    n_docs_observed: int = 0,
) -> str:
    """Append-or-merge semantics. If a row exists for (domain, term):
       - synonyms are union'd
       - n_docs_observed is incremented (not replaced)
       - confidence is updated to MAX(existing, new)
       - definition / expansion are updated only if the new value is non-None
       - source is upgraded (user_defined > imported > discovered)
       - embedding is updated only if new is supplied
    Returns the row id."""
    syn_list = list(synonyms)
    embedding_str: str | None = None
    if embedding is not None:
        embedding_str = "[" + ",".join(str(float(v)) for v in embedding) + "]"

    cur = await conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM domain_vocabulary "
        "WHERE domain_id = %s AND lower(canonical_term) = lower(%s) LIMIT 1",
        (domain_id, canonical_term),
    )
    existing_row = await cur.fetchone()

    if existing_row is None:
        # Fresh insert. Branch in Python so PG can infer types — passing
        # NULL through CASE-WHEN-%s-IS-NULL leaves the type indeterminate.
        if embedding_str is None:
            cur = await conn.execute(
                """
                INSERT INTO domain_vocabulary (
                    domain_id, canonical_term, synonyms, acronym_of, expansion,
                    definition, source, confidence, n_docs_observed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    domain_id, canonical_term, syn_list, acronym_of, expansion,
                    definition, source, confidence, n_docs_observed,
                ),
            )
        else:
            cur = await conn.execute(
                """
                INSERT INTO domain_vocabulary (
                    domain_id, canonical_term, synonyms, acronym_of, expansion,
                    definition, embedding, source, confidence, n_docs_observed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::halfvec, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    domain_id, canonical_term, syn_list, acronym_of, expansion,
                    definition, embedding_str,
                    source, confidence, n_docs_observed,
                ),
            )
        row = await cur.fetchone()
        assert row is not None
        return str(row[0])

    existing = _row_to_record(existing_row)
    merged_synonyms = sorted(set(existing.synonyms) | set(syn_list))

    # source upgrade rules: user_defined > imported > discovered
    source_rank = {"discovered": 0, "imported": 1, "user_defined": 2}
    if source_rank.get(source, 0) >= source_rank.get(existing.source, 0):
        new_source = source
    else:
        new_source = existing.source

    if embedding_str is None:
        await conn.execute(
            """
            UPDATE domain_vocabulary
               SET synonyms        = %s,
                   acronym_of      = COALESCE(%s, acronym_of),
                   expansion       = COALESCE(%s, expansion),
                   definition      = COALESCE(%s, definition),
                   source          = %s,
                   confidence      = GREATEST(confidence, %s),
                   n_docs_observed = n_docs_observed + %s,
                   updated_at      = NOW(),
                   active          = true
             WHERE id = %s
            """,
            (
                merged_synonyms,
                acronym_of, expansion, definition,
                new_source, confidence, n_docs_observed,
                existing.id,
            ),
        )
    else:
        await conn.execute(
            """
            UPDATE domain_vocabulary
               SET synonyms        = %s,
                   acronym_of      = COALESCE(%s, acronym_of),
                   expansion       = COALESCE(%s, expansion),
                   definition      = COALESCE(%s, definition),
                   embedding       = %s::halfvec,
                   source          = %s,
                   confidence      = GREATEST(confidence, %s),
                   n_docs_observed = n_docs_observed + %s,
                   updated_at      = NOW(),
                   active          = true
             WHERE id = %s
            """,
            (
                merged_synonyms,
                acronym_of, expansion, definition,
                embedding_str,
                new_source, confidence, n_docs_observed,
                existing.id,
            ),
        )
    return existing.id


async def set_active(
    conn: Connection,
    *,
    vocab_id: str,
    active: bool,
) -> bool:
    """Soft-toggle. Returns True if a row was changed."""
    cur = await conn.execute(
        "UPDATE domain_vocabulary SET active = %s, updated_at = NOW() "
        "WHERE id = %s",
        (active, vocab_id),
    )
    return getattr(cur, "rowcount", 0) > 0


async def update_definition(
    conn: Connection,
    *,
    vocab_id: str,
    definition: str,
) -> bool:
    """User edits the definition field via the Vocabulary UI tab."""
    cur = await conn.execute(
        "UPDATE domain_vocabulary SET definition = %s, updated_at = NOW() "
        "WHERE id = %s",
        (definition, vocab_id),
    )
    return getattr(cur, "rowcount", 0) > 0
