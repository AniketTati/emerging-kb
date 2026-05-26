"""Wave A close-up — /explore: faceted search across the workspace.
Plus /explore/entity/{id}/profile for the Pass B entity-card rollup.

The Explore page is the third primary-nav surface (alongside Chat +
Upload). It answers "what's in my corpus?" by category:

  - documents      (files where lifecycle_state = 'ready')
  - doc_types      (distinct inferred_doc_type values)
  - atomic_units   (clauses / transactions / rows / decisions)
  - entities       (resolved canonical entities)
  - relationships  (typed edges from the L5 graph)
  - topics         (raptor_nodes — corpus-level themes)
  - anomalies      (atomic_units with rarity_score above threshold)

Two endpoints:

  GET /explore/counts
        One round-trip, returns the per-category total.
        Used by the left rail to populate the count badges
        without firing 7 separate queries from the UI.

  GET /explore/search?q=...&kind=...&offset=0&limit=50
        Faceted text search. Cursor-style pagination via offset+limit
        — workable to ~10k results; beyond that the UI should switch
        to per-kind drill-down (a separate endpoint per kind, already
        present at /files, /entities, /atomic-units, etc.).

Scale posture: the SQL uses indexed prefix/ILIKE matches on
  - files.name
  - entities.canonical_name
  - atomic_units.unit_text (subset)
At 100k+ workspaces add pg_trgm GIN indexes to those columns and
swap ILIKE for `% q %`. The /explore/search query layout doesn't
need to change.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection


router = APIRouter(tags=["explore"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ExploreCounts(BaseModel):
    documents: int = 0
    doc_types: int = 0
    atomic_units: int = 0
    entities: int = 0
    relationships: int = 0
    topics: int = 0
    anomalies: int = 0


class ExploreHit(BaseModel):
    kind: str            # "document" | "doc_type" | "atomic_unit" | "entity" | "relationship" | "topic" | "anomaly"
    id: str              # primary key (or canonical handle, e.g. doc_type name)
    title: str           # display label
    subtitle: str | None = None
    snippet: str | None = None
    file_id: str | None = None
    file_name: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ExploreSearchResponse(BaseModel):
    q: str
    kind: str | None = None
    offset: int = 0
    limit: int = 50
    total_estimate: int = 0
    items: list[ExploreHit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Filter bag — keeps the search helper signatures compact when we keep
# adding optional facets. has_conflicts / has_chain are post-extraction
# joins (fact_conflicts + doc_chain_members); has_anomaly is a per-file
# EXISTS check against atomic_units.
# ---------------------------------------------------------------------------


from dataclasses import dataclass


_VALID_SORTS = {"relevance", "name", "recent"}


@dataclass(frozen=True)
class _SearchFilters:
    doc_type: str | None = None
    doc_types: tuple[str, ...] = ()
    date_from: str | None = None         # ISO 'YYYY-MM-DD'
    date_to: str | None = None
    has_anomaly: bool = False
    has_conflicts: bool = False
    has_chain: bool = False
    # `relevance` is the per-kind default ORDER BY (created_at DESC for
    # docs, mention_count DESC for entities, n DESC for doc_types).
    # `name` and `recent` give the user explicit overrides — see
    # _ORDER_BY_DOCS / _ORDER_BY_ENTITIES below.
    sort: str = "relevance"

    def effective_doc_types(self) -> tuple[str, ...]:
        """`doc_types` (multi) takes precedence over `doc_type` (single)
        for back-compat. Callers should branch on len(): 0 = no filter,
        1 = single, >1 = ANY-of."""
        if self.doc_types:
            return self.doc_types
        if self.doc_type:
            return (self.doc_type,)
        return ()

    def applies_to_files(self) -> bool:
        return bool(
            self.effective_doc_types() or self.has_anomaly
            or self.has_conflicts or self.has_chain
            or self.date_from or self.date_to
        )


# ---------------------------------------------------------------------------
# Entity-profile response models (Pass B — Related accordion)
# ---------------------------------------------------------------------------


class EntityProfileBucket(BaseModel):
    """One row of the entity card's `RELATED` accordion. Maps to the
    prototype lines like:
      `17 Contracts — supply, services, employment`
      `34 Invoices — total ₹4.7 Cr · 31 paid, 3 pending`
      `11 Connected people — counterparties, signatories, site engineers`
    """
    key: str               # stable id for the UI ("contracts" / "projects" / …)
    label: str             # display title ("17 Contracts")
    icon: str              # lucide icon name ("file-text" / "users" / …)
    count: int
    subtitle: str          # human-readable subtitle (sample titles joined)
    # The scope-filter pair that `view all →` should apply on /explore
    # to drill into this bucket (kind + optional inferred_doc_type / etc.).
    deep_link_kind: str | None = None
    deep_link_doc_type: str | None = None
    deep_link_q: str | None = None


class EntityProfileResponse(BaseModel):
    id: str
    canonical_name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    n_docs: int = 0
    mention_count: int = 0
    summary: str = ""      # narrative blurb (template-generated)
    related: list[EntityProfileBucket] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /explore/counts — left-rail badges in one shot
# ---------------------------------------------------------------------------


@router.get(
    "/explore/counts",
    response_model=ExploreCounts,
    summary="Per-category counts for the Explore left rail",
)
async def get_explore_counts(
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ExploreCounts:
    """One round-trip. Each count is a single indexed COUNT(*) under
    workspace RLS, so even at 100k docs this stays under ~50ms.

    Anomaly threshold matches the channel: rarity_score > 0.7.
    """
    out = ExploreCounts()
    # Documents — exclude soft-deleted + failed.
    cur = await conn.execute(
        "SELECT count(*)::int FROM files "
        "WHERE lifecycle_state NOT IN ('deleted','failed')"
    )
    out.documents = int((await cur.fetchone())[0])

    cur = await conn.execute(
        "SELECT count(DISTINCT inferred_doc_type)::int FROM files "
        "WHERE lifecycle_state NOT IN ('deleted','failed') "
        "  AND inferred_doc_type IS NOT NULL"
    )
    out.doc_types = int((await cur.fetchone())[0])

    # Sub-entity rows (transactions / clauses / line_items / …) live
    # in extracted_entities with `unit_type IS NOT NULL` after the
    # nested-entities refactor. Count surface name stays `atomic_units`
    # so the UI counter stays stable across the migration.
    cur = await conn.execute(
        "SELECT count(*)::int FROM extracted_entities "
        "WHERE unit_type IS NOT NULL"
    )
    out.atomic_units = int((await cur.fetchone())[0])

    cur = await conn.execute("SELECT count(*)::int FROM entities")
    out.entities = int((await cur.fetchone())[0])

    cur = await conn.execute("SELECT count(*)::int FROM relationships")
    out.relationships = int((await cur.fetchone())[0])

    # Topics = corpus-scope raptor nodes (the workspace summary + cluster
    # summaries). Per-doc RAPTOR nodes aren't "topics" in the user
    # sense — they're per-doc abstractions. We filter to scope='corpus'.
    cur = await conn.execute(
        "SELECT count(*)::int FROM raptor_nodes WHERE scope = 'corpus'"
    )
    out.topics = int((await cur.fetchone())[0])

    cur = await conn.execute(
        "SELECT count(*)::int FROM extracted_entities "
        "WHERE unit_type IS NOT NULL "
        "  AND rarity_score IS NOT NULL AND rarity_score > 0.7"
    )
    out.anomalies = int((await cur.fetchone())[0])

    return out


# ---------------------------------------------------------------------------
# /explore/search — faceted ILIKE search
# ---------------------------------------------------------------------------


_VALID_KINDS = {
    "document", "doc_type", "atomic_unit", "entity", "relationship",
    "topic", "anomaly",
}

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get(
    "/explore/search",
    response_model=ExploreSearchResponse,
    summary="Faceted explore search across docs / entities / atomic units / topics",
)
async def get_explore_search(
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
    q: str = Query(default="", description="Free-text query. Empty = browse all."),
    kind: str | None = Query(
        default=None,
        description=(
            "Restrict to a single category: document, doc_type, "
            "atomic_unit, entity, relationship, topic, anomaly."
        ),
    ),
    doc_type: str | None = Query(
        default=None,
        description=(
            "Filter document/atomic_unit/anomaly buckets to one doc_type. "
            "DEPRECATED — use `doc_types=X,Y,Z` for multi-select; this "
            "single-value form is kept for back-compat."
        ),
    ),
    doc_types: str | None = Query(
        default=None,
        description=(
            "Comma-separated list of inferred_doc_type values. When set "
            "(takes precedence over `doc_type`), only files whose "
            "inferred_doc_type is in the list are returned."
        ),
    ),
    date_from: str | None = Query(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD). Restrict file/atomic_unit/anomaly "
            "results to rows whose `files.created_at >= date_from`."
        ),
    ),
    date_to: str | None = Query(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD). Restrict to `files.created_at <= "
            "date_to`. Inclusive on both ends."
        ),
    ),
    has_anomaly: bool = Query(
        default=False,
        description=(
            "When true, only return documents/entities whose files contain "
            "at least one atomic_unit with rarity_score > 0.7."
        ),
    ),
    has_conflicts: bool = Query(
        default=False,
        description=(
            "When true, only return documents whose entity_id or chain_id "
            "appears in `fact_conflicts`."
        ),
    ),
    has_chain: bool = Query(
        default=False,
        description=(
            "When true, only return documents that are part of a doc_chain "
            "(amendment / email thread / drawing revision)."
        ),
    ),
    sort: str = Query(
        default="relevance",
        description=(
            "Sort order: 'relevance' (per-kind default), 'name' (A→Z by "
            "title/canonical_name), or 'recent' (most-recently created/"
            "seen first). Unknown values fall back to relevance."
        ),
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> ExploreSearchResponse:
    """Returns up to `limit` matches across all categories (or just one
    if `kind` is set). Empty `q` returns recent/all rows per category.

    Per-category SQL keeps the implementation linear: each category is
    one indexed SELECT, results merged at the application layer. This
    is cheap (~50-200ms for a 100k workspace) and avoids the
    UNION-with-mismatched-columns rabbit hole.
    """
    q_norm = (q or "").strip()
    like = f"%{q_norm}%"
    has_query = bool(q_norm)

    # If kind is set, restrict to just that bucket. Else fan out.
    if kind and kind not in _VALID_KINDS:
        kind = None

    targets = [kind] if kind else list(_VALID_KINDS)
    # Parse comma-separated doc_types. Skip empties / strip whitespace.
    parsed_doc_types: tuple[str, ...] = tuple(
        t.strip() for t in (doc_types or "").split(",") if t.strip()
    )
    sort_norm = sort if sort in _VALID_SORTS else "relevance"
    filters = _SearchFilters(
        doc_type=doc_type,
        doc_types=parsed_doc_types,
        date_from=date_from,
        date_to=date_to,
        has_anomaly=has_anomaly,
        has_conflicts=has_conflicts,
        has_chain=has_chain,
        sort=sort_norm,
    )

    items: list[ExploreHit] = []
    total_estimate = 0

    for tgt in targets:
        if tgt == "document":
            items_part, n = await _search_documents(conn, like, has_query, offset, limit, filters)
        elif tgt == "doc_type":
            items_part, n = await _search_doc_types(conn, like, has_query, offset, limit)
        elif tgt == "atomic_unit":
            items_part, n = await _search_atomic_units(conn, like, has_query, offset, limit, filters)
        elif tgt == "entity":
            items_part, n = await _search_entities(conn, like, has_query, offset, limit, filters)
        elif tgt == "relationship":
            items_part, n = await _search_relationships(conn, like, has_query, offset, limit)
        elif tgt == "topic":
            items_part, n = await _search_topics(conn, like, has_query, offset, limit)
        elif tgt == "anomaly":
            items_part, n = await _search_anomalies(conn, like, has_query, offset, limit, filters)
        else:
            items_part, n = [], 0
        items.extend(items_part)
        total_estimate += n

    # If we fanned out across many kinds, soft-cap the final response
    # at `limit` so the UI doesn't get a wall of mixed results.
    if not kind and len(items) > limit:
        items = items[:limit]

    return ExploreSearchResponse(
        q=q_norm, kind=kind, offset=offset, limit=limit,
        total_estimate=total_estimate, items=items,
    )


# ---------------------------------------------------------------------------
# Per-kind SQL helpers (each returns (items, total_count_estimate))
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /explore/entity/{id}/profile — Pass B rich entity card
# ---------------------------------------------------------------------------


# How doc_types group into the prototype's bucket labels. Anything not
# in this map falls into the "Other documents" bucket.
_DOCTYPE_GROUPS: dict[str, tuple[str, str]] = {
    # (group_key, group_label)
    "legal_contract":            ("contracts", "Contracts"),
    "master_services_agreement": ("contracts", "Contracts"),
    "subscription_agreement":    ("contracts", "Contracts"),
    "vendor_evaluation":         ("contracts", "Contracts"),
    "offer_letter":              ("contracts", "Contracts"),

    "invoice":                   ("invoices", "Invoices"),
    "bank_statement":            ("invoices", "Invoices"),
    "expense_report":            ("invoices", "Invoices"),
    "explanation_of_benefits":   ("invoices", "Invoices"),
    "price_sheet":               ("invoices", "Invoices"),

    "email_thread":              ("emails", "Email threads"),

    "incident_report":           ("reports", "Reports"),
    "incident_postmortem":       ("reports", "Reports"),
    "bug_report":                ("reports", "Reports"),
    "lab_report":                ("reports", "Reports"),
    "discharge_summary":         ("reports", "Reports"),
    "financial_report":          ("reports", "Reports"),
    "case_study":                ("reports", "Reports"),
    "performance_review":        ("reports", "Reports"),
    "meeting_minutes":           ("reports", "Reports"),
    "press_release":             ("reports", "Reports"),

    "resume":                    ("people", "People profiles"),
    "job_posting":               ("people", "People profiles"),

    "request_for_comments":      ("design", "Design docs"),
    "handwritten_note":          ("notes", "Notes"),
}

_GROUP_ICONS: dict[str, str] = {
    "contracts": "file-text",
    "invoices":  "dollar-sign",
    "emails":    "mail",
    "reports":   "file-text",
    "people":    "user",
    "design":    "blueprint",
    "notes":     "sticky-note",
    "other":     "file",
}


@router.get(
    "/explore/entity/{entity_id}/profile",
    response_model=EntityProfileResponse,
    summary=(
        "Rich entity card with Related accordion (Pass B for "
        "prototype/explore.html parity)"
    ),
)
async def get_entity_profile(
    entity_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> EntityProfileResponse:
    """Builds the entity card rollup the prototype shows:

      RELATED
        17 Contracts — supply, services, employment      view all →
        6 Projects — Aurangabad warehouse, Pune…         view all →
        34 Invoices — total ₹4.7 Cr · 31 paid, 3 pending view all →
        3 Employees — Priya Sharma, Rohan Patel…         view all →
        11 Connected people — counterparties, signat…   view all →
        1 Anomaly — 4-hour delivery clause on…           view →

    Implementation: 4 indexed sub-queries against
      entities + extracted_mentions + mention_to_entity + files + atomic_units
    one per bucket family (file-grouped / co-mentioned PERSON / co-mentioned
    ORG / anomalies). At demo scale (~hundreds of mentions per entity)
    this stays under 100ms; at 100k entities × 1M mentions we'd cache
    the result onto `entity_summary` and refresh on extraction.
    """
    # ---- Base entity row ----
    cur = await conn.execute(
        "SELECT id::text, canonical_name, entity_type, mention_count "
        "  FROM entities WHERE id = %s",
        (entity_id,),
    )
    row = await cur.fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="entity not found")
    _, canonical_name, entity_type, mention_count = row

    # ---- Aliases (top distinct surface forms != canonical) ----
    cur = await conn.execute(
        """
        SELECT DISTINCT em.mention_text
          FROM extracted_mentions em
          JOIN mention_to_entity me ON me.mention_id = em.id
         WHERE me.entity_id = %s
           AND lower(em.mention_text) <> lower(%s)
         LIMIT 4
        """,
        (entity_id, canonical_name),
    )
    aliases = [r[0] for r in await cur.fetchall() if r[0]][:3]

    # ---- First / last seen + n_docs ----
    cur = await conn.execute(
        """
        SELECT MIN(f.created_at)::date::text,
               MAX(f.created_at)::date::text,
               count(DISTINCT em.file_id)::int
          FROM extracted_mentions em
          JOIN mention_to_entity me ON me.mention_id = em.id
          JOIN files f ON f.id = em.file_id
         WHERE me.entity_id = %s
           AND f.lifecycle_state NOT IN ('deleted','failed')
        """,
        (entity_id,),
    )
    fs_row = await cur.fetchone()
    first_seen = fs_row[0] if fs_row else None
    last_seen = fs_row[1] if fs_row else None
    n_docs = int(fs_row[2] or 0) if fs_row else 0

    # ---- File buckets: group inferred_doc_type → bucket label, count + sample ----
    cur = await conn.execute(
        """
        SELECT f.inferred_doc_type, count(DISTINCT f.id)::int AS n_files,
               (array_agg(DISTINCT f.name))[1:5] AS sample_names
          FROM extracted_mentions em
          JOIN mention_to_entity me ON me.mention_id = em.id
          JOIN files f ON f.id = em.file_id
         WHERE me.entity_id = %s
           AND f.lifecycle_state NOT IN ('deleted','failed')
           AND f.inferred_doc_type IS NOT NULL
         GROUP BY f.inferred_doc_type
         ORDER BY n_files DESC
        """,
        (entity_id,),
    )
    file_rows = await cur.fetchall()

    # Roll up by group_key
    group_acc: dict[str, dict[str, Any]] = {}
    for dt, n, samples in file_rows:
        gkey, glabel = _DOCTYPE_GROUPS.get(dt, ("other", "Other documents"))
        slot = group_acc.setdefault(gkey, {
            "label": glabel, "count": 0, "doc_types": [], "samples": [],
        })
        slot["count"] += int(n)
        slot["doc_types"].append(dt)
        slot["samples"].extend(samples or [])

    related: list[EntityProfileBucket] = []
    for gkey, slot in sorted(
        group_acc.items(), key=lambda kv: kv[1]["count"], reverse=True,
    ):
        subtitle_items = []
        # Show the doc_types as the subtitle, capped at 3 ("supply, services, employment" style).
        for dt in slot["doc_types"][:3]:
            subtitle_items.append(dt.replace("_", " "))
        if len(slot["doc_types"]) > 3:
            subtitle_items.append(f"+{len(slot['doc_types']) - 3} more")
        related.append(EntityProfileBucket(
            key=gkey,
            label=f"{slot['count']} {slot['label']}",
            icon=_GROUP_ICONS.get(gkey, "file"),
            count=slot["count"],
            subtitle=", ".join(subtitle_items),
            deep_link_kind="document",
            deep_link_doc_type=slot["doc_types"][0] if slot["doc_types"] else None,
        ))

    # ---- Connected people (PERSON entities co-mentioned in same files) ----
    cur = await conn.execute(
        """
        SELECT e2.id::text, e2.canonical_name,
               count(DISTINCT em2.file_id)::int AS shared_files
          FROM extracted_mentions em1
          JOIN mention_to_entity me1 ON me1.mention_id = em1.id
          JOIN extracted_mentions em2 ON em2.file_id = em1.file_id
                                     AND em2.id <> em1.id
          JOIN mention_to_entity me2 ON me2.mention_id = em2.id
          JOIN entities e2 ON e2.id = me2.entity_id
         WHERE me1.entity_id = %s
           AND e2.entity_type = 'PERSON'
           AND e2.id <> %s
         GROUP BY e2.id, e2.canonical_name
         ORDER BY shared_files DESC, e2.canonical_name
         LIMIT 25
        """,
        (entity_id, entity_id),
    )
    people_rows = await cur.fetchall()
    if people_rows:
        sample_names = [r[1] for r in people_rows[:3]]
        more = max(0, len(people_rows) - 3)
        subtitle = ", ".join(sample_names) + (f", +{more} more" if more else "")
        related.append(EntityProfileBucket(
            key="connected_people",
            label=f"{len(people_rows)} Connected people",
            icon="users",
            count=len(people_rows),
            subtitle=subtitle,
            deep_link_kind="entity",
            deep_link_q=canonical_name,  # the explore search will surface people
        ))

    # ---- Connected orgs (other ORG entities co-mentioned) ----
    cur = await conn.execute(
        """
        SELECT e2.id::text, e2.canonical_name,
               count(DISTINCT em2.file_id)::int AS shared_files
          FROM extracted_mentions em1
          JOIN mention_to_entity me1 ON me1.mention_id = em1.id
          JOIN extracted_mentions em2 ON em2.file_id = em1.file_id
                                     AND em2.id <> em1.id
          JOIN mention_to_entity me2 ON me2.mention_id = em2.id
          JOIN entities e2 ON e2.id = me2.entity_id
         WHERE me1.entity_id = %s
           AND e2.entity_type = 'ORG'
           AND e2.id <> %s
         GROUP BY e2.id, e2.canonical_name
         ORDER BY shared_files DESC, e2.canonical_name
         LIMIT 25
        """,
        (entity_id, entity_id),
    )
    org_rows = await cur.fetchall()
    if org_rows:
        sample_names = [r[1] for r in org_rows[:3]]
        more = max(0, len(org_rows) - 3)
        subtitle = ", ".join(sample_names) + (f", +{more} more" if more else "")
        related.append(EntityProfileBucket(
            key="connected_orgs",
            label=f"{len(org_rows)} Connected orgs",
            icon="building",
            count=len(org_rows),
            subtitle=subtitle,
            deep_link_kind="entity",
            deep_link_q=canonical_name,
        ))

    # ---- Anomalies: high-rarity sub_entity rows in files mentioning the entity ----
    # Post nested-entities refactor: sub_entities live in
    # extracted_entities (`unit_type IS NOT NULL`); `fields` replaces
    # the legacy `parameters` jsonb.
    cur = await conn.execute(
        """
        SELECT ee.id::text, ee.unit_type, ee.rarity_score, f.name,
               substring(ee.fields::text from 1 for 200) AS preview
          FROM extracted_entities ee
          JOIN files f ON f.id = ee.file_id
         WHERE ee.unit_type IS NOT NULL
           AND ee.rarity_score IS NOT NULL
           AND ee.rarity_score > 0.7
           AND f.lifecycle_state NOT IN ('deleted','failed')
           AND EXISTS (
             SELECT 1 FROM extracted_mentions em
              JOIN mention_to_entity me ON me.mention_id = em.id
              WHERE em.file_id = ee.file_id
                AND me.entity_id = %s
           )
         ORDER BY ee.rarity_score DESC
         LIMIT 25
        """,
        (entity_id,),
    )
    anomaly_rows = await cur.fetchall()
    if anomaly_rows:
        top = anomaly_rows[0]
        subtitle = (
            f"{top[1] or 'unit'} on {top[3] or '?'} "
            f"(rarity {float(top[2] or 0):.2f})"
        )
        related.append(EntityProfileBucket(
            key="anomalies",
            label=f"{len(anomaly_rows)} Anomal{'ies' if len(anomaly_rows) != 1 else 'y'}",
            icon="alert-circle",
            count=len(anomaly_rows),
            subtitle=subtitle,
            deep_link_kind="anomaly",
        ))

    # ---- Narrative summary (template — LLM polish is future work) ----
    type_label = (entity_type or "entity").lower()
    summary_parts = [
        f"{entity_type or 'Entity'} mentioned in {n_docs} doc{'s' if n_docs != 1 else ''}."
    ]
    if people_rows or org_rows:
        connections = []
        if people_rows:
            connections.append(f"{len(people_rows)} people")
        if org_rows:
            connections.append(f"{len(org_rows)} other orgs")
        if connections:
            summary_parts.append(f"Connected to {', '.join(connections)}.")
    if anomaly_rows:
        summary_parts.append(
            f"{len(anomaly_rows)} anomal{'ies' if len(anomaly_rows) != 1 else 'y'} flagged in associated docs."
        )
    summary = " ".join(summary_parts)
    _ = type_label  # reserved for future LLM-narrative polish

    return EntityProfileResponse(
        id=entity_id,
        canonical_name=canonical_name,
        entity_type=entity_type or "ENTITY",
        aliases=aliases,
        first_seen=first_seen,
        last_seen=last_seen,
        n_docs=n_docs,
        mention_count=int(mention_count or 0),
        summary=summary,
        related=related,
    )


# ---------------------------------------------------------------------------
# Per-kind SQL helpers (each returns (items, total_count_estimate))
# ---------------------------------------------------------------------------


async def _search_documents(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
    filters: _SearchFilters | None = None,
) -> tuple[list[ExploreHit], int]:
    filters = filters or _SearchFilters()
    where_parts = ["lifecycle_state NOT IN ('deleted','failed')"]
    where_params: list[Any] = []
    if has_query:
        where_parts.append("name ILIKE %s")
        where_params.append(like)
    eff_doc_types = filters.effective_doc_types()
    if eff_doc_types:
        where_parts.append("inferred_doc_type = ANY(%s)")
        where_params.append(list(eff_doc_types))
    if filters.date_from:
        where_parts.append("created_at >= %s::date")
        where_params.append(filters.date_from)
    if filters.date_to:
        # `<= date + 1 day` to make the upper bound inclusive on the
        # whole day instead of requiring callers to pass timestamps.
        where_parts.append("created_at < (%s::date + INTERVAL '1 day')")
        where_params.append(filters.date_to)
    if filters.has_anomaly:
        where_parts.append(
            "EXISTS (SELECT 1 FROM extracted_entities ee "
            "  WHERE ee.file_id = files.id "
            "    AND ee.unit_type IS NOT NULL "
            "    AND ee.rarity_score IS NOT NULL "
            "    AND ee.rarity_score > 0.7)"
        )
    if filters.has_conflicts:
        # Files whose chain_id appears in fact_conflicts.chain_id.
        where_parts.append(
            "EXISTS (SELECT 1 FROM doc_chain_members dcm "
            "  JOIN fact_conflicts fc ON fc.chain_id = dcm.chain_id "
            "  WHERE dcm.doc_id = files.id)"
        )
    if filters.has_chain:
        where_parts.append(
            "EXISTS (SELECT 1 FROM doc_chain_members dcm "
            "  WHERE dcm.doc_id = files.id)"
        )
    where = " AND ".join(where_parts)
    # Sort: `name` = filename A→Z; `recent` / `relevance` = newest first
    # (the prototype's "browse the corpus" default). `name` uses LOWER()
    # so casing doesn't push uppercase filenames above lowercase ones.
    if filters.sort == "name":
        order_by = "LOWER(name) ASC, created_at DESC"
    else:
        order_by = "created_at DESC"
    params: tuple = tuple(where_params) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT id::text, name, inferred_doc_type, mime_type,
               size_bytes, created_at::text, lifecycle_state
          FROM files
         WHERE {where}
         ORDER BY {order_by}
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        f"SELECT count(*)::int FROM files WHERE {where}",
        tuple(where_params),
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="document",
            id=r[0],
            title=r[1],
            subtitle=r[2] or r[3],
            file_id=r[0], file_name=r[1],
            extra={
                "mime_type": r[3],
                "size_bytes": int(r[4]),
                "lifecycle_state": r[6],
                "inferred_doc_type": r[2],
                "created_at": r[5],
            },
        )
        for r in rows
    ]
    return items, total


async def _search_doc_types(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    where_q = "AND inferred_doc_type ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT inferred_doc_type, count(*)::int AS n
          FROM files
         WHERE lifecycle_state NOT IN ('deleted','failed')
           AND inferred_doc_type IS NOT NULL {where_q}
         GROUP BY inferred_doc_type
         ORDER BY n DESC, inferred_doc_type ASC
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(DISTINCT inferred_doc_type)::int FROM files
         WHERE lifecycle_state NOT IN ('deleted','failed')
           AND inferred_doc_type IS NOT NULL {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="doc_type",
            id=r[0],
            title=r[0],
            subtitle=f"{int(r[1])} doc{'s' if r[1] != 1 else ''}",
            extra={"file_count": int(r[1])},
        )
        for r in rows
    ]
    return items, total


async def _search_atomic_units(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
    filters: _SearchFilters | None = None,
) -> tuple[list[ExploreHit], int]:
    # Post nested-entities refactor: sub_entity rows (transactions /
    # clauses / line_items / …) live in `extracted_entities` with
    # `unit_type IS NOT NULL`. `fields` jsonb replaces `parameters`.
    # We keep the alias `au` for readability — same payload, new home.
    filters = filters or _SearchFilters()
    where_parts = [
        "f.lifecycle_state NOT IN ('deleted','failed')",
        "au.unit_type IS NOT NULL",
    ]
    where_params: list[Any] = []
    if has_query:
        where_parts.append("au.unit_type ILIKE %s")
        where_params.append(like)
    eff_doc_types = filters.effective_doc_types()
    if eff_doc_types:
        where_parts.append("f.inferred_doc_type = ANY(%s)")
        where_params.append(list(eff_doc_types))
    if filters.date_from:
        where_parts.append("f.created_at >= %s::date")
        where_params.append(filters.date_from)
    if filters.date_to:
        where_parts.append("f.created_at < (%s::date + INTERVAL '1 day')")
        where_params.append(filters.date_to)
    if filters.has_anomaly:
        where_parts.append("au.rarity_score > 0.7")
    if filters.has_chain:
        where_parts.append(
            "EXISTS (SELECT 1 FROM doc_chain_members dcm "
            "  WHERE dcm.doc_id = f.id)"
        )
    where = " AND ".join(where_parts)
    params: tuple = tuple(where_params) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT au.id::text, au.unit_type, au.fields::text,
               au.rarity_score, au.file_id::text, f.name
          FROM extracted_entities au
          JOIN files f ON f.id = au.file_id
         WHERE {where}
         ORDER BY COALESCE(au.rarity_score, 0) DESC, au.id
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM extracted_entities au
          JOIN files f ON f.id = au.file_id
         WHERE {where}
        """,
        tuple(where_params),
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="atomic_unit",
            id=r[0],
            title=str(r[1] or "(untyped unit)"),
            subtitle=(r[5] or None),
            snippet=(r[2] or "")[:300],
            file_id=r[4], file_name=r[5],
            extra={"rarity_score": float(r[3]) if r[3] is not None else None},
        )
        for r in rows
    ]
    return items, total


