"""FIX 5 (declared half) — user-declared fields are extracted BY DECLARATION.

The emergent path promotes a field only after it repeats (count-based, FIX 5).
A USER-declared field needs no count: it's a schema_field the moment the user
declares it, and the schema-driven extractor must pick it up by its declared
name. This locks the contract that the extraction readers surface declared
fields (auto_promoted=false) with NO promotion filter — so a future regression
that filters to auto_promoted=true would fail here.
"""

from __future__ import annotations

import uuid

import pytest

from kb.db.pool import open_connection
from kb.domain.extracted_entities import (
    read_active_schemas_for_doctype,
    read_schema_entities_with_fields,
)
from kb.extraction.promotion import ensure_auto_schema_entity


pytestmark = pytest.mark.asyncio


async def test_declared_field_drives_extraction_without_promotion(db_url_superuser):
    workspace = str(uuid.uuid4())
    doc_type = "loan_agreement"
    async with open_connection(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
            )
            schema_id, doc_root_id = await ensure_auto_schema_entity(
                conn, workspace_id=workspace, doc_type=doc_type,
            )
            # USER declares a field (auto_promoted defaults to false) — the
            # only thing the user did; no docs have promoted it.
            await conn.execute(
                "INSERT INTO schema_fields "
                "(entity_id, workspace_id, name, type, nl_description, "
                " lifecycle_state, auto_promoted) "
                "VALUES (%s, %s, 'interest_rate', 'number', "
                "        'All-in interest rate', 'active', false)",
                (doc_root_id, workspace),
            )

    async with open_connection(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        # The doc-type's active schemas include the auto schema...
        schemas = await read_active_schemas_for_doctype(
            conn, workspace_id=workspace, inferred_doc_type=doc_type,
        )
        assert any(sid == schema_id for sid, _ in schemas)

        # ...and the declared field is surfaced in field_defs (no auto_promoted
        # filter) → it drives the schema-driven LLM extraction by its name.
        entities = await read_schema_entities_with_fields(conn, schema_id=schema_id)
        field_names = {
            f["name"] for e in entities for f in e["field_defs"]
        }
        assert "interest_rate" in field_names
