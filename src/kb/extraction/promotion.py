"""Phase 5b — cross-doc field clustering + auto-promotion to typed schema.

Per build_tracker §5.12.2 decisions #5/#6/#7/#9.

Two responsibilities:

1. `cluster_fields(proposed_fields_per_doc)` — group similar field names into
   canonical clusters within a (workspace, doc_type). Wave A simplification:
   normalize-by-snake_case + union by exact match. Phase 6 will add
   embedding-based blocking + LLM-judge for borderlines.

2. `check_and_promote(...)` — for each clustered field, compute prevalence /
   stability / value_type_confidence. If all thresholds cross, INSERT a
   typed schema_fields row (auto_promoted=true) and link it from the
   inferred_schema_fields row.

Thresholds (decision #6):
  - prevalence ≥ 0.80 (fraction of docs of this type that have the field)
  - stability ≥ 0.90 (fraction of times the value_type is consistent)
  - value_type_confidence ≥ 0.90 (same as stability for Wave A — same metric;
    Phase 6 can split into separate signals)
  - n_docs_observed ≥ KB_PROMOTION_MIN_DOCS (default 5 for demo)
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from kb.db.pool import Connection


# Default n=1 so a single-doc demo corpus actually exercises the L4
# closed-world path. The other three thresholds (prevalence, stability,
# value_type_confidence) still gate noisy fields. Production deployments
# with 100s of docs per type should raise this via KB_PROMOTION_MIN_DOCS
# (e.g. =20) so one-off fields don't pollute the schema.
DEFAULT_PROMOTION_MIN_DOCS = 1


def _normalize_field_name(raw: str) -> str:
    """Lowercase + snake_case + collapse whitespace. Cluster key for Wave A."""
    s = raw.strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:200] or "unknown"


@dataclass
class FieldCluster:
    canonical_name: str
    description: str          # most common across docs
    value_type: str           # majority vote across docs
    n_docs_observed: int
    prevalence: float         # n_docs_observed / total_docs_of_type
    stability: float          # frequency of the modal value_type
    value_type_confidence: float


def cluster_fields_for_doctype(
    *,
    proposed_per_doc: dict[str, list[dict]],
    total_docs_of_type: int,
) -> list[FieldCluster]:
    """Cluster proposed_fields across all docs of one doc_type.

    Input: `proposed_per_doc` maps file_id → list of proposed_field dicts,
    each dict has at minimum `field_name`, `field_description`, `value_type`.

    Output: one FieldCluster per canonical_name.
    """
    if total_docs_of_type <= 0:
        return []

    # canonical → list of (file_id, value_type, description)
    by_canonical: dict[str, list[tuple[str, str, str]]] = {}
    for file_id, fields in proposed_per_doc.items():
        seen_in_this_file: set[str] = set()
        for f in fields:
            canon = _normalize_field_name(f.get("field_name") or "")
            if not canon or canon in seen_in_this_file:
                continue  # dedupe within doc
            seen_in_this_file.add(canon)
            by_canonical.setdefault(canon, []).append((
                file_id,
                f.get("value_type") or "text",
                f.get("field_description") or "",
            ))

    clusters: list[FieldCluster] = []
    for canon, observations in by_canonical.items():
        n_docs = len({obs[0] for obs in observations})
        type_counts = Counter(obs[1] for obs in observations)
        modal_type, modal_count = type_counts.most_common(1)[0]
        desc_counts = Counter(obs[2] for obs in observations if obs[2])
        modal_desc = desc_counts.most_common(1)[0][0] if desc_counts else ""
        stability = modal_count / len(observations) if observations else 0.0
        prevalence = n_docs / total_docs_of_type
        clusters.append(FieldCluster(
            canonical_name=canon,
            description=modal_desc,
            value_type=modal_type,
            n_docs_observed=n_docs,
            prevalence=min(1.0, prevalence),
            stability=stability,
            value_type_confidence=stability,
        ))
    return clusters


@dataclass
class PromotionThresholds:
    prevalence: float = 0.80
    stability: float = 0.90
    value_type_confidence: float = 0.90
    min_docs: int = DEFAULT_PROMOTION_MIN_DOCS

    @classmethod
    def from_env(cls) -> "PromotionThresholds":
        return cls(
            min_docs=int(os.environ.get("KB_PROMOTION_MIN_DOCS") or DEFAULT_PROMOTION_MIN_DOCS),
        )


def should_promote(cluster: FieldCluster, thresholds: PromotionThresholds) -> bool:
    return (
        cluster.n_docs_observed >= thresholds.min_docs
        and cluster.prevalence >= thresholds.prevalence
        and cluster.stability >= thresholds.stability
        and cluster.value_type_confidence >= thresholds.value_type_confidence
    )


# ---------------------------------------------------------------------------
# DB-side helpers — auto-create schema/entity if missing; promote field
# ---------------------------------------------------------------------------


# schema_fields.type CHECK accepts only ('string','number','boolean','date','datetime')
# per 0007. Our ProposedField.value_type uses ('text','number','date','datetime',
# 'boolean','enum'). Map text/enum → string; others pass through unchanged.
_VALUE_TYPE_TO_SCHEMA_TYPE = {
    "text": "string",
    "enum": "string",
    "number": "number",
    "boolean": "boolean",
    "date": "date",
    "datetime": "datetime",
}


def map_value_type_to_schema_type(value_type: str) -> str:
    return _VALUE_TYPE_TO_SCHEMA_TYPE.get(value_type, "string")


def _snake_to_pascal(s: str) -> str:
    """`bank_statement` → `BankStatement`. Used to derive the doc_root
    entity-type name from the inferred doc_type. Empty/`unknown` →
    `Doc` so we never end up with a nameless type."""
    s = (s or "").strip()
    if not s or s.lower() == "unknown":
        return "Doc"
    parts = re.split(r"[_\s-]+", s)
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p) or "Doc"


def doc_root_name_for(doc_type: str) -> str:
    """Public alias — call this anywhere the worker / API needs to
    know the doc_root entity-type name for a given doc_type. Single
    source of truth for the convention."""
    return _snake_to_pascal(doc_type)


def sub_entity_name_for(unit_type: str) -> str:
    """`transaction` → `Transaction`. Used for sub_entity type names
    derived from L3 plugin unit_type."""
    return _snake_to_pascal(unit_type)


async def ensure_auto_schema_entity(
    conn: Connection,
    *,
    workspace_id: str,
    doc_type: str,
) -> tuple[str, str]:
    """Ensure `schemas(name='auto:<doc_type>', active)` + a doc_root
    schema_entity exist for the doc_type. Returns
    `(schema_id, doc_root_entity_id)`.

    Idempotent: returns existing IDs when the rows are already there.

    Nested-entities refactor (P1.5):
      - doc_root entity name is now the PascalCase of doc_type
        (`bank_statement` → `BankStatement`). Previously a single
        placeholder "Doc" was used for every doc-type; this caused the
        schema layer to lose all type information.
      - Backfill is handled in this same function: any pre-existing
        schema_entity named "Doc" for the schema is RENAMED to the
        doc_root name + tagged `kind='doc_root'`. This keeps existing
        rows + their schema_fields stable across the migration without
        a destructive drop. New schema_entity rows are created with the
        proper name from the start.
    """
    schema_name = f"auto:{doc_type}"
    doc_root_name = doc_root_name_for(doc_type)

    # Find existing active schema
    cur = await conn.execute(
        "SELECT id::text FROM schemas "
        "WHERE workspace_id = %s AND name = %s AND lifecycle_state = 'active' "
        "LIMIT 1",
        (workspace_id, schema_name),
    )
    row = await cur.fetchone()
    if row:
        schema_id = row[0]
    else:
        cur = await conn.execute(
            "INSERT INTO schemas (workspace_id, name, description, lifecycle_state) "
            "VALUES (%s, %s, %s, 'active') "
            "RETURNING id::text",
            (
                workspace_id, schema_name,
                f"Auto-created from emergent fields for doc-type '{doc_type}'",
            ),
        )
        schema_id = (await cur.fetchone())[0]
        # Phase 1b expects schema_versions row to back the schema.
        # Create v1 with kind='post' (the original creation flavor per 0006).
        await conn.execute(
            "INSERT INTO schema_versions (schema_id, workspace_id, version_number, body, kind) "
            "VALUES (%s, %s, 1, %s::jsonb, 'post') "
            "ON CONFLICT DO NOTHING",
            (
                schema_id, workspace_id,
                '{"name": "' + schema_name + '", "entities": [], "relationships": []}',
            ),
        )
        await conn.execute(
            "UPDATE schemas SET current_version_id = ("
            "SELECT id FROM schema_versions WHERE schema_id = %s AND version_number = 1"
            ") WHERE id = %s",
            (schema_id, schema_id),
        )

    # Find existing doc_root entity. Three cases handled in order:
    #   1. A row named with the proper PascalCase already exists (new path).
    #   2. A legacy row named "Doc" exists (created by the pre-P1.5 code) —
    #      rename it in place + tag kind='doc_root'.
    #   3. No matching row — create it fresh with kind='doc_root'.
    cur = await conn.execute(
        "SELECT id::text, name FROM schema_entities "
        "WHERE schema_id = %s AND lifecycle_state = 'active' "
        "  AND parent_type_id IS NULL "
        "  AND name IN (%s, 'Doc') "
        "ORDER BY (name = %s) DESC "  # prefer the canonical name when both exist
        "LIMIT 1",
        (schema_id, doc_root_name, doc_root_name),
    )
    row = await cur.fetchone()
    if row:
        entity_id, existing_name = row[0], row[1]
        if existing_name != doc_root_name:
            # Legacy "Doc" → rename to PascalCase doc_root.
            await conn.execute(
                "UPDATE schema_entities SET name = %s, kind = 'doc_root', "
                "  description = COALESCE(NULLIF(description, ''), %s), "
                "  updated_at = NOW() "
                "WHERE id = %s",
                (doc_root_name, f"Auto-created doc-root entity for '{doc_type}'", entity_id),
            )
        else:
            # Already correctly named; ensure kind tagged.
            await conn.execute(
                "UPDATE schema_entities SET kind = 'doc_root' "
                "WHERE id = %s AND kind <> 'doc_root'",
                (entity_id,),
            )
    else:
        cur = await conn.execute(
            "INSERT INTO schema_entities "
            "  (schema_id, workspace_id, name, description, "
            "   lifecycle_state, kind) "
            "VALUES (%s, %s, %s, %s, 'active', 'doc_root') "
            "RETURNING id::text",
            (schema_id, workspace_id, doc_root_name,
             f"Auto-created doc-root entity for '{doc_type}'"),
        )
        entity_id = (await cur.fetchone())[0]

    return schema_id, entity_id


async def ensure_contains_relationship(
    conn: Connection,
    *,
    workspace_id: str,
    schema_id: str,
    parent_entity_id: str,
    child_entity_id: str,
    name_hint: str | None = None,
) -> str:
    """Ensure the schema_relationships row that declares
    `parent contains child` (cardinality one_to_many, cascade_delete=true,
    single_parent=true). The lineage assignment in
    `kb.extraction.lineage` reads exactly this row when computing
    parent_entity_id for each extracted_entity.

    Returns the relationship id. Idempotent — matches on
    (from_entity_id, to_entity_id, kind='contains', active)
    so re-running this for the same parent/child pair is a no-op.

    `name_hint` defaults to `has_<child_name_lowercase>` — readable
    but not user-visible.
    """
    cur = await conn.execute(
        "SELECT id::text FROM schema_relationships "
        "WHERE schema_id = %s AND from_entity_id = %s AND to_entity_id = %s "
        "  AND kind = 'contains' AND lifecycle_state = 'active' "
        "LIMIT 1",
        (schema_id, parent_entity_id, child_entity_id),
    )
    row = await cur.fetchone()
    if row:
        return row[0]

    # Read child name to build the relationship's `name` field.
    cur = await conn.execute(
        "SELECT name FROM schema_entities WHERE id = %s", (child_entity_id,),
    )
    child_row = await cur.fetchone()
    child_name = child_row[0] if child_row else "child"
    rel_name = name_hint or f"has_{child_name.lower()}s"

    cur = await conn.execute(
        "INSERT INTO schema_relationships "
        "  (schema_id, workspace_id, name, "
        "   from_entity_id, to_entity_id, kind, cardinality, "
        "   cascade_delete, single_parent, lifecycle_state) "
        "VALUES (%s, %s, %s, %s, %s, 'contains', 'one_to_many', "
        "        true, true, 'active') "
        "RETURNING id::text",
        (schema_id, workspace_id, rel_name,
         parent_entity_id, child_entity_id),
    )
    return (await cur.fetchone())[0]


async def ensure_sub_entity_type(
    conn: Connection,
    *,
    workspace_id: str,
    schema_id: str,
    parent_type_id: str,
    unit_type: str,
    description: str = "",
) -> str:
    """Ensure a `sub_entity` schema_entity exists for the given
    structural `unit_type` (e.g. 'transaction', 'clause',
    'line_item') under the given doc_root parent type.

    The sub_entity name is `sub_entity_name_for(unit_type)` (PascalCase).
    Returns the sub_entity's id. Idempotent.

    Usage during extraction:
      1. ensure_auto_schema_entity → doc_root id (parent)
      2. for each unit_type observed in this doc's atomic_units:
           ensure_sub_entity_type → sub_entity id (child)
      3. for each atomic_unit row, create an extracted_entity with
         schema_entity_id = sub_entity id, parent_entity_id pointing
         at the doc's parent extracted_entity.
    """
    sub_name = sub_entity_name_for(unit_type)
    # The UNIQUE constraint (schema_entities_schema_name_active_idx) is on
    # (schema_id, name) WHERE lifecycle_state='active' — NOT on
    # parent_type_id. The previous SELECT-then-INSERT pattern checked
    # parent_type_id but the constraint doesn't, so on
    # retry-after-partial-failure (or concurrent inserts) the SELECT
    # missed but the INSERT raised UniqueViolation. The fix:
    #   1. SELECT by (schema_id, name) — same keys as the unique.
    #   2. INSERT with ON CONFLICT DO NOTHING + re-SELECT to win the
    #      race deterministically.
    cur = await conn.execute(
        "SELECT id::text FROM schema_entities "
        "WHERE schema_id = %s AND name = %s "
        "  AND lifecycle_state = 'active' "
        "LIMIT 1",
        (schema_id, sub_name),
    )
    row = await cur.fetchone()
    if row:
        return row[0]

    cur = await conn.execute(
        "INSERT INTO schema_entities "
        "  (schema_id, workspace_id, name, description, "
        "   lifecycle_state, kind, parent_type_id) "
        "VALUES (%s, %s, %s, %s, 'active', 'sub_entity', %s) "
        "ON CONFLICT DO NOTHING "
        "RETURNING id::text",
        (
            schema_id, workspace_id, sub_name,
            description or f"Auto-created sub-entity type from unit_type='{unit_type}'",
            parent_type_id,
        ),
    )
    row = await cur.fetchone()
    if row:
        return row[0]
    # ON CONFLICT path — another tx won the race; re-SELECT.
    cur = await conn.execute(
        "SELECT id::text FROM schema_entities "
        "WHERE schema_id = %s AND name = %s "
        "  AND lifecycle_state = 'active' "
        "LIMIT 1",
        (schema_id, sub_name),
    )
    row = await cur.fetchone()
    if row is None:
        # Shouldn't happen — UNIQUE held but row vanished. Surface
        # the error rather than silently returning something wrong.
        raise RuntimeError(
            f"ensure_sub_entity_type: ON CONFLICT but no row found "
            f"for schema_id={schema_id} name={sub_name}"
        )
    return row[0]


async def promote_field(
    conn: Connection,
    *,
    workspace_id: str,
    schema_entity_id: str,
    canonical_name: str,
    description: str,
    value_type: str,
) -> str:
    """INSERT a `schema_fields` row with `auto_promoted=true`. Returns the
    new schema_fields.id. Idempotent on `(entity_id, name)` UNIQUE — returns
    existing id if duplicate.

    `value_type` is the ProposedField value_type ('text'/'enum'/'number'/etc.)
    — mapped to schema_fields.type ('string'/'number'/...) per the 0007 CHECK.
    """
    # Check existing active row first
    cur = await conn.execute(
        "SELECT id::text FROM schema_fields "
        "WHERE entity_id = %s AND name = %s AND lifecycle_state = 'active' "
        "LIMIT 1",
        (schema_entity_id, canonical_name),
    )
    row = await cur.fetchone()
    if row:
        return row[0]

    schema_type = map_value_type_to_schema_type(value_type)
    cur = await conn.execute(
        "INSERT INTO schema_fields "
        "(entity_id, workspace_id, name, type, nl_description, lifecycle_state, auto_promoted) "
        "VALUES (%s, %s, %s, %s, %s, 'active', true) "
        "RETURNING id::text",
        (schema_entity_id, workspace_id, canonical_name, schema_type, description),
    )
    return (await cur.fetchone())[0]