async def _search_entities(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
    filters: _SearchFilters | None = None,
) -> tuple[list[ExploreHit], int]:
    """Entity rows + enrichments the prototype's entity card needs:
      - Aliases: top 3 distinct surface forms from extracted_mentions
        (excluding the canonical name itself).
      - First / last mention: MIN/MAX file.created_at across mentions.

    Both come from a single LATERAL join — one DB round-trip per
    matching entity. At demo scale this is fine; at 100k entities we'd
    pre-compute these into an `entity_summary` materialized view
    refreshed on extraction.
    """
    filters = filters or _SearchFilters()
    where_q = "AND e.canonical_name ILIKE %s" if has_query else ""
    # `name` = canonical_name A→Z; `recent` = last mention first;
    # `relevance` (default) = most-mentioned first.
    if filters.sort == "name":
        order_by = "LOWER(e.canonical_name) ASC, e.mention_count DESC"
    elif filters.sort == "recent":
        # Order by most-recent mention date. Use the same subquery the
        # SELECT exposes as `last_seen`.
        order_by = (
            "(SELECT MAX(f.created_at) FROM extracted_mentions em "
            "  JOIN mention_to_entity me ON me.mention_id = em.id "
            "  JOIN files f ON f.id = em.file_id "
            " WHERE me.entity_id = e.id) DESC NULLS LAST, "
            "e.canonical_name ASC"
        )
    else:
        order_by = "e.mention_count DESC, e.canonical_name ASC"
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT e.id::text, e.canonical_name, e.entity_type, e.mention_count,
               (
                 SELECT array_agg(DISTINCT em.mention_text)
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                  WHERE me.entity_id = e.id
                    AND lower(em.mention_text) <> lower(e.canonical_name)
                  LIMIT 4
               ) AS aliases_raw,
               (
                 SELECT MIN(f.created_at)::date::text
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                   JOIN files f ON f.id = em.file_id
                  WHERE me.entity_id = e.id
               ) AS first_seen,
               (
                 SELECT MAX(f.created_at)::date::text
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                   JOIN files f ON f.id = em.file_id
                  WHERE me.entity_id = e.id
               ) AS last_seen,
               (
                 SELECT count(DISTINCT em.file_id)::int
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                  WHERE me.entity_id = e.id
               ) AS n_docs
          FROM entities e
         WHERE 1=1 {where_q}
         ORDER BY {order_by}
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"SELECT count(*)::int FROM entities e WHERE 1=1 {where_q}",
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items: list[ExploreHit] = []
    for r in rows:
        aliases = [a for a in (r[4] or []) if a][:3]
        items.append(ExploreHit(
            kind="entity",
            id=r[0],
            title=r[1],
            subtitle=r[2],
            extra={
                "mention_count": int(r[3] or 0),
                "aliases": aliases,
                "first_seen": r[5],
                "last_seen": r[6],
                "n_docs": int(r[7] or 0),
            },
        ))
    return items, total


