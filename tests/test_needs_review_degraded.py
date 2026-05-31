"""FIX 3 — degraded extractions surface in GET /knowledge-map/needs-review.

A text-rich doc that produced no body fields is flagged extraction_degraded
at KV+Tables write time so it can't hide behind a healthy-looking `ready`
status. This test seeds the files rows directly (the flag is the contract the
needs-review view reads) rather than running the whole pipeline.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


def _headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def _seed_file(
    db_url: str, workspace_id: str, *, label: str, degraded: bool,
    coverage: dict,
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"nr-{workspace_id}-{label}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace_id,),
        )
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, "
            "object_key, mime_type, size_bytes, lifecycle_state, "
            "inferred_doc_type, extraction_coverage, extraction_degraded) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready', "
            "%s, %s::jsonb, %s)",
            (file_id, workspace_id, f"{label}.pdf", sha, f"raw_files/{sha}",
             "loan_agreement", json.dumps(coverage), degraded),
        )
        await conn.commit()
    return file_id


async def test_degraded_doc_surfaces_in_needs_review(client, db_url_superuser):
    workspace = str(uuid.uuid4())
    degraded_cov = {
        "body_fields": 0, "frontmatter_fields": 9, "table_rows": 0,
        "text_rich": True, "model_id": "identity", "degraded": True,
    }
    healthy_cov = {
        "body_fields": 12, "frontmatter_fields": 3, "table_rows": 40,
        "text_rich": True, "model_id": "gemini", "degraded": False,
    }
    degraded_id = await _seed_file(
        db_url_superuser, workspace, label="bad-loan",
        degraded=True, coverage=degraded_cov,
    )
    await _seed_file(
        db_url_superuser, workspace, label="good-statement",
        degraded=False, coverage=healthy_cov,
    )

    resp = await client.get(
        "/knowledge-map/needs-review", headers=_headers(workspace),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["degraded_extractions_total"] == 1
    surfaced = body["degraded_extractions"]
    assert len(surfaced) == 1
    doc = surfaced[0]
    assert doc["file_id"] == degraded_id
    assert doc["doc_type"] == "loan_agreement"
    assert doc["coverage"]["body_fields"] == 0
    assert doc["coverage"]["text_rich"] is True


async def test_no_degraded_docs_is_clean(client, db_url_superuser):
    workspace = str(uuid.uuid4())
    await _seed_file(
        db_url_superuser, workspace, label="ok-doc",
        degraded=False,
        coverage={"body_fields": 5, "table_rows": 0, "text_rich": True,
                  "degraded": False},
    )
    resp = await client.get(
        "/knowledge-map/needs-review", headers=_headers(workspace),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded_extractions_total"] == 0
    assert body["degraded_extractions"] == []
