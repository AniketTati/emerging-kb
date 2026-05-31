"""Review the ingestion result for a workspace (and optionally one file).

    source scripts/dev_env.sh
    python scripts/review_ingest.py <workspace_id> [file_id]

Dumps the per-doc artifacts (lifecycle, chunks, proposed_fields, doc_root
fields, children, mentions) and the cross-doc state (emergent schema +
promotion, canonical entities + resolution, doc chains) so we can eyeball
whether linking / schema-evolution / numeric-typing behaved as expected.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import psycopg

DB = os.environ["KB_DATABASE_URL"]


def _h(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


async def per_doc(conn, file_id: str) -> None:
    cur = await conn.execute(
        "SELECT name, lifecycle_state, inferred_doc_type, doc_status, "
        "       extraction_degraded, extraction_coverage "
        "FROM files WHERE id = %s", (file_id,))
    row = await cur.fetchone()
    if not row:
        print(f"!! file {file_id} not found")
        return
    name, lc, dt, ds, degraded, cov = row
    _h(f"PER-DOC  {name}")
    print(f"  lifecycle={lc}  doc_type={dt}  doc_status={ds}  degraded={degraded}")
    if cov:
        print(f"  coverage={cov}")

    for tbl, where in [
        ("raw_pages", "file_id = %s"),
        ("chunks", "file_id = %s"),
        ("contextual_chunks", "file_id = %s"),
        ("chunk_embeddings", "file_id = %s"),
        ("extracted_mentions", "file_id = %s"),
    ]:
        cur = await conn.execute(f"SELECT count(*) FROM {tbl} WHERE {where}", (file_id,))
        print(f"  {tbl:20s} {(await cur.fetchone())[0]}")
    cur = await conn.execute(
        "SELECT node_level, count(*) FROM chunks WHERE file_id=%s GROUP BY node_level ORDER BY node_level",
        (file_id,))
    levels = {lv: c for lv, c in await cur.fetchall()}
    print(f"  chunks by node_level (0=leaf,1=mid,2=root): {levels}")

    _h("  proposed_fields (name | value_text | value_numeric | source)")
    cur = await conn.execute(
        "SELECT field_name, value_text, value_numeric, model_id "
        "FROM proposed_fields WHERE file_id=%s ORDER BY model_id, field_name", (file_id,))
    for fn, vt, vn, mid in await cur.fetchall():
        src = "frontmatter" if mid == "frontmatter:auto" else "body"
        num = f"  [num={vn}]" if vn is not None else ""
        print(f"    {fn:28s} = {str(vt)[:40]:40s} ({src}){num}")

    _h("  doc_root extracted_entities.fields (the QUERYABLE record)")
    cur = await conn.execute(
        "SELECT fields FROM extracted_entities WHERE file_id=%s AND unit_type IS NULL", (file_id,))
    roots = await cur.fetchall()
    if not roots:
        print("    (no doc_root!)")
    for (f,) in roots:
        print(f"    {json.dumps(f, default=str)}")

    cur = await conn.execute(
        "SELECT unit_type, count(*), (array_agg(fields))[1] FROM extracted_entities "
        "WHERE file_id=%s AND unit_type IS NOT NULL GROUP BY unit_type", (file_id,))
    kids = await cur.fetchall()
    if kids:
        _h("  child rows (unit_type | count | sample)")
        for ut, c, sample in kids:
            print(f"    {ut:20s} x{c}  e.g. {json.dumps(sample, default=str)[:80]}")

    cur = await conn.execute(
        "SELECT mention_type, count(*) FROM extracted_mentions WHERE file_id=%s "
        "GROUP BY mention_type ORDER BY count(*) DESC", (file_id,))
    mt = await cur.fetchall()
    if mt:
        print("  mentions by type:", {t: c for t, c in mt})


async def cross_doc(conn, ws: str) -> None:
    _h("WORKSPACE — files")
    cur = await conn.execute(
        "SELECT name, lifecycle_state, inferred_doc_type, doc_status, extraction_degraded "
        "FROM files WHERE workspace_id=%s AND lifecycle_state<>'deleted' ORDER BY created_at", (ws,))
    for n, lc, dt, ds, dg in await cur.fetchall():
        flag = "  ⚠DEGRADED" if dg else ""
        print(f"  {n:36s} {lc:14s} {str(dt):16s} {str(ds):12s}{flag}")

    _h("EMERGENT SCHEMA per doc_type (inferred fields → promotion)")
    cur = await conn.execute(
        "SELECT DISTINCT inferred_doc_type FROM files "
        "WHERE workspace_id=%s AND inferred_doc_type IS NOT NULL "
        "  AND inferred_doc_type<>'unknown' ORDER BY 1", (ws,))
    for (dt,) in await cur.fetchall():
        print(f"\n  [{dt}]")
        cur2 = await conn.execute(
            "SELECT canonical_name, n_docs_observed, round(prevalence::numeric,2), is_promoted "
            "FROM inferred_schema_fields WHERE workspace_id=%s AND inferred_doc_type=%s "
            "ORDER BY is_promoted DESC, n_docs_observed DESC, canonical_name", (ws, dt))
        for cn, nd, prev, prom in await cur2.fetchall():
            star = "★PROMOTED" if prom else "         "
            print(f"    {star}  {cn:30s} seen={nd} prev={prev}")

    _h("CANONICAL ENTITIES (resolution — mention_count = how many merged in)")
    cur = await conn.execute(
        "SELECT canonical_name, entity_type, mention_count FROM canonical_entities "
        "WHERE workspace_id=%s AND merged_into IS NULL ORDER BY mention_count DESC, canonical_name", (ws,))
    for cn, et, mc in await cur.fetchall():
        print(f"    {cn:34s} {et:10s} mentions={mc}")

    _h("DOC CHAINS (linking)")
    cur = await conn.execute(
        "SELECT id::text, type, title, member_count FROM doc_chains WHERE workspace_id=%s", (ws,))
    chains = await cur.fetchall()
    if not chains:
        print("    (no chains)")
    for cid, ctype, title, mc in chains:
        print(f"  chain [{ctype}] {title!r}  members={mc}")
        cur2 = await conn.execute(
            "SELECT f.name, m.version_index, m.role, f.doc_status "
            "FROM doc_chain_members m JOIN files f ON f.id=m.doc_id "
            "WHERE m.chain_id=%s ORDER BY m.version_index", (cid,))
        for n, vi, role, ds in await cur2.fetchall():
            print(f"      v{vi}  {role:12s} {str(ds):12s} {n}")


async def main() -> None:
    ws = sys.argv[1]
    file_id = sys.argv[2] if len(sys.argv) > 2 else None
    async with await psycopg.AsyncConnection.connect(DB) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        if file_id:
            await per_doc(conn, file_id)
        await cross_doc(conn, ws)


if __name__ == "__main__":
    asyncio.run(main())
