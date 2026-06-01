#!/usr/bin/env python3
"""SUBSET re-extract: re-derive structured fields for the finance LOAN docs
only (the narrative docs whose doc_root typed fields are null), from cached
chunks — no re-parse, non-destructive (FIX 9/10). Proves the re-extract fixes
F-mode + aggregation without touching all 46 docs.

  source scripts/dev_env.sh && python3 scripts/reextract_loans.py
"""
from __future__ import annotations

import asyncio
import os

import psycopg

from kb.workers.tasks import (
    extract_kv_tables_file_impl,
    extract_schema_entities_file_impl,
)

WS = "f0000000-0000-0000-0000-000000000001"
URL = os.environ["KB_DATABASE_URL"]


def _loans() -> list[tuple[str, str]]:
    with psycopg.connect(URL) as c, c.cursor() as cur:
        cur.execute(
            "SELECT id::text, name FROM files "
            "WHERE workspace_id=%s AND inferred_doc_type='loan_agreement' "
            "  AND lifecycle_state='ready' ORDER BY name",
            (WS,),
        )
        return cur.fetchall()


def _show_fields(tag: str) -> None:
    with psycopg.connect(URL) as c, c.cursor() as cur:
        cur.execute(
            "SELECT f.name, ee.fields->>'interest_rate', "
            "       ee.fields->>'principal_amount' "
            "FROM extracted_entities ee JOIN files f ON f.id=ee.file_id "
            "WHERE ee.workspace_id=%s AND f.inferred_doc_type='loan_agreement' "
            "  AND ee.parent_entity_id IS NULL ORDER BY f.name",
            (WS,),
        )
        print(f"\n=== {tag}: loan doc_root [interest_rate | principal_amount] ===")
        for name, rate, principal in cur.fetchall():
            print(f"  {name:<38} rate={rate!s:<8} principal={principal!s}")


async def main() -> None:
    loans = _loans()
    _show_fields("BEFORE")
    print(f"\nre-extracting {len(loans)} loan docs (force, cached chunks)...")
    for fid, name in loans:
        try:
            await extract_kv_tables_file_impl(fid, force=True)
            await extract_schema_entities_file_impl(fid, force=True)
            print(f"  [ok] {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERR] {name}: {exc}")
    _show_fields("AFTER")


if __name__ == "__main__":
    asyncio.run(main())
