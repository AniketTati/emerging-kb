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


def normalize_unit_key(name: str) -> str:
    """FIX 4 — canonical MATCH key for sub_entity / table (unit_type) names.

    Collapses separator / spacing / case / simple-plural variants so
    `transaction_listing`, `transactionlisting`, `Transaction Listing`, and
    `Transactions`... wait — `transactionlisting` and `transaction_listing`
    are the SAME concept spelled differently and must share a key; `Transactions`
    (the plural of a different word) maps to `transaction`, distinct from
    `transactionlisting`. We don't try to reduce `transaction listing` → its
    head noun (that's semantic — left to the convergence judge).

    Strategy: strip everything non-alphanumeric + lowercase (so spacing /
    underscore / hyphen / case variants unify), then drop a simple trailing
    plural. High precision — only unifies spelling variants, not meanings.
    """
    s = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    if len(s) > 4 and s.endswith("ies"):
        return s[:-3] + "y"
    if s.endswith(("ss", "is", "us", "as")):
        return s
    if len(s) > 4 and s.endswith(("sses", "xes", "zzes", "shes", "ches")):
        return s[:-2]
    if len(s) > 3 and s.endswith("s"):
        return s[:-1]
    return s


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


async def converge_clusters_semantic(
    clusters: list[FieldCluster],
    *,
    embed_fn: Any,
    judge_fn: Any,
    sim_threshold: float = 0.86,
) -> list[FieldCluster]:
    """I2 (EDC) — merge exact-match clusters that mean the SAME field under
    different names (e.g. `total_cost` vs `total_amount`).

    Two-stage embedding-block + LLM-judge:
      1. BLOCK — embed each cluster's "name: description" and take cosine
         similarity; only pairs ≥ `sim_threshold` are merge CANDIDATES (cheap
         filter so the judge sees few pairs, not O(n²)).
      2. JUDGE — `judge_fn(a, b)` confirms each candidate pair is truly the
         same concept; confirmed pairs are union-merged.

    `embed_fn(list[str]) -> list[list[float]]` and `judge_fn(a, b) -> bool`
    are injected (real wiring uses the Gemini embedder + an LLM judge; tests
    pass fakes). Merged clusters keep the most-prevalent name as canonical and
    sum doc observations. Pure aside from the two injected calls — order of
    the returned list is by descending prevalence for determinism.
    """
    if len(clusters) < 2:
        return list(clusters)

    texts = [f"{c.canonical_name}: {c.description}".strip(": ") for c in clusters]
    vectors = await embed_fn(texts)

    def _cos(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    # Union-find over cluster indices.
    parent = list(range(len(clusters)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri, rj = _find(i), _find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    # Candidate pairs above the blocking threshold, judged for true merge.
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            if _find(i) == _find(j):
                continue
            if _cos(vectors[i], vectors[j]) < sim_threshold:
                continue
            try:
                if await judge_fn(clusters[i], clusters[j]):
                    _union(i, j)
            except Exception:  # noqa: BLE001 — judge failure → don't merge
                continue

    # Build merged groups.
    groups: dict[int, list[int]] = {}
    for idx in range(len(clusters)):
        groups.setdefault(_find(idx), []).append(idx)

    merged: list[FieldCluster] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(clusters[members[0]])
            continue
        group = [clusters[m] for m in members]
        # Canonical = the most-prevalent member's name (the dominant spelling).
        lead = max(group, key=lambda c: (c.prevalence, c.n_docs_observed))
        total_docs = sum(c.n_docs_observed for c in group)
        # Prevalence/stability: doc-weighted averages, clamped to 1.0.
        prevalence = min(1.0, sum(c.prevalence * c.n_docs_observed for c in group)
                         / total_docs) if total_docs else lead.prevalence
        stability = (sum(c.stability * c.n_docs_observed for c in group)
                     / total_docs) if total_docs else lead.stability
        merged.append(FieldCluster(
            canonical_name=lead.canonical_name,
            description=lead.description,
            value_type=lead.value_type,
            n_docs_observed=total_docs,
            prevalence=prevalence,
            stability=stability,
            value_type_confidence=stability,
        ))

    merged.sort(key=lambda c: (-c.prevalence, c.canonical_name))
    return merged


# FIX 5 — count-based promotion. The design intent is "a field that repeats
# 2–3 times across docs of a type is a real field." The old rule AND-ed an
# 80% prevalence gate, which dominates at small N: `interest_rate_all_in` in
# 3/6 loans (prevalence 0.50) was rejected though it genuinely repeats. The
# new rule promotes a type-stable field seen in ≥ promote_count docs OR at
# high prevalence (so the first doc of a type still seeds the schema), with
# stability / value_type_confidence still gating noise.
DEFAULT_PROMOTION_COUNT = 2


@dataclass
class PromotionThresholds:
    prevalence: float = 0.80
    stability: float = 0.90
    value_type_confidence: float = 0.90
    min_docs: int = DEFAULT_PROMOTION_MIN_DOCS
    promote_count: int = DEFAULT_PROMOTION_COUNT

    @classmethod
    def from_env(cls) -> "PromotionThresholds":
        return cls(
            min_docs=int(os.environ.get("KB_PROMOTION_MIN_DOCS") or DEFAULT_PROMOTION_MIN_DOCS),
            promote_count=int(
                os.environ.get("KB_PROMOTION_COUNT") or DEFAULT_PROMOTION_COUNT
            ),
        )


def should_promote(cluster: FieldCluster, thresholds: PromotionThresholds) -> bool:
    # Noise gates still apply: an absolute floor + type consistency.
    if cluster.n_docs_observed < thresholds.min_docs:
        return False
    if cluster.stability < thresholds.stability:
        return False
    if cluster.value_type_confidence < thresholds.value_type_confidence:
        return False
    # Promote when the field REPEATS enough times (count-based, the designed
    # rule) OR clears the prevalence bar (keeps first-doc schema seeding:
    # 1/1 docs = prevalence 1.0).
    return (
        cluster.n_docs_observed >= thresholds.promote_count
        or cluster.prevalence >= thresholds.prevalence
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
        # Race-safe: concurrent same-doc_type ingest may both try to create
        # the auto schema. ON CONFLICT DO NOTHING + re-SELECT; only the winner
        # creates the v1 schema_version.
        cur = await conn.execute(
            "INSERT INTO schemas (workspace_id, name, description, lifecycle_state) "
            "VALUES (%s, %s, %s, 'active') "
            "ON CONFLICT DO NOTHING "
            "RETURNING id::text",
            (
                workspace_id, schema_name,
                f"Auto-created from emergent fields for doc-type '{doc_type}'",
            ),
        )
        _new = await cur.fetchone()
        if _new is None:
            # Lost the race — another tx created it; re-SELECT and skip version
            # creation (the winner handles it). Falls through to doc_root below.
            cur = await conn.execute(
                "SELECT id::text FROM schemas "
                "WHERE workspace_id = %s AND name = %s AND lifecycle_state = 'active' "
                "LIMIT 1",
                (workspace_id, schema_name),
            )
            schema_id = (await cur.fetchone())[0]
        else:
            schema_id = _new[0]
            # Winner: back the schema with a v1 schema_versions row.
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
        # Race-safe doc_root creation (concurrent same-doc_type ingest).
        cur = await conn.execute(
            "INSERT INTO schema_entities "
            "  (schema_id, workspace_id, name, description, "
            "   lifecycle_state, kind) "
            "VALUES (%s, %s, %s, %s, 'active', 'doc_root') "
            "ON CONFLICT DO NOTHING "
            "RETURNING id::text",
            (schema_id, workspace_id, doc_root_name,
             f"Auto-created doc-root entity for '{doc_type}'"),
        )
        _r = await cur.fetchone()
        if _r:
            entity_id = _r[0]
        else:
            cur = await conn.execute(
                "SELECT id::text FROM schema_entities "
                "WHERE schema_id = %s AND name = %s AND lifecycle_state = 'active' "
                "  AND parent_type_id IS NULL "
                "LIMIT 1",
                (schema_id, doc_root_name),
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

    # Race-safe insert (mirrors ensure_sub_entity_type): concurrent ingest of
    # multiple same-doc_type files (e.g. a batch of bank_statements) all try to
    # create the same `contains` relationship → UniqueViolation on the
    # (schema_id, name) WHERE active index. ON CONFLICT DO NOTHING + re-SELECT
    # makes it idempotent instead of parking the loser at fields_extracting.
    cur = await conn.execute(
        "INSERT INTO schema_relationships "
        "  (schema_id, workspace_id, name, "
        "   from_entity_id, to_entity_id, kind, cardinality, "
        "   cascade_delete, single_parent, lifecycle_state) "
        "VALUES (%s, %s, %s, %s, %s, 'contains', 'one_to_many', "
        "        true, true, 'active') "
        "ON CONFLICT DO NOTHING "
        "RETURNING id::text",
        (schema_id, workspace_id, rel_name,
         parent_entity_id, child_entity_id),
    )
    row = await cur.fetchone()
    if row:
        return row[0]
    # Another tx won the race — re-SELECT by the same parent/child pair.
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
    # Conflict was a (schema_id, name) collision from a different parent/child;
    # return the active row holding that name so callers still get a valid id.
    cur = await conn.execute(
        "SELECT id::text FROM schema_relationships "
        "WHERE schema_id = %s AND name = %s AND lifecycle_state = 'active' "
        "LIMIT 1",
        (schema_id, rel_name),
    )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"ensure_contains_relationship: ON CONFLICT but no row found "
            f"for schema_id={schema_id} name={rel_name}"
        )
    return row[0]


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

    # FIX 4 — canonical table-name reuse. The exact-name lookup misses
    # spelling variants the LLM emits for the same table (transaction_listing
    # vs transactionlisting vs Transactions). Before creating a NEW sub_entity
    # type, scan existing active sub_entities under this parent and reuse one
    # whose normalized key matches — first spelling wins, variants collapse
    # onto it (non-destructive; no merge of already-written rows needed).
    want_key = normalize_unit_key(sub_name)
    cur = await conn.execute(
        "SELECT id::text, name FROM schema_entities "
        "WHERE schema_id = %s AND parent_type_id = %s "
        "  AND lifecycle_state = 'active' AND kind = 'sub_entity'",
        (schema_id, parent_type_id),
    )
    for existing_id, existing_name in await cur.fetchall():
        if normalize_unit_key(existing_name) == want_key:
            return existing_id

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
