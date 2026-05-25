"""Backfill source_chunk_id + source_char_start/end on rows extracted
before migration 0032. Read-mostly: rows where the resolver returns
None get left untouched (the UI shows them as "no source location").

Usage:
    source scripts/dev_env.sh
    uv run python scripts/backfill_source_positions.py
"""

from __future__ import annotations

import asyncio
import os

import psycopg

from kb.extraction.source_resolver import resolve


async def backfill_mentions(conn) -> tuple[int, int]:
    cur = await conn.execute(
        """
        SELECT em.id, em.mention_text, c.id, c.text
          FROM extracted_mentions em
          JOIN contextual_chunks cc ON cc.id = em.contextual_chunk_id
          JOIN chunks c ON c.id = cc.chunk_id
         WHERE em.source_chunk_id IS NULL
        """
    )
    rows = await cur.fetchall()
    matched = 0
    for mid, text, chunk_id, chunk_text in rows:
        pos = resolve(text, chunk_text or "")
        if pos is None:
            continue
        await conn.execute(
            "UPDATE extracted_mentions "
            "SET source_chunk_id = %s, source_char_start = %s, source_char_end = %s "
            "WHERE id = %s",
            (chunk_id, pos.char_start, pos.char_end, mid),
        )
        matched += 1
    return matched, len(rows)


async def backfill_proposed_fields(conn) -> tuple[int, int]:
    # Walk every proposed_field that has a value_text but no source link.
    # For each, scan the file's chunks until a match lands.
    cur = await conn.execute(
        """
        SELECT pf.id, pf.file_id, pf.value_text
          FROM proposed_fields pf
         WHERE pf.source_chunk_id IS NULL
           AND pf.value_text IS NOT NULL
           AND pf.value_text <> ''
        """
    )
    rows = await cur.fetchall()
    matched = 0
    chunks_cache: dict[str, list[tuple[str, str]]] = {}
    for pid, file_id, value_text in rows:
        if file_id not in chunks_cache:
            ccur = await conn.execute(
                "SELECT id::text, text FROM chunks WHERE file_id = %s "
                "ORDER BY chunk_index ASC",
                (file_id,),
            )
            chunks_cache[file_id] = [
                (r[0], r[1] or "") for r in await ccur.fetchall()
            ]
        for cid, ctext in chunks_cache[file_id]:
            pos = resolve(value_text, ctext)
            if pos:
                await conn.execute(
                    "UPDATE proposed_fields "
                    "SET source_chunk_id = %s, source_char_start = %s, "
                    "source_char_end = %s WHERE id = %s",
                    (cid, pos.char_start, pos.char_end, pid),
                )
                matched += 1
                break
    return matched, len(rows)


async def backfill_atomic_units(conn) -> tuple[int, int]:
    cur = await conn.execute(
        """
        SELECT au.id, au.file_id, au.parameters
          FROM atomic_units au
         WHERE au.source_chunk_id IS NULL
        """
    )
    rows = await cur.fetchall()
    matched = 0
    chunks_cache: dict[str, list[tuple[str, str]]] = {}
    for uid, file_id, params in rows:
        summary = (params or {}).get("summary") if isinstance(params, dict) else None
        if not isinstance(summary, str) or not summary.strip():
            continue
        if file_id not in chunks_cache:
            ccur = await conn.execute(
                "SELECT id::text, text FROM chunks WHERE file_id = %s "
                "ORDER BY chunk_index ASC",
                (file_id,),
            )
            chunks_cache[file_id] = [
                (r[0], r[1] or "") for r in await ccur.fetchall()
            ]
        for cid, ctext in chunks_cache[file_id]:
            pos = resolve(summary, ctext)
            if pos:
                await conn.execute(
                    "UPDATE atomic_units "
                    "SET source_chunk_id = %s, source_char_start = %s, "
                    "source_char_end = %s WHERE id = %s",
                    (cid, pos.char_start, pos.char_end, uid),
                )
                matched += 1
                break
    return matched, len(rows)


async def backfill_triples(conn) -> tuple[int, int]:
    # Pre-PR2 triples stored chunk_id = contextual_chunks.id; PR2-and-later
    # store chunk_id = chunks.id directly. Resolve via either join, and
    # also REPOINT existing rows to the canonical chunks.id so the
    # list_triples_in_doc SQL (which JOINs on chunks.id) renders pages.
    cur = await conn.execute(
        """
        SELECT t.id, t.subject_text, t.object_text,
               COALESCE(direct.id, src.id)::text AS canonical_chunk_id,
               COALESCE(direct.text, src.text)   AS chunk_text
          FROM extracted_triples t
          LEFT JOIN chunks direct ON direct.id = t.chunk_id
          LEFT JOIN contextual_chunks cc ON cc.id = t.chunk_id
          LEFT JOIN chunks src ON src.id = cc.chunk_id
         WHERE t.subject_char_start IS NULL
        """
    )
    rows = await cur.fetchall()
    matched = 0
    for tid, subj, obj, canonical_chunk_id, ctext in rows:
        if not canonical_chunk_id or not ctext:
            continue
        s = resolve(subj, ctext)
        o = resolve(obj, ctext)
        await conn.execute(
            "UPDATE extracted_triples SET "
            "chunk_id = %s, "
            "subject_char_start = %s, subject_char_end = %s, "
            "object_char_start = %s, object_char_end = %s "
            "WHERE id = %s",
            (
                canonical_chunk_id,
                s.char_start if s else None, s.char_end if s else None,
                o.char_start if o else None, o.char_end if o else None,
                tid,
            ),
        )
        if s or o:
            matched += 1
    return matched, len(rows)


async def main() -> int:
    dsn = os.environ.get("KB_DATABASE_URL")
    if not dsn:
        print("[backfill] KB_DATABASE_URL not set", flush=True)
        return 1
    print(f"[backfill] connecting to {dsn.split('@')[-1]}", flush=True)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=False) as conn:
        for label, fn in (
            ("mentions",         backfill_mentions),
            ("proposed_fields",  backfill_proposed_fields),
            ("atomic_units",     backfill_atomic_units),
            ("triples",          backfill_triples),
        ):
            matched, total = await fn(conn)
            print(f"[backfill] {label}: {matched}/{total} rows resolved", flush=True)
        await conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
