# G-mode (graph reasoning) — status

**Original estimate:** ~9h of focused work
**Actual:** ~2h (most pieces were already built in earlier waves;
Bug G was the single blocker that prevented graph_edges from ever
populating)

## Shipped (this session)

### Backend — graph layer now works end-to-end

- **Bug G fix** (`221d396`) — `build_graph_file_impl` previously raised
  FK violations on every doc because lineage_pairs use
  `extracted_entities.id` pairs but `graph_edges.{src,dst}_entity_id`
  has FK to `canonical_entities(id)`. Two fixes: skip lineage edges
  (they're already in `extracted_entities.parent_entity_id`), and
  wrap every upsert in a per-edge SAVEPOINT so one bad edge can't
  abort the whole graph_built task. Construction workspace now has
  **3,628 graph edges** (140 typed relationships + 3,488 co-mentions).

- **Worker concurrency = 5** (also `221d396`) — default for
  `scripts/dev_worker.sh`. Cuts per-domain ingest from ~35 min to
  ~7 min. Tier-1 Gemini has 4× headroom at peak observed RPM.
  Both prior concurrency-race bugs are now fixed.

- **Retroactive backfill** (`e6e5a23`) —
  `scripts/rebuild_graph_edges.py` re-defers `build_graph_file` for
  every ready file in a workspace, used to fill `graph_edges` for
  docs that ingested under the old broken pipeline.

### Backend — Knowledge Map entity API (`1dbe962`)

- `GET /knowledge-map/entities` — paginated list of canonical entities
  with `mention_count` + `n_relationships` + `n_files` counters per
  row. Filters by `entity_type` and `q` substring.
- `GET /knowledge-map/entities/{id}` — side-panel payload: the entity,
  its 1-hop neighborhood (typed relationships from `relationships`
  table + co-mention edges from `graph_edges`), and the files that
  mention it.

### UI — Entities tab in Knowledge Map (`1dbe962`)

Fourth tab alongside Catalog / Needs Review / History. Compact table
with sticky filter strip (type chips + search). Click any entity to
open a slide-in side panel showing:

- Summary strip (mentions / relationships / co-mentions / files)
- **Relationships** grouped by direction with predicate text — e.g.
  "→ submitted proposal to → CIDCO", "← works for ← Rakesh Iyer"
- **Co-mentioned with** (top 20 by edge weight)
- **Files mentioning** ranked by mention count, deep-link to
  `/files/{id}`

The side panel navigates — clicking a neighbor entity opens its panel,
so you can walk the graph by hand.

### Query-side — T-mode + E-mode already shipped

`/chat` was already wired to use T-mode (PPR multi-hop) and E-mode
(single-entity boost) when the intent classifier labels a query as
`multi-hop` or `entity_lookup`. Both modes degraded silently to H-mode
prior to this work because `graph_edges` was empty — the FIX was just
populating the table. Verified working on construction:

| Query | Mode | Verdict |
|---|---|---|
| "Tell me about Mahalaxmi" | E | ✓ returns rich entity summary |
| "Who are the subcontractors of the contractor for the Acme datacentre?" | T | ✓ "Mahalaxmi → Phoenix MEP + Sai Labour" |
| "Through what suppliers does the safety officer connect to JSW Steel?" (3-hop) | T | ✓ "Mr. Pradeep Bhargava → Mahalaxmi → JSW Steel" |
| "List all parties that have worked with Mahalaxmi" | T | ✓ "CIDCO, Acme Corp, …" |

## Remaining gaps (out of scope for this session)

- **Force-directed graph viz** in the side panel — currently text-list
  format. The data is in the API already; rendering would be a
  vis-network or react-flow integration (~2-4h)
- **Cypher-style multi-hop prompt template** — current PPR-based
  approach handles 2-3 hops well in practice but there's no explicit
  "walk N hops in direction X via edges of kind Y" query language for
  the user to express deep graph traversals
- **Schema-aware predicate normalization** — currently the LLM emits
  free-form predicates ("has iso certification", "implemented by",
  "submitted proposal to"). A canonical predicate vocabulary would
  make the graph easier to reason about programmatically
