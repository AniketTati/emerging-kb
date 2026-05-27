"""One-shot backfill: parse YAML frontmatter from raw_pages.text for
every markdown file in scope, ensure those fields land in
proposed_fields. Mirrors what extract_kv_tables_file_impl now does
inline going forward.

After running this, re-trigger chain detection + doc_status backfill
to pick up the newly-populated chain_id / parent_doc / status fields.

Usage:
    uv run python scripts/backfill_frontmatter.py \
        --workspace c0000000-0000-0000-0000-000000000001 \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.config import get_settings  # noqa: E402
from kb.db.pool import open_connection  # noqa: E402
from kb.domain.fields import insert_proposed_field  # noqa: E402
from kb.workers.tasks import _parse_yaml_frontmatter  # noqa: E402


async def main(workspace_id: str, dry_run: bool) -> int:
    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            cur = await conn.execute(
                "SELECT f.id::text, f.name, f.inferred_doc_type, rp.text "
                "FROM files f "
                "LEFT JOIN raw_pages rp ON rp.file_id = f.id AND rp.page_number = 1 "
                "WHERE f.workspace_id = %s "
                "AND f.lifecycle_state NOT IN ('queued','parsing','failed','deleted') "
                "ORDER BY f.created_at ASC",
                (workspace_id,),
            )
            files = await cur.fetchall()

        applied = 0
        skipped_no_fm = 0
        already_present = 0
        for fid, name, doc_type, first_page_text in files:
            fm = _parse_yaml_frontmatter(first_page_text or "")
            if not fm:
                skipped_no_fm += 1
                continue

            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.workspace_id', %s, true)",
                    (workspace_id,),
                )
                # First chunk for source_chunk_id attribution.
                cur = await conn.execute(
                    "SELECT id::text FROM chunks WHERE file_id = %s "
                    "ORDER BY chunk_index LIMIT 1",
                    (fid,),
                )
                row = await cur.fetchone()
                fm_chunk_id = row[0] if row else None

                # Existing field names for this file.
                cur = await conn.execute(
                    "SELECT lower(field_name) FROM proposed_fields WHERE file_id = %s",
                    (fid,),
                )
                existing = {r[0] for r in await cur.fetchall()}

                added_here = 0
                overwrote_here = 0
                for key, value in fm.items():
                    val_str = str(value or "").strip()
                    if not val_str:
                        continue
                    if key.lower() in existing:
                        # Frontmatter wins — overwrite the LLM value.
                        if not dry_run:
                            await conn.execute(
                                "DELETE FROM proposed_fields "
                                "WHERE file_id = %s AND lower(field_name) = %s",
                                (fid, key.lower()),
                            )
                        overwrote_here += 1
                    # Decide value_type.
                    klc = key.lower()
                    if klc in ("status", "doc_status") and val_str.lower() in (
                        "live", "superseded", "draft", "archived", "retracted",
                    ):
                        v_type = "enum"
                        val_str = val_str.lower()
                    elif klc.endswith("date"):
                        v_type = "date"
                    else:
                        v_type = "text"
                    if not dry_run:
                        await insert_proposed_field(
                            conn,
                            file_id=fid,
                            workspace_id=workspace_id,
                            inferred_doc_type=doc_type,
                            field_name=key,
                            field_description=f"Extracted from YAML frontmatter ({key})",
                            value_text=val_str,
                            value_type=v_type,
                            is_pii=False,
                            model_id="frontmatter:auto",
                            source_chunk_id=fm_chunk_id,
                            source_char_start=None,
                            source_char_end=None,
                        )
                    added_here += 1
            applied += added_here
            if added_here == 0:
                already_present += 1
            print(f"  {fid[:8]} {name:55s} +{added_here} new, {overwrote_here} overwrote")

        print()
        print(f"# files scanned: {len(files)}")
        print(f"  no frontmatter: {skipped_no_fm}")
        print(f"  total fields {'WOULD be' if dry_run else ''} written: {applied}")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace, args.dry_run)))