async def _search_relationships(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    # The relationships table uses (subject, predicate, object) shape.
    # Match on predicate text when the user typed a query; otherwise
    # browse by confidence descending. n_evidence shows how many
    # triples backed each edge.
    where_q = "AND r.predicate ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT r.id::text, r.predicate, r.confidence, r.n_evidence,
               e_src.canonical_name, e_dst.canonical_name
          FROM relationships r
          LEFT JOIN entities e_src ON e_src.id = r.subject_entity_id
          LEFT JOIN entities e_dst ON e_dst.id = r.object_entity_id
         WHERE 1=1 {where_q}
         ORDER BY r.confidence DESC NULLS LAST
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"SELECT count(*)::int FROM relationships r WHERE 1=1 {where_q}",
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="relationship",
            id=r[0],
            title=str(r[1] or "(untyped)"),
            subtitle=f"{r[4] or '?'} → {r[5] or '?'}",
            extra={
                "confidence": float(r[2]) if r[2] is not None else None,
                "n_evidence": int(r[3]) if r[3] is not None else None,
            },
        )
        for r in rows
    ]
    return items, total


async def _search_topics(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    where_q = "AND text ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT id::text, level, text
          FROM raptor_nodes
         WHERE scope = 'corpus' {where_q}
         ORDER BY level DESC, id
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM raptor_nodes
         WHERE scope = 'corpus' {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = []
    for r in rows:
        level = int(r[1] or 0)
        # Level 3 = corpus root (workspace summary); level 2 = topic
        # clusters; lower = doc-level abstractions.
        if level >= 3:
            label = "Workspace summary"
        elif level == 2:
            label = "Topic cluster"
        else:
            label = f"Topic L{level}"
        snippet = (r[2] or "")[:300]
        items.append(ExploreHit(
            kind="topic",
            id=r[0],
            title=label,
            snippet=snippet,
            extra={"level": level},
        ))
    return items, total


async def _search_anomalies(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
    filters: _SearchFilters | None = None,
) -> tuple[list[ExploreHit], int]:
    filters = filters or _SearchFilters()
    # Anomalies = high-rarity sub_entity rows. After the
    # nested-entities refactor these live in extracted_entities with
    # `unit_type IS NOT NULL`; we keep the alias `au` for readability.
    where_parts = [
        "au.unit_type IS NOT NULL",
        "au.rarity_score IS NOT NULL",
        "au.rarity_score > 0.7",
        "f.lifecycle_state NOT IN ('deleted','failed')",
    ]
    where_params: list[Any] = []
    if has_query:
        where_parts.append("au.unit_type ILIKE %s")
        where_params.append(like)
    eff_doc_types = filters.effective_doc_types()
    if eff_doc_types:
        where_parts.append("f.inferred_doc_type = ANY(%s)")
        where_params.append(list(eff_doc_types))
    if filters.date_from:
        where_parts.append("f.created_at >= %s::date")
        where_params.append(filters.date_from)
    if filters.date_to:
        where_parts.append("f.created_at < (%s::date + INTERVAL '1 day')")
        where_params.append(filters.date_to)
    where = " AND ".join(where_parts)
    params: tuple = tuple(where_params) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT au.id::text, au.unit_type, au.rarity_score,
               au.file_id::text, f.name, au.fields::text
          FROM extracted_entities au
          JOIN files f ON f.id = au.file_id
         WHERE {where}
         ORDER BY au.rarity_score DESC
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM extracted_entities au
          JOIN files f ON f.id = au.file_id
         WHERE {where}
        """,
        tuple(where_params),
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="anomaly",
            id=r[0],
            title=str(r[1] or "(untyped unit)"),
            subtitle=f"rarity {float(r[2] or 0):.2f}",
            snippet=(r[5] or "")[:300],
            file_id=r[3], file_name=r[4],
            extra={"rarity_score": float(r[2] or 0)},
        )
        for r in rows
    ]
    return items, total
