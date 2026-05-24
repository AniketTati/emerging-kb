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


DEFAULT_PROMOTION_MIN_DOCS = 5


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


async def ensure_auto_schema_entity(
    conn: Connection,
    *,
    workspace_id: str,
    doc_type: str,
) -> tuple[str, str]:
    """Ensure `schemas(name='auto:<doc_type>', active)` + matching schema_entity
    exist for the doc_type. Returns (schema_id, schema_entity_id).

    Idempotent: if already exists, returns the existing IDs.
    """
    schema_name = f"auto:{doc_type}"

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

    # Find or create one schema_entity per doc_type (single "Doc" entity).
    entity_name = "Doc"
    cur = await conn.execute(
        "SELECT id::text FROM schema_entities "
        "WHERE schema_id = %s AND name = %s AND lifecycle_state = 'active' "
        "LIMIT 1",
        (schema_id, entity_name),
    )
    row = await cur.fetchone()
    if row:
        entity_id = row[0]
    else:
        cur = await conn.execute(
            "INSERT INTO schema_entities (schema_id, workspace_id, name, description, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, 'active') "
            "RETURNING id::text",
            (schema_id, workspace_id, entity_name, f"Auto-created entity for '{doc_type}'"),
        )
        entity_id = (await cur.fetchone())[0]

    return schema_id, entity_id


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
