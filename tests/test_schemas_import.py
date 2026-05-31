"""P3 — POST /schemas/import.yaml (inverse of GET /schemas/export.yaml).

Covers create + update upsert paths, export→import round-trip fidelity,
relationship import, and malformed-document 400s. Mirrors the fixture
conventions in test_schemas_crud.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.asyncio

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FINANCE_SCHEMA = _REPO_ROOT / "demo-corpus" / "domains" / "finance" / "schema.yaml"


def headers(workspace: str, *, content_type: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if content_type is not None:
        h["Content-Type"] = content_type
    return h


def _ws() -> str:
    return str(uuid.uuid4())


SAMPLE_DOC = """
schemas:
  - name: Loan
    description: "A finance loan agreement."
    entities:
      - name: LoanAgreement
        description: "The loan contract document."
        fields:
          - name: principal
            type: number
            description: "Sanctioned loan amount."
            required: true
          - name: interest_rate
            type: number
            description: "Annual interest rate percent."
          - name: borrower
            type: string
            description: "Borrower legal name."
      - name: Lender
        description: "The bank extending the loan."
        fields:
          - name: lender_name
            type: string
    relationships:
      - name: loan_to_lender
        from: LoanAgreement
        to: Lender
        kind: references
        cardinality: one_to_many
"""


async def _import(client, workspace, body: str):
    return await client.post(
        "/schemas/import.yaml",
        content=body,
        headers=headers(workspace, content_type="application/x-yaml"),
    )


# ---------------------------------------------------------------------------
# Create path
# ---------------------------------------------------------------------------


async def test_import_creates_schema_with_entities_and_fields(client):
    ws = _ws()
    resp = await _import(client, ws, SAMPLE_DOC)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["imported"]) == 1
    item = body["imported"][0]
    assert item["name"] == "Loan"
    assert item["action"] == "created"
    assert item["entities"] == 2
    assert item["fields"] == 4
    assert item["relationships"] == 1
    assert item["current_version"] >= 1

    # The schema is now listable + its entities/fields are queryable.
    schema_id = item["schema_id"]
    ents = await client.get(
        f"/schemas/{schema_id}/entities", headers=headers(ws)
    )
    assert ents.status_code == 200, ents.text
    names = {e["name"] for e in ents.json()["items"]}
    assert names == {"LoanAgreement", "Lender"}


# ---------------------------------------------------------------------------
# Round-trip: export then re-import into a fresh workspace
# ---------------------------------------------------------------------------


async def test_export_import_roundtrip(client):
    ws1 = _ws()
    resp = await _import(client, ws1, SAMPLE_DOC)
    assert resp.status_code == 200, resp.text

    exported = await client.get("/schemas/export.yaml", headers=headers(ws1))
    assert exported.status_code == 200, exported.text
    exported_yaml = exported.text

    # Re-import the exported YAML into a brand-new workspace.
    ws2 = _ws()
    resp2 = await _import(client, ws2, exported_yaml)
    assert resp2.status_code == 200, resp2.text
    item = resp2.json()["imported"][0]
    assert item["action"] == "created"
    assert item["entities"] == 2
    # Export omits relationships today, so the round-trip carries fields but
    # not edges — assert the field count survives the round-trip.
    assert item["fields"] == 4


# ---------------------------------------------------------------------------
# Update path — re-importing the same doc upserts in place + bumps version
# ---------------------------------------------------------------------------


async def test_reimport_updates_in_place(client):
    ws = _ws()
    first = await _import(client, ws, SAMPLE_DOC)
    v1 = first.json()["imported"][0]["current_version"]
    schema_id = first.json()["imported"][0]["schema_id"]

    second = await _import(client, ws, SAMPLE_DOC)
    item = second.json()["imported"][0]
    assert item["action"] == "updated"
    assert item["schema_id"] == schema_id  # same schema, not a duplicate
    assert item["current_version"] > v1  # a new version was recorded

    # No duplicate schema created.
    listing = await client.get("/schemas", headers=headers(ws))
    loans = [s for s in listing.json()["items"] if s["name"] == "Loan"]
    assert len(loans) == 1


async def test_reimport_removes_dropped_field(client):
    ws = _ws()
    await _import(client, ws, SAMPLE_DOC)

    # Same schema, but LoanAgreement loses interest_rate + borrower.
    trimmed = """
schemas:
  - name: Loan
    description: "A finance loan agreement."
    entities:
      - name: LoanAgreement
        fields:
          - name: principal
            type: number
"""
    resp = await _import(client, ws, trimmed)
    assert resp.status_code == 200, resp.text
    item = resp.json()["imported"][0]
    assert item["action"] == "updated"
    # Lender entity dropped, only LoanAgreement.principal remains.
    assert item["entities"] == 1
    assert item["fields"] == 1


# ---------------------------------------------------------------------------
# Validation — malformed documents return 400, not 500
# ---------------------------------------------------------------------------


async def test_import_empty_body_400(client):
    resp = await _import(client, _ws(), "")
    assert resp.status_code == 400, resp.text


async def test_import_missing_schemas_key_400(client):
    resp = await _import(client, _ws(), "foo: bar\n")
    assert resp.status_code == 400, resp.text


async def test_import_bad_field_type_400(client):
    bad = """
schemas:
  - name: Bad
    entities:
      - name: E
        fields:
          - name: f
            type: not_a_type
"""
    resp = await _import(client, _ws(), bad)
    assert resp.status_code == 400, resp.text


async def test_import_relationship_unknown_entity_400(client):
    bad = """
schemas:
  - name: Bad
    entities:
      - name: A
    relationships:
      - name: a_to_ghost
        from: A
        to: Ghost
        kind: references
"""
    resp = await _import(client, _ws(), bad)
    assert resp.status_code == 400, resp.text


async def test_import_invalid_yaml_400(client):
    resp = await _import(client, _ws(), "schemas: [unclosed\n")
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Committed demo artifact imports cleanly (guards against drift)
# ---------------------------------------------------------------------------


async def test_import_accepts_json_body(client):
    """The endpoint reads the raw body and yaml.safe_load()s it, and JSON is
    valid YAML — so the FE wizard can POST a JSON document (no YAML lib in the
    browser) to the same endpoint. Locks that contract."""
    import json

    doc = {
        "schemas": [
            {
                "name": "JsonSchema",
                "description": "built from a JS object",
                "entities": [
                    {"name": "Thing", "fields": [
                        {"name": "label", "type": "string", "is_required": True},
                    ]},
                ],
            }
        ]
    }
    resp = await client.post(
        "/schemas/import.yaml",
        content=json.dumps(doc),
        headers=headers(_ws(), content_type="application/json"),
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["imported"][0]
    assert item["name"] == "JsonSchema"
    assert item["entities"] == 1
    assert item["fields"] == 1


async def test_finance_demo_schema_imports(client):
    body = _FINANCE_SCHEMA.read_text(encoding="utf-8")
    ws = _ws()
    resp = await _import(client, ws, body)
    assert resp.status_code == 200, resp.text
    item = resp.json()["imported"][0]
    assert item["name"] == "Finance"
    assert item["action"] == "created"
    # 9 doc-type entities + Organization + Person.
    assert item["entities"] == 11
    assert item["relationships"] == 8
    assert item["fields"] > 0
