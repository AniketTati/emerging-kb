# Gaps Design — Three Tier-1 Items Designed Properly

**Date:** 2026-05-21
**Status:** design spec, integrated into `architecture.md` upon merge. Closes red-team findings F1, F2, and the doc-chain gap.
**Source validation:** May 2026 literature review (cited inline).

This doc designs the three gaps that the red-team and the scenario analysis flagged as critical:

1. **Aggregation `Q` planner mode** — turns SQL/computation queries from silent failures into first-class plans. (Closes red-team F1, F2.)
2. **Conflict detection + source authority** — when two docs disagree, the system resolves via authority + recency or surfaces the conflict cleanly. (Closes the "disagreement" hand-wave in architecture.md §6 step 8.)
3. **Doc chains** — emails-as-threads, contracts-with-amendments, drawings-with-revisions, government-circulars-with-corrigenda are modeled as logical-document chains over raw files. (Closes red-team F6 and the Enron thread gap.)

Each is designed end-to-end: data model, pipeline integration, planner integration, generation behavior, UI surface, failure modes, eval criteria.

**Update (post problem-brief re-review):** Designs 6–9 added below to close four functional requirements (domain vocabulary, hierarchical containment + lineage, conversational context, layered config). Total now 9 designs. Remaining items (temporal AS-OF, schema ops beyond add, meta M1–M4) flagged at the end as the next batch.

---

## Design 1 — Aggregation `Q` planner mode

### Problem

About 14 scenarios queries in `docs/scenarios.md` are SQL-shaped aggregations or set operations, today graded "✓" but actually wrong-routed through the retrieval pipeline (red-team F1, F2). They return plausible-looking answers built from the top-20 retrieved chunks rather than the actual aggregation over the full dataset. The CFO query *"Total vendor spend across petrochem in Q2 2025"* lands with a confidently wrong number sourced from 20 of 5,127 invoices.

Industry 2026 pattern is well-established: **multi-stage retrieval where initial RAG narrows the scope, then SQL-based refinement does the math** (Azure AI Search agentic retrieval, CSR-RAG arxiv 2601.06564, Hebbia Matrix output shape). We adopt this directly.

### Data model

No new tables — the `Q` mode operates over the existing extraction layers:

- `extracted_entities` (schema-projected typed records)
- L3 atomic units (`clauses`, `transactions`, `line_items`, `components`, `rows`, ...)
- L5 `relationships` (typed edges with provenance)
- `files` (doc metadata, source authority, doc-chain membership)

What is added: a **read-only SQL execution path** alongside retrieval, with a parameterized query builder, allowlist of tables, and a budget+timeout enforcer.

### Plan grammar

The planner emits a `Q` plan as JSON:

```json
{
  "mode": "Q",
  "from": "Contract",
  "join": [
    { "table": "extracted_entities.Invoice", "on": "contract_id" },
    { "table": "relationships",
      "on": "Invoice.vendor = relationships.subj",
      "predicate": "supplies_to" }
  ],
  "filter": [
    { "field": "Invoice.doc_type",    "op": "eq",      "value": "invoice" },
    { "field": "Invoice.signed_date", "op": "between", "value": ["2025-04-01","2025-06-30"] },
    { "field": "relationships.obj",   "op": "eq",      "value": "Reliance_Petrochem" }
  ],
  "group_by":  ["Invoice.vendor"],
  "aggregate": [
    { "fn": "SUM",   "field": "Invoice.amount", "as": "total_spend" },
    { "fn": "COUNT", "as": "n_invoices" }
  ],
  "order_by":  [{ "field": "total_spend", "desc": true }],
  "limit":     50,
  "set_op":    null
}
```

For Boolean set operations (the "subcontractors with delayed delivery AND safety incident" class), the planner emits `set_op: "intersect"` over a list of sub-plans:

```json
{
  "mode": "Q",
  "set_op": "intersect",
  "key": "Subcontractor.id",
  "plans": [
    { ... Q plan filtering DeliveryRecord with status=delayed in 2025 ... },
    { ... Q plan filtering SafetyIncident in 2025 ... }
  ]
}
```

Three set ops: `intersect`, `union`, `except`. The `key` field declares the joining identifier.

### Plan generation

Gemini Flash with a constrained-JSON output schema and ~12 few-shot examples covering:
- Plain aggregation (SUM, AVG, COUNT, MIN, MAX)
- Group-by with HAVING-style threshold
- Two-table join with predicate
- Set intersection (AND), union (OR), difference (NOT)
- Time-range filter (`between`, `before`, `after`, `during_quarter`, `during_year`)
- Field aliasing for natural-language synonyms

Generation runs in parallel with the retrieval pipeline. The intent classifier (which now includes `aggregation` and `set_operation`) determines whether `Q` mode is *primary* (skip retrieval rerank, return aggregation directly) or *augmenting* (retrieval narrows the doc set, then `Q` aggregates over it).

For ambiguous queries, both paths run and the answer combines: *"Across the 5,127 invoices that match (total ₹4,213 cr by SUM), the top 3 vendors by spend are X, Y, Z [retrieved candidate chunks with details]"*.

### Validation

**Numerical limits below are operating defaults — configurable per workspace.** Before any SQL hits the database, a validator runs:

1. **Field existence**: every referenced field must exist in the current schema version or the L3 parameter schemas. Misses surface as *"I couldn't find a field for `vertical`. Closest matches in your schema: `business_unit`, `division`."*
2. **Type compatibility**: `SUM` / `AVG` require numeric fields; `COUNT` is unrestricted; `MIN`/`MAX` work on numeric, date, ordinal. Mismatch surfaces as *"`notes` is text, can't sum. Did you mean `amount`?"*
3. **Predicate-value type**: `signed_date between [..]` requires ISO date strings; `vertical eq ..` requires the enum value to exist (warning if free text).
4. **Allowlist**: only the read tables listed in the data-model section above. No `DROP`/`UPDATE`/`DELETE`/`CREATE`. No `INFORMATION_SCHEMA` introspection.
5. **Join depth**: `join.length ≤ 3` by default; warn at 4; refuse at 5.
6. **Cardinality budget**: pre-flight `EXPLAIN` estimate; if estimated rows > 10M before aggregation, refuse with *"this query would scan 12.4M rows — please tighten filters."*
7. **Timeout**: SQL execution capped at 30s; surfaces as *"the query timed out; here's a sample of partial results."*

### Execution + Security (this matters because the Q-plan JSON comes from an LLM)

The validated `Q` plan compiles to parameterized SQL via a strict query builder (`src/kb/core/q_planner/`). **Defense-in-depth against SQL injection — the LLM can be adversarial or simply wrong, so we assume zero trust on its output:**

1. **Field-name whitelist:** every field reference in the JSON plan must resolve to a known `(table, column)` from `schemas.schema_entities + schemas.schema_fields + L3.atomic_unit_parameters + L5.relationships`. Any field not in the catalog → refuse with "field doesn't exist; closest matches: …".
2. **Operator enum:** allowed operators are `{eq, ne, lt, le, gt, ge, in, not_in, between, like, is_null}`. Anything else → refuse.
3. **Aggregation enum:** allowed functions are `{SUM, COUNT, AVG, MIN, MAX, COUNT_DISTINCT}`. Anything else → refuse.
4. **Set-op enum:** allowed are `{intersect, union, except}`. Anything else → refuse.
5. **Values via parameter placeholders only:** every literal value goes through `$N` (psycopg parameter substitution). **Never string-interpolated.** Even if the LLM emits `"value": "'; DROP TABLE files;--"`, it becomes a literal `text` parameter, not SQL.
6. **No raw-SQL escape hatch in the grammar.** No way to write a `WHERE` clause as a string. The compiler refuses any plan field not built from validated atoms.
7. **Read-only PG role.** The connection used for Q-mode is a dedicated role with `SELECT` permission only on the allowed tables. Even if a bypass exists in the compiler, `DROP/UPDATE/DELETE/CREATE` fail at the DB layer.
8. **`SET statement_timeout = 30s`** — defends against `pg_sleep(...)`-based timing exfiltration and runaway queries.
9. **Result row cap before aggregation** — default 100,000 rows; configurable via Design 9. Prevents memory exhaustion.
10. **Audit log captures the compiled SQL + parameters + row count** — every Q-mode execution is reconstructable.

This is layered defense: even a determined adversarial LLM (or a manually-crafted malicious Q-plan POSTed to the API) gets blocked at layer 1 (catalog whitelist); if somehow bypassed, layer 5 (parameterization) makes injection harmless; if somehow bypassed, layer 7 (read-only role) prevents writes; layer 8 (timeout) prevents resource exhaustion. **Each layer alone is not sufficient; together they are.**

### Architectural illustration vs. demo example

The "₹4,213 cr across 5,127 invoices" example below is the **Reliance-scale architectural illustration** — what the system is built for, not what we demo on 80–100 docs.

**Demo-runnable aggregation queries** (computable live on the actual CUAD + Enron + SEC corpus):

1. *"Total aggregate indemnification cap across all our contracts."* — L3 indemnification clauses with `cap_amount` parameter. Sums to a real number; row count is real (~18 indemnification clauses in CUAD subset). **This is the canonical Moment 4 demo query.**
2. *"Average term length of license agreements."* — filter `doc_subtype=license`, aggregate `term_years` across ~15 license agreements in CUAD.
3. *"Count emails by sender in the Enron M&A thread cluster."* — group-by aggregation on doc-chain ID.
4. *"Top 5 most frequent counterparties across our contract corpus."* — L4 entity + L5 relationship grouping.

Each of these produces a *real* number from data we actually have ingested. Generation surfaces the row count + top-N inline + the audit-artifact citation. **Reliance numbers are aspirational; CUAD numbers are demonstrable.**

### Result formatting (generation)

Aggregation answers are **templated**, not freeform LLM output. Generation uses Gemini Flash with a strict template:

```
Across the {n_total} {entity_type} matching {filter_summary},
{aggregate_function_phrase} was {value}{unit},
computed at {timestamp} from query {audit_link}.

{top_n_rows_table}

{breakdown_caveats}
```

Concrete example:

> Across the **5,127 invoices** matching `doc_type=invoice ∧ vertical=petrochem ∧ signed_date in Q2 2025`, **total vendor spend** was **₹4,213 crore**, computed at 2026-05-21 14:32:01 IST from query [audit#a7c2].
>
> **Top 5 vendors by spend:**
>
> | Vendor              | Total spend | # invoices |
> |---------------------|------------:|-----------:|
> | Reliance Logistics  | ₹612 cr     | 487        |
> | Indian Oil Corp     | ₹438 cr     | 322        |
> | Mumbai Petrochem    | ₹279 cr     | 198        |
> | ...                 |             |            |
>
> [Download full row list (5,127 invoices, CSV)] · [View query plan]
>
> *3.2% of invoices in this period were missing the `vertical` field at extraction time and may be undercounted. Schema rerun would close this gap (est. ~$0.50, ~2 min).*

The "breakdown caveats" line is the key honesty feature: the system reports its own incompleteness.

### Citation strategy for aggregates

A single aggregate answer cites **the query itself**, not 5,127 individual rows. The audit log entry for the query becomes a citable artifact. Citation `[audit#a7c2]` resolves to:

- The exact `Q` plan that was executed
- The SQL that was generated
- The full row list (downloadable CSV)
- The schema version at the time of execution
- The L3 extraction completeness stats per filtered field

For the top-N rows that *are* shown inline, each row links back to its source document (existing per-doc citation behavior).

### Failure modes

| Symptom | UX behavior |
|---|---|
| Field doesn't exist | Suggest closest matches; offer to schema-edit or rephrase |
| Empty result | "0 matches. Closest filter: ..." with one-step relaxation suggestions |
| Type mismatch | "Can't aggregate `notes` (text). Did you mean `amount`?" |
| Cardinality budget exceeded | Refuse with row-count estimate; suggest tighter filters |
| Timeout | Partial results + offer background-job mode |
| Genuine ambiguity ("which `revenue`?") | Tree-of-Clarifications branch, prompt user |
| Schema field has low extraction coverage | Surface caveats inline: *"3.2% of invoices were missing the `vertical` field"* |

### UI integration

- **Chat page**: aggregation answers render as templated cards with the table inline.
- **"How I answered" inspector**: shows the `Q` plan JSON, the generated SQL, and the validator output.
- **Download artifact** (CSV / JSON) appears as a button alongside the answer.
- **Schema Studio**: clicking a field shows "this field is used in 23 saved aggregation queries" if applicable.

### Eval stratum (new, 5 questions)

Replaces or augments existing strata. Pass criteria per question:

| Question shape | Pass criterion |
|---|---|
| "Total of X by Y in time range Z" | Aggregate value within ±1% of ground truth AND row count exact |
| "Top-K X by Y" | Top-K set identical to ground truth |
| "X with both A AND B" | Set intersection identical to ground truth |
| "X having Z > threshold" | Result set identical to ground truth |
| "Show me trend of X over time" | Time-series data points within ±2% per bucket |

CI gate: aggregation stratum accuracy ≥ 0.95.

### References

- CSR-RAG (text-to-SQL with hybrid retrieval, 80% recall + 30ms latency): [arxiv 2601.06564](https://arxiv.org/pdf/2601.06564)
- Microsoft Azure AI Search agentic retrieval pattern
- Hebbia Matrix multi-agent output shape: [hebbia.com](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign)
- Multi-Query RAG aggregation (2026): [dasroot.net](https://dasroot.net/posts/2026/04/multi-query-re-ranking-advanced-rag/)

---

## Design 2 — Conflict detection + source authority

### Problem

Two docs in the corpus assert different values for the same fact. The architecture's §6 step 8 says generation should "surface disagreement, do not pick one" — a slogan with no design. Today the generation prompt has no signal of which source is more authoritative; the answer ranks candidates by retrieval similarity, which can pick a draft memo over an audited filing.

Industry 2026 has named patterns: **ConflictRAG** (arxiv 2605.17301) detects-classifies-resolves; **DARE** (Springer 2026) does dialectical adversarial resolution; **RAG with source-reliability estimation** (arxiv 2410.22954) weights candidates. The dominant resolution principle across the literature: **"authority and recency dominate."** We adopt this directly.

### Data model

New columns on `files`:

```sql
ALTER TABLE files ADD COLUMN source_authority NUMERIC(3,2) NOT NULL DEFAULT 0.5;
ALTER TABLE files ADD COLUMN source_authority_reason TEXT;
ALTER TABLE files ADD COLUMN doc_status TEXT
  CHECK (doc_status IN ('live','superseded','draft','archived','retracted'))
  NOT NULL DEFAULT 'live';
```

New table for explicit conflicts at the fact level:

```sql
CREATE TABLE fact_conflicts (
  id              UUID PRIMARY KEY,
  entity_id       UUID REFERENCES entities,
  predicate       TEXT NOT NULL,
  observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  evidence        JSONB NOT NULL,   -- list of (doc_id, value, authority, recency, span)
  resolution      TEXT CHECK (resolution IN ('authority','recency','chain','unresolved','user')),
  resolved_value  TEXT,
  resolved_doc_id UUID,
  notes           TEXT
);
```

### Source-authority defaults per doc-type

**These numerical values are our operating defaults — not research-validated.** Literature establishes the principle ("authority and recency dominate" — ConflictRAG, κ-RRSS, DARE) but does not specify a canonical scale. Per-workspace tunable; per-doc override available in the UI. See `docs/citations_audit.md` §2.3.

**How authority is assigned to a doc at ingest** (the part that was missing): the L2b classifier outputs a `doc_type_proposal` (e.g., `sec_10k_filing`, `cardiac_catheterization_report`, `internal_memo_draft`). Each doc-type has a config entry at `config/doc_types/<type>.yaml` (Design 9 layered config) declaring its default authority + reason. The ingest pipeline reads that entry and populates `files.source_authority` accordingly. Users can override per-doc in the Doc Detail panel.

Example doc-type configs:
```yaml
# config/doc_types/sec_10k_filing.yaml
authority: 1.00
authority_reason: "SEC-filed audited annual report; legally binding disclosure"
```
```yaml
# config/doc_types/internal_memo_draft.yaml
authority: 0.30
authority_reason: "Draft internal memo; usually superseded by executed versions"
```

Defaults (used if a doc-type config doesn't declare authority):

| Doc type | Default authority | Examples |
|---|---:|---|
| Audited regulatory filing | 1.00 | 10-K, FDA report, court order, certified registry |
| Executed signed contract / amendment | 0.90 | CUAD contracts, board resolutions |
| Approved internal policy | 0.80 | HR handbook, SOP |
| Lab/test report (signed by issuer) | 0.80 | Pathology report, QC certificate |
| Meeting minutes (approved) | 0.70 | Board minutes, committee minutes |
| Internal memo | 0.60 | Working memo, briefing note |
| Email (from authoritative sender) | 0.50 | Internal correspondence |
| Email (forwarded / external) | 0.30 | Vendor-to-internal forward |
| Draft document | 0.30 | Anything tagged `_draft.docx` |
| Handwritten / informal note | 0.20 | Field note, scribble |
| External news / blog | 0.20 | Press article, blog post |

User can override per-doc with a workspace UI; the `source_authority_reason` field records why.

### Conflict detection

A conflict is detected at generation time, not at ingest. Detection logic:

For each *factual claim* about to be cited in the response, the system asks: do any other retrieved candidates assert a *different* value for the same `(entity_id, predicate)` tuple?

Practical: the planner's `H` (hybrid) and `E` (entity) channels both return candidates with structured field values. The generation prompt receives an annotated candidate set:

```
Candidate A (doc=enron_epe.pdf, authority=0.90, date=1999-03-15)
  Fact: indemnification_cap = $25,000,000
Candidate B (doc=enron_epe_draft.docx, authority=0.30, date=1999-02-20)
  Fact: indemnification_cap = $50,000,000
Candidate C (doc=enron_epe_amendment2.pdf, authority=0.90, date=2001-06-10)
  Fact: indemnification_cap = $50,000,000
```

A conflict-detector prompt step (cheap, Gemini Flash) examines the candidate annotations and emits structured conflict records into `fact_conflicts` when values disagree for the same `(entity, predicate)`.

### Resolution rules — applied in order

1. **Doc-chain check.** If both docs are in the same doc-chain and one supersedes the other (see Design 3), use the chain's `current_version_doc_id`. **This is not a conflict; it is supersession.** Generation explicitly says: *"updated by Amendment 2 in 2001"*.

2. **Status filter.** If any candidate has `doc_status ∈ {superseded, archived, retracted, draft}`, drop it from primary citation unless query explicitly asks for history (`"what did we know in 1999?"`).

3. **Authority dominates.** If the authority gap between the highest-authority candidate and the next ≥ 0.3, use the higher. Surface the lower as: *"An older draft showed $50M, but the executed contract sets it at $25M."*

4. **Recency tiebreaker.** When authority is equal or within 0.3, the more-recent doc wins, with explicit *"the older record showed X; more recent evidence shows Y"* framing.

5. **Unresolvable.** Authority and recency both ambiguous → surface both, side-by-side, no winner. *"I found two contradictory answers in similarly-authoritative documents from similar dates. Please review."*

### Generation behavior

The system prompt for generation includes the conflict annotations and the resolution rules. The output template for a resolved conflict:

> **Indemnification cap** in the Enron / El Paso power supply contract is **$25 million** [¹] — as set in the executed 1999 agreement.
>
> An earlier February 1999 draft listed $50M [²]; a 2001 amendment retained the executed $25M figure [³].

`[¹]` = executed contract (authority 0.90, primary citation)
`[²]` = draft (authority 0.30, contextual citation, marked "draft" in citation card)
`[³]` = amendment (authority 0.90, marked "current per amendment chain")

For an *unresolvable* conflict the answer becomes:

> **Two contradictory values** for the indemnification cap appear in similarly authoritative sources:
>
> - **$25M** per [¹] Enron contract (authority 0.90, 1999)
> - **$50M** per [²] internal counsel memo (authority 0.80, 1999)
>
> The discrepancy is not explained by a known amendment chain. Please review.

### UI surface

- **Citation cards** show authority badge and `doc_status` chip. A "superseded" or "draft" chip is visually distinct.
- **Conflict cards** render side-by-side when generation surfaces an unresolvable conflict.
- **Dashboard › Needs attention** (per the locked 10-surface IA · `ui_design.md` §6.7) and **per-doc Doc Detail panel** list open `fact_conflicts` rows with `resolution = 'unresolved'`. Admin can review and resolve manually; resolution is audit-logged. *(In the pre-prototype IA this lived in Explore › Conflicts tab.)*
- **Doc Detail panel** shows the doc's `source_authority` + `doc_status` prominently and offers an override (with reason).

### Failure modes

| Symptom | UX behavior |
|---|---|
| Two equally authoritative recent docs disagree | Surface both, do not pick |
| Draft doc has fact not in any final doc | Cite the draft, mark with "draft only" chip |
| Authority unknown (no doc-type classifier label) | Default to 0.5; mark with "authority not assessed" chip |
| Doc-chain detection missed an amendment | Surface as conflict; user can manually link the chain |

### Eval coverage

The negative / refusal stratum and a new "conflict resolution" sub-stratum:

| Question shape | Pass criterion |
|---|---|
| "Indemnity cap in X contract?" (when amendment supersedes) | Cite current value AND mention amendment |
| "What did our policy say in Y?" (historical) | Cite the historical version explicitly, mark current is different |
| "Which is the right answer?" (genuine conflict) | Surface both, do not pick |
| Authority gap query (draft vs. executed) | Cite executed, contextualize draft |

CI gate: conflict-handling accuracy ≥ 0.90.

### References

- ConflictRAG (detect-classify-resolve, Entropy-TOPSIS source credibility, 88.7% F1): [arXiv 2605.17301](https://arxiv.org/abs/2605.17301)
- DARE (dialectical adversarial RAG, evidence-aware cross-examination): [Springer 2026](https://link.springer.com/chapter/10.1007/978-3-032-21300-6_27)
- κ-RRSS / RAG with Estimation of Source Reliability: [arXiv 2410.22954](https://arxiv.org/pdf/2410.22954)
- Astute RAG (source-aware iterative consolidation): [arXiv 2410.07176](https://arxiv.org/abs/2410.07176)
- Document prioritization patterns: [customgpt.ai](https://customgpt.ai/prioritize-documents-in-rag-retrieval-process/)
- Reliable RAG via source credibility: [poniaktimes.com](https://www.poniaktimes.com/reliable-rag-ai-search/)

---

## Design 3 — Doc chains (logical documents)

### Problem

Three real document types in our scenarios are *chains* of related raw files, not standalone units:

- **Email threads** (Enron corpus): reply, forward, quote-of-quote — the *thread* is the unit of meaning, not the individual email.
- **Contract chains**: Original + Amendment 1 + Side Letter + Amendment 2 = one logical contract.
- **Drawing revisions** (L&T scenario): C7-v1 → C7-v2 → C7-v3 → "latest revision" queries.
- **Government circulars**: original notification + corrigenda.
- **Patient charts** (Apollo): related encounters / labs / discharges.

Today we treat each raw file as standalone. Retrieval returns chunks across the chain unordered; thread context is lost; amendment supersession is invisible; "latest revision" queries are silently wrong.

This also resolves **half of the temporal problem** without full bi-temporal — chain ordering gives "before/after" semantics for free.

### Data model

Two new tables sitting between L0 (raw files) and L1 (parse):

```sql
CREATE TABLE doc_chains (
  id                    UUID PRIMARY KEY,
  type                  TEXT NOT NULL
                        CHECK (type IN ('email_thread','contract_chain',
                                        'drawing_revisions','circular_chain',
                                        'patient_chart','other')),
  title                 TEXT,
  current_version_id    UUID REFERENCES files,   -- null for email_thread
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  member_count          INT NOT NULL DEFAULT 0,
  detection_confidence  NUMERIC(3,2) NOT NULL
);

CREATE TABLE doc_chain_members (
  chain_id      UUID NOT NULL REFERENCES doc_chains ON DELETE CASCADE,
  doc_id        UUID NOT NULL REFERENCES files,
  version_index INT NOT NULL,    -- ordering within chain
  role          TEXT NOT NULL
                CHECK (role IN ('original','amendment','side_letter','superseded',
                                'reply','forward','revision','corrigendum',
                                'encounter','lab','discharge','other')),
  parent_doc_id UUID REFERENCES files,    -- for tree-shaped threads
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chain_id, doc_id)
);

CREATE INDEX idx_chain_members_doc ON doc_chain_members(doc_id);
```

### Detection — per chain type

**Heuristic thresholds below are configurable defaults — chosen as sensible starting points.** Detection runs as a new ingest pipeline stage immediately after parse (between current steps 5 and 6 in §5):

#### Email threads

- Parse `In-Reply-To`, `References`, `Message-ID` headers from EML
- Normalize subject (strip `Re:`, `Fwd:`, `[EXTERNAL]` prefixes; collapse whitespace)
- Two emails join a thread if any of: shared `References` ancestor; reply-to chain; same normalized subject + sender/recipient overlap ≥ 0.5 + within 30 days
- `role` = `'reply'` if In-Reply-To matches an existing member; `'forward'` if subject prefix is `Fwd:`; `'original'` if first chronologically
- Tree-shaped: `parent_doc_id` links to the email being replied to

#### Contract chains

- Title similarity ≥ 0.7 (normalized: drop "Amendment N", "Side Letter", "v2", date suffixes)
- Same parties (entity-level match after L4)
- Explicit relation language in opening clauses: regex on phrases like *"amends"*, *"supplements"*, *"supersedes Section X of"*, *"this Amendment N to"*. **English-only for the demo** — Marathi/Hindi/Devanagari and other non-English corpora need either per-language regex sets OR a Gemini Flash LLM-judge fallback (more expensive, more flexible). Same Wave C scope as cross-lingual L3 atomic-unit extraction.
- `role` = `'amendment'` / `'side_letter'` / `'superseded'` based on language detected
- `current_version_id` = latest amendment by date

#### Drawing revisions

- Filename pattern: same base name + revision suffix (`_RevA`, `_v2`, `_R03`)
- Same project_id metadata (extracted via L2 mentions)
- Date stamp in title block (extracted at parse)
- `role` = `'revision'`; `current_version_id` = highest revision number

#### Government circulars / corrigenda

- Title similarity ≥ 0.8 between circular and its corrigendum
- Corrigendum has explicit "Corrigendum to GR No. X" header
- `role` = `'corrigendum'`; original GR keeps `'original'`

#### Patient charts

- Same `patient_id` entity (L4) across encounters
- `role` = `'encounter'`/`'lab'`/`'discharge'` based on doc-type classification
- No `current_version_id` (charts are time-series, not versioned)

### Pipeline integration

A new step in §5 between current step 5 (raw_pages) and step 6 (chunking):

```
5.5  Doc-chain detection
       per parsed doc:
         - emit candidate links to existing chains (heuristics above)
         - LLM judge on borderline cases (Gemini Flash, cheap, ~$0.001/doc)
         - on confident match: insert doc_chain_members row
         - on weak match: create new chain or leave unchained
         - on confident amendment: update parent chain's current_version_id
```

Chains are *cheap* — most are < 5 members. Detection cost ~$0.001/doc.

### Retrieval integration

The planner gains a new mode `K` (chain):

```json
{
  "mode": "K",
  "filter": "...",
  "policy": "current_version_only" | "all_versions" | "history_only",
  "ordered": true | false
}
```

Behavior:
- **`current_version_only`** (default for most queries): when retrieving from a chain, return only the doc at `current_version_id`. Hides superseded versions.
- **`all_versions`**: return chain card with all members; useful for *"show me everything we have on this contract"*.
- **`history_only`**: return only superseded members; for *"what did our policy say before the amendment?"*.

When a retrieval channel surfaces *any* doc that is a chain member, the retriever automatically attaches the chain context to the candidate (chain_id, role, version_index, current_version_id). RRF and rerank operate on candidates; generation receives the chain-aware annotations.

### Generation behavior

When generation cites a doc that is part of a chain, it must:

1. Surface the chain context: *"Per the executed 1999 agreement, as amended by Amendment 2 in 2001 [¹][²]..."*
2. For "latest" queries, cite the current version explicitly: *"The latest revision of drawing C7 (revision 03, dated 2024-08-15) [¹]..."*
3. For "history" queries, walk the chain in order and surface the evolution: *"Originally $25M [¹], proposed at $50M in a February draft [²], retained at $25M in the executed contract [³], unchanged through Amendment 2 [⁴]."*

This is also how conflicts that are actually *supersessions* get correctly classified (Design 2 resolution rule #1).

### UI surface

- **Citation cards** show chain info: a "chain of 3" badge linking to the chain timeline.
- **Doc Detail panel** has a "Chain" section listing siblings with their roles and dates.
- **New view: Chain timeline**. Click the chain badge → visualize the chain as a horizontal timeline with each member, role, and the "current" indicator. Click any member → open Doc Detail for that version.
- **Schema Studio › Inferred** notes when an inferred doc-type tends to form chains (e.g., "vendor_purchase_order documents often have amendment chains").

### Failure modes

| Symptom | UX behavior |
|---|---|
| Two unrelated docs falsely chained | "Unlink" button on chain timeline; audit-logged |
| Amendment language not detected | Doc stays standalone; user can manually link from Doc Detail |
| Email reply with broken In-Reply-To header | Falls back to subject + participant overlap matching |
| `current_version_id` ambiguous (two amendments on same date) | Most-recently-ingested wins; surface as conflict per Design 2 |
| Chain too large (Enron mega-threads) | Cap chain size at 100 members; emit warning; user can review |

### Eval coverage

New stratum (5 questions); existing strata gain chain-aware criteria:

| Question shape | Pass criterion |
|---|---|
| "Latest revision of X" | Cite the current_version_id member of the chain |
| "What was the original cap before amendment?" | Cite the original member; mark current value distinct |
| "Resolution of email thread on Mexico deal" | Read members in order; reconstruct the thread's conclusion |
| "Show me how this policy evolved" | Walk the chain; cite each member with role |
| "Are there any amendments to X contract?" | List chain members with roles |

CI gate: chain-aware retrieval accuracy ≥ 0.90 on chain stratum.

### References

- Conversational context and threading in enterprise email (Glean knowledge graph): [glean.com](https://www.glean.com/resources/guides/glean-knowledge-graph)
- Amendment / superceding document detection in legal text (CUAD precedent): [CUAD arxiv](https://arxiv.org/abs/2103.06268)
- Recency weighting patterns: [ConflictRAG arxiv](https://arxiv.org/html/2605.17301), [Reliable RAG poniaktimes](https://www.poniaktimes.com/reliable-rag-ai-search/)

---

## Integration touchpoints in `architecture.md`

### §1 Reality A.1
Add a sentence noting that doc-chain awareness is in the architecture (not just per-file).

### §2 Multi-Resolution Knowledge Representation
Insert a new line for L0.5:

```
L0.5  DOC CHAINS           Logical groupings over raw files: email threads,
                           contract+amendment chains, drawing revisions,
                           circulars+corrigenda, patient charts. Detection
                           at ingest; per-chain ordering + current-version
                           pointer.
```

### §5 Indexing Pipeline
- New step 5.5: doc-chain detection
- New steps 12b–12d (already added in prior edit): L2b emergent fields
- Step 14: L3 unchanged
- Add note: schema-projected facts inherit `doc_status` and `source_authority` from their source file

### §6 Query-Time Pipeline
- Step 1 (intent classifier): add `aggregation`, `set_operation`, `temporal_history`, `chain_aware` as recognized intents
- Step 3 (planner): add modes `Q` (aggregation/SQL) and `K` (chain)
- Step 4 (parallel retrieval): retrieval channels now annotate candidates with `chain_id`, `chain_role`, `source_authority`, `doc_status`
- Step 7 (CRAG): conflict detector runs alongside; emits `fact_conflicts` rows when retrieved candidates disagree
- Step 8 (generation): receives conflict annotations; applies resolution rules in order (chain → status → authority → recency); aggregation answers use templated output

### §7 Storage Stack
Add tables:
```
doc_chains, doc_chain_members,
fact_conflicts
```
Add columns:
```
files.source_authority, files.source_authority_reason, files.doc_status
```

### §9 Evaluation Design
- New strata: `aggregation` (5q), `chain_aware` (5q), `conflict_resolution` (sub-stratum within negative)
- New CI gates: aggregation accuracy ≥ 0.95; chain-aware accuracy ≥ 0.90; conflict-handling accuracy ≥ 0.90

### §11 UI surface
- Chat citation cards: authority + status badges
- Doc Detail panel: source authority + chain section
- Explore page: new "Conflicts" tab + "Chains" tab (or sub-tabs under existing Doc Types)
- Chain timeline view (new)

### §13 Cost
- Doc-chain detection adds ~$0.001/doc → +$100 at 100K-doc scale (negligible)
- Conflict detector adds 1 Gemini Flash call per ambiguous query (~10% of queries) → +$0.0005 average per query (negligible)
- `Q` mode aggregation queries are 2–3× cheaper than retrieval queries (no rerank, no generation context-stuffing) — net cost slightly lower

---

## Design 4 — User feedback / correction loop

### Problem

The system has no path from *"this answer is wrong"* back into the data. A user asks for indemnity caps; the system returns 4 contracts; the user says *"contract 3 has a higher cap on page 14, you missed it"*. Today: the correction lands in chat history. The L3 extraction error on contract 3 stays uncorrected. The next user gets the same wrong answer. The system has **learned nothing**.

This is the single most-asked enterprise feature and the cleanest signal of "real KB" vs. "stateless RAG demo." Industry confirms: NotebookLM and Glean both have feedback gaps flagged by users; production teams cite "no systematic eval for retrieval quality" as a top-3 failure mode ([Redis 2026](https://redis.io/blog/rag-at-scale/), [DEV.to](https://dev.to/gabrielanhaia/70-of-enterprise-rag-deployments-fail-before-production-heres-what-kills-them-26ml)).

### Design principle

Feedback is **structured at the point of complaint**. The user doesn't write a paragraph; they click on the wrong thing (citation, extraction, entity, field) and the system captures the precise target. This requires precise citations across modalities — which is exactly what Design 5 unlocks.

### Data model

```sql
CREATE TABLE corrections (
  id              UUID PRIMARY KEY,
  user_id         UUID,
  workspace_id    UUID NOT NULL,
  scope           TEXT NOT NULL CHECK (scope IN (
    'answer','citation','extraction','entity_merge','entity_split',
    'schema_field','doc_chain','source_authority','other'
  )),
  target          JSONB NOT NULL,         -- precise target: query_id, doc_id+span,
                                          -- entity_id, field_name, chain_id, etc.
  observed_value  TEXT,                   -- what the system said
  correct_value   TEXT,                   -- what user says is right (optional)
  reason          TEXT,                   -- user free-text
  severity        TEXT CHECK (severity IN ('blocker','important','minor','enhancement'))
                  DEFAULT 'important',
  status          TEXT CHECK (status IN (
    'open','triaged','fixing','verified','closed','rejected'
  )) DEFAULT 'open',
  resolution      JSONB,                  -- what we did about it
  audit_query_id  UUID,                   -- back-link to the offending query
  created_at      TIMESTAMPTZ DEFAULT now(),
  resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_corrections_scope_status ON corrections(scope, status);
CREATE INDEX idx_corrections_target_doc    ON corrections((target->>'doc_id'));
CREATE INDEX idx_corrections_target_entity ON corrections((target->>'entity_id'));

CREATE TABLE entity_overrides (
  id           UUID PRIMARY KEY,
  rule_type    TEXT CHECK (rule_type IN ('never_merge','always_merge','rename','split')),
  entity_a     UUID,
  entity_b     UUID,
  rename_to    TEXT,
  reason       TEXT,
  created_by   UUID,
  created_at   TIMESTAMPTZ DEFAULT now(),
  active       BOOLEAN DEFAULT true
);

CREATE TABLE schema_field_overrides (
  id              UUID PRIMARY KEY,
  field_path      TEXT NOT NULL,    -- e.g., "Contract.indemnity_cap"
  override_kind   TEXT CHECK (override_kind IN (
                    'undo_promotion','retype','rename','blacklist'
                  )),
  details         JSONB,
  reason          TEXT,
  created_by      UUID,
  created_at      TIMESTAMPTZ DEFAULT now(),
  active          BOOLEAN DEFAULT true
);

CREATE TABLE regression_set (
  id              UUID PRIMARY KEY,
  source_correction_id UUID REFERENCES corrections,
  query_text      TEXT NOT NULL,
  expected_facts  JSONB NOT NULL,   -- structured assertions: "answer must cite X"
  implicated_docs UUID[],
  severity        TEXT,
  active          BOOLEAN DEFAULT true,
  last_pass_at    TIMESTAMPTZ,
  last_fail_at    TIMESTAMPTZ,
  fail_count      INT DEFAULT 0
);
```

### Feedback flow

```
User clicks "wrong" on a target  (citation / extraction / entity / answer / field)
   ↓
Feedback dialog opens with target pre-filled.
User picks a reason chip (or free-text) and optionally provides correct value.
   ↓
INSERT corrections row, status='open'.
   ↓
Severity classifier (Gemini Flash, ~$0.0002):
   blocker     — answer was wrong in a high-stakes domain or contradicts user-supplied truth
   important   — extraction error, missed citation, wrong entity merge
   minor       — typo in label, slightly off
   enhancement — "this would be nicer if..."
   ↓
ROUTE based on scope + severity:

  scope='extraction' + severity ∈ {blocker, important}:
    → trigger TARGETED RE-EXTRACTION on implicated doc(s):
        - high-effort prompt (Gemini Pro, more context)
        - explicit hint: "user reports field X was Y but should be Z"
        - re-run L3 + L2b for that doc only
        - overwrite extracted rows; mark with correction_id provenance
        - audit log records before/after
    → status='fixing'
    → notify user when done (realistic 30-90s — 3 Pro calls at ~10s each
       for L2b + L3 + schema-driven re-run, plus DB writes + audit + regression
       set insert; best case ~30s on a small doc, upper bound ~90s on a 30-page
       contract under good network)

  scope='entity_merge':
    → INSERT entity_overrides(rule_type='never_merge')
    → trigger re-resolution on affected mention cluster
    → status='fixing'

  scope='entity_split':
    → split union-find cluster; create new canonical entity
    → re-aim relationships (L5) per old vs. new
    → INSERT entity_overrides(rule_type='split')
    → status='fixing'

  scope='schema_field' (undo promotion):
    → INSERT schema_field_overrides
    → revert field from typed to inferred (data preserved in emergent_fields)
    → status='verified' (no further fix needed)
    → IMPACT on existing artifacts:
        • Past chat turns that cited the field: citations stay (audit log
          is immutable); the citation card displays as "inferred field at
          time of citation" with the historical schema_version annotation
        • Past audit-log rows referencing the field: unchanged (immutable)
        • Re-running an old query: answer may differ because the field is
          now inferred, not typed — that's correct behavior, audit-logged
        • UI surfaces "X past chats reference this field — they continue
          to work; new queries will see it as inferred not typed"
          before confirming the undo

  scope='answer' or 'citation' (without specific extraction error):
    → re-run query with augmented context including correction
    → if better answer found, present it and ask "is this it?"
    → status='triaged'

  scope='doc_chain':
    → unlink the false chain member; audit-log
    → re-run detection on the doc; may create separate chain
    → status='fixing'

  scope='source_authority':
    → adjust files.source_authority with reason
    → status='verified'
   ↓
On status='verified' for any blocker/important correction:
   → INSERT regression_set row from (query, corrected_answer, implicated_docs)
   → this query runs on every deploy as part of CI
   → CI fails if a previously-fixed correction regresses
   ↓
Surface to user:
   → "Correction recorded. The system re-extracted contract_3.pdf and
      now finds the $50M cap on page 14. Updated answer above. ✓"
   → If complex: "Triaged; will be addressed in next review cycle."
```

### Streaming + feedback interaction (the timing details)

**Mid-stream feedback (user clicks 👎 while answer is still streaming):** cancel the in-flight generation immediately (set Procrastinate job state to `cancelled`); the partial output is shown with a "cancelled — recording your correction" indicator. Correction is then created; routing proceeds as usual. Avoids the awkward case where user clicks 👎 on something the model is about to retract anyway.

**Live update of past assistant messages after re-extraction:** when targeted re-extraction completes (~30–90s after correction), the chat-turn row is updated and an SSE `chat.message_update` event is fired. The chat UI patches the message in place (new text appended above the original with a "✓ updated based on your correction" indicator), and the right-pane citation card refreshes. The user sees their feedback take visible effect without leaving the chat.

### UI surfaces (feedback affordances everywhere)

Every place the system shows a system-produced fact gets a "wrong?" affordance:

| Surface | Affordance |
|---|---|
| Chat: assistant message | 👍 / 👎 with optional reason picker |
| Chat: citation card | "wrong source" + "this passage says something different" |
| Doc Detail: emergent field | inline edit pencil → correct_value + reason |
| Doc Detail: atomic unit | "wrong type" / "wrong parameter value" |
| Entity profile: alias list | "this isn't the same person" → split |
| Entity profile: relationship | "this relationship is wrong" |
| Schema › Typed: auto-promoted field | "undo promotion" (already in Design from earlier) |
| Schema › Inferred: field | "this isn't a real field" → blacklist |
| Conflict card: resolution | "the system picked wrong" → override authority |
| Doc chain timeline | "these aren't actually related" → unlink |

**Critical UX rule:** the correction dialog is *small*. Default option is a one-click reason chip. Typing is optional. The friction to file feedback must be **lower** than the friction to dismiss it; otherwise no one uses it.

### Admin surface — feedback queue

In the locked 10-surface IA, the feedback / corrections queue lives in the **Audit** page (`ui_design.md` §6.8), with the open-correction count surfaced on **Dashboard › Needs attention**. *(The pre-prototype IA placed this at `/explore › Feedback` — the mockup below dates from that version; the data model and routing are unchanged.)*

```
┌────────────────────────────────────────────────────────────────┐
│ 🔍 Explore › Feedback                              28 open · 4 │
│                                                       blockers │
├────────────────────────────────────────────────────────────────┤
│  Status [all▼]  Scope [all▼]  Severity [≥important▼]            │
│                                                                 │
│  ┌──────┬─────────────┬───────────────┬──────────┬───────────┐ │
│  │ Sev  │ Scope       │ Target         │ Status   │ Age       │ │
│  ├──────┼─────────────┼───────────────┼──────────┼───────────┤ │
│  │ ▲    │ extraction  │ contract_3.pdf│ fixing   │ 2m        │ │
│  │      │             │  p14 §indemn  │          │           │ │
│  │ ▲    │ entity_merge│ "M. Ambani"   │ open     │ 2h        │ │
│  │      │             │  ↔ Mukesh A.  │          │           │ │
│  │ ▽    │ citation    │ rerank q#a7c2 │ triaged  │ 3h        │ │
│  └──────┴─────────────┴───────────────┴──────────┴───────────┘ │
│                                                                 │
│  Click row → full correction + resolution log + re-run buttons │
└────────────────────────────────────────────────────────────────┘
```

### Learning signal (feedback isn't just a defect tracker)

Per-extractor failure-rate dashboard, derived from `corrections` aggregations:

- L3 clause typer: 12 corrections this month on `delivery_timing` mis-typings → flag for retraining
- L4 identity resolution: 8 corrections on Indian-name aliases → bias the LLM-judge prompt
- L2b emergent extraction: 3 corrections on `vendor_id` ambiguity → tighten the description prompt

These signals feed into:
- The next ingestion cycle's prompts
- The CUAD-style annotated regression set
- The "this extractor underperforms on subtype X" alerts in admin

### Failure modes

| Symptom | UX behavior |
|---|---|
| User says "wrong" without specifying | Probe with reason chips; if still vague, file as triage |
| Re-extraction also gets it wrong | After 2 retries, escalate to admin; mark correction `status='triaged'` |
| Correction conflicts with another user's correction | Both stored; admin reviews; both surface in conflict card |
| User abuses feedback (auto-rejecting everything) | Per-user rate limit; admin can mark user `low_signal` |
| Doc was deleted before correction processed | Correction stays valid for audit; resolution = `'source_unavailable'` |

### Eval coverage

The `regression_set` is itself a permanent CI surface. Beyond that, a "feedback responsiveness" stratum (5 questions in the full eval, not the demo 30):

| Question shape | Pass criterion |
|---|---|
| Inject a synthetic extraction error → user files correction | System re-extracts within 60s, answer updates correctly |
| User reports entity merge wrong | Override persists; later docs respect it |
| User undoes auto-promotion | Field reverts; data preserved; next ingestion respects override |
| Regression set passes | All previously-fixed corrections still resolved |

CI gate: regression set 100% pass on every deploy.

### Cost

- Correction intake: ~$0.0002 (severity classifier)
- Targeted re-extraction: ~$0.05/doc (Gemini Pro high-effort, on ~5% of corrections)
- Regression CI: ~$0.01 per regression-set query × ~50 queries × per-deploy = ~$0.50 per deploy
- Total at 100K-doc scale with ~500 corrections/month: ~$30/month. Trivial.

### References

- [Glean Knowledge Graph feedback patterns](https://www.glean.com/resources/guides/glean-knowledge-graph)
- [CustomGPT on document prioritization (user corrections as signal)](https://customgpt.ai/prioritize-documents-in-rag-retrieval-process/)
- [RAG anti-patterns 2026 (no eval feedback = top failure)](https://www.digitalapplied.com/blog/rag-anti-patterns-7-failure-modes-2026-engineering-guide)

---

## Design 5 — Citation across modalities

### Problem

Today the citation primitive is "(doc_id, page, bbox) on a PDF" and works beautifully for PDF spans. Every other modality is silent or improvised:

- **xlsx**: cite the cell? the row? what if the user re-sorts after we cite?
- **Image bbox** (scan, photo): how is it stored, rendered, click-through?
- **Handwritten OCR**: OCR text isn't faithfully the same as the source — dual citation needed.
- **Email body**: cite within thread; thread context matters.
- **RAPTOR summary**: the summary is LLM-generated, not in any source. What do we cite?
- **Aggregation answer**: there's no single span — the answer is a function over rows.
- **L3 atomic unit**: clauses, transactions, components, rows — each has its own native render.
- **Entity / chain references**: cite a *concept*, not a span.

This design defines a **universal citation envelope** plus per-type renderers. The data model unifies; the UI specializes; generation always picks the most precise type available.

### Universal envelope

Every citation is a row in `citations` (extend the existing table):

```sql
ALTER TABLE citations ADD COLUMN type TEXT NOT NULL DEFAULT 'pdf_span';
ALTER TABLE citations ADD COLUMN ref  JSONB NOT NULL;
ALTER TABLE citations ADD COLUMN label TEXT;
ALTER TABLE citations ADD COLUMN preview TEXT;
ALTER TABLE citations ADD COLUMN confidence NUMERIC(3,2);
-- confidence source per citation type:
--   pdf_span        : cross-encoder rerank score for the chunk containing the span
--   pdf_bbox        : same as pdf_span (rerank on the chunk that includes the bbox)
--   xlsx_row        : 1.0 (exact lookup, no inference)
--   xlsx_cell       : 1.0 (exact lookup)
--   image_bbox      : VLM classification confidence (~0.6–0.9 typical for ColPali)
--   ocr_span        : OCR_confidence × rerank_score (OCR conf is per-character avg)
--   email_message   : rerank score of the message chunk
--   raptor_summary  : geometric mean of leaf chunk rerank scores
--   aggregate       : 1.0 (SQL result is exact at the schema layer)
--   atomic_unit     : L3_extraction_confidence × rerank_score
--   entity_ref      : L4 identity-resolution cluster confidence
--   chain_ref       : doc-chain detection_confidence × member_confidence (average)
ALTER TABLE citations ADD COLUMN authority NUMERIC(3,2);
ALTER TABLE citations ADD COLUMN doc_status TEXT;
ALTER TABLE citations ADD COLUMN chain_id UUID;       -- if part of doc chain
ALTER TABLE citations ADD COLUMN modality TEXT;        -- 'text','image','table','synthetic'

-- valid types:
--   pdf_span, pdf_bbox, xlsx_row, xlsx_cell,
--   image_bbox, ocr_span, email_message,
--   raptor_summary, aggregate, atomic_unit,
--   entity_ref, chain_ref
```

`ref` is the type-specific locator:

```json
// pdf_span
{ "page": 7, "char_start": 1248, "char_end": 1392 }

// pdf_bbox (visual citation, e.g. for a clause located via layout)
{ "page": 7, "bbox": [120, 480, 540, 612] }

// xlsx_row
{ "sheet": "Q2 Vendors", "row_hash": "0x7af3c…", "row_index": 482,
  "key_cols": {"vendor_id": "VEN-001"} }
// row_hash makes citation stable if the user re-sorts the sheet

// xlsx_cell
{ "sheet": "Q2 Vendors", "row_hash": "0x7af3c…", "col": "total_spend" }

// image_bbox (photo or scan, no OCR)
{ "page": 1, "bbox": [220, 110, 480, 350], "caption": "concrete pour, March 15" }

// ocr_span (handwritten, scanned, low-confidence text)
{ "page": 2, "ocr_char_start": 84, "ocr_char_end": 142,
  "src_bbox": [120, 240, 480, 290], "ocr_conf": 0.71 }
// dual: text + image, renderer always shows both

// email_message
{ "thread_id": "...", "message_id": "...", "char_start": 0, "char_end": 384 }
// thread_id links to doc_chain

// raptor_summary
{ "node_id": "...", "level": 1, "leaf_chunk_ids": [...] }
// "summary of N source chunks, click to expand"

// aggregate
{ "audit_query_id": "...", "Q_plan_id": "...", "row_count": 5127,
  "csv_artifact_id": "..." }
// citation IS the audit artifact; downloadable

// atomic_unit
{ "unit_id": "...", "unit_type": "clause", "doc_id": "...",
  "page": 7, "bbox": [...] }
// renders as typed unit + drills to source

// entity_ref
{ "entity_id": "...", "alias_used": "M. Ambani" }
// not a source citation — a concept citation; opens entity profile

// chain_ref
{ "chain_id": "...", "highlight_members": [doc_id, ...] }
// renders as timeline with highlighted versions
```

### Generation behavior — pick the most precise type

When generation emits a citation, an ordered preference is applied:

```
For each fact in the generated answer:

  if fact derives from an L3 atomic unit:
    → cite as atomic_unit (drills to source automatically)

  elif fact derives from an xlsx row:
    → cite as xlsx_row (or xlsx_cell if a specific value)

  elif fact derives from an OCR'd handwritten doc:
    → cite as ocr_span (dual: text + image)

  elif fact derives from an email in a thread:
    → cite as email_message + attach chain_ref for context

  elif fact derives from a RAPTOR summary (no leaf chunk verified directly):
    → cite as raptor_summary; expose leaf chunks on click

  elif fact derives from an aggregation:
    → cite as aggregate (audit artifact + row list)

  elif fact is conceptual (about an entity, not a source span):
    → cite as entity_ref

  elif fact derives from a doc chain (e.g., "amended by …"):
    → cite as chain_ref with relevant members highlighted

  else:
    → cite as pdf_span (the default)
```

A single answer typically uses multiple citation types. *"The indemnity cap is $25M [¹ atomic_unit] in the current contract, amended in 2001 [² chain_ref], with total prior payouts of ₹4.2 cr [³ aggregate]"*.

### UI: one card per type, universal label + preview

The citation card on the right of `/chat` becomes a polymorphic component. All cards share:
- Title line with type icon
- Preview text (always present)
- Authority + status badges
- Chain badge if applicable
- "Open" button with type-appropriate action

Per-type renderer specifics:

**pdf_span / pdf_bbox card:**
```
[📄] enron_epe.pdf · Page 7, Section 12
─────────────────────────────────────
"…Indemnification cap of $25,000,000
 per occurrence shall not be exceeded…"
auth 0.90 · live · chain of 3
[Open PDF →]
```

**xlsx_row card:**
```
[📊] vendor_list.xlsx · Sheet: Q2 Vendors · Row 482
─────────────────────────────────────
vendor_id     │ total_spend │ category
─────────────┼────────────┼──────────
VEN-001      │ ₹612 cr    │ Logistics
auth 0.70 · live
[Open xlsx →]
```

**image_bbox card:**
```
[🖼️] site_photo_mar15.jpg · Page 1
┌─────────────────┐
│   [cropped image region with bbox]   │
└─────────────────┘
"concrete pour, March 15"
auth 0.30 · live
[Open image →]
```

**ocr_span card (dual):**
```
[✍️] aakash_note.jpg · Page 2 · OCR conf 0.71 ⚠
─────────────────────────────────────
OCR text:  "vendor failed to deliver
            concrete on Mar 15…"
┌─────────────────┐
│ [image region with bbox highlight]    │
└─────────────────┘
"verify against source image — OCR confidence is moderate"
auth 0.20 · live
[Open image →] [Open OCR text →]
```

**email_message card:**
```
[📧] thread "Mexico deal Q3" · message 7 of 12
From: a.fastow@enron.com → m.skilling@enron.com
2001-08-14 · Re: revised offer
─────────────────────────────────────
"…I'd suggest we route this through the
 SPE rather than book it on the balance…"
auth 0.50 · live · chain of 12
[Open thread →]
```

**raptor_summary card:**
```
[🧠] System-generated summary · Level 1 cluster
─────────────────────────────────────
"Power supply agreement (1999) between Enron
 Energy Services and El Paso Electric for
 10-year term. Includes $25M indemnity cap…"

Built from 4 source chunks:
  → enron_epe.pdf p7  (open)
  → enron_epe.pdf p12 (open)
  → enron_epe.pdf p15 (open)
  → enron_epe.pdf p18 (open)
This summary is synthesized — verify against the chunks above.
```

**aggregate card:**
```
[Σ] Aggregation result · query [audit#a7c2]
─────────────────────────────────────
Total: ₹4,213 cr across 5,127 invoices
filter: doc_type=invoice ∧ vertical=petrochem
        ∧ signed_date ∈ Q2 2025
computed at 2026-05-21 14:32:01 IST

[Download CSV (5,127 rows) →]  [View Q plan →]
3.2% of invoices missing `vertical` field — may
be undercounted. [Re-run after schema update]
```

**atomic_unit card:**
```
[⬡] Clause CL-9 · contract_xyz.pdf p7 §8.2
─────────────────────────────────────
Type: delivery_timing
Parameter: { hours: 4, scope: "rush_event" }
Rarity: 0.99 (top 1% of corpus) ⚠

"Vendor agrees to deliver supplies within
 four (4) hours of confirmed order…"
auth 0.90 · live
[Open PDF →]   [Why anomalous? →]
```

**entity_ref card:**
```
[👤] Mukesh Ambani · Person (P-088)
─────────────────────────────────────
Also: "M. Ambani", "Mukesh A.", "M.D. Ambani"
Cluster confidence: 0.97
Appears in 12 docs · 18 relationships
[Open entity profile →]
```

**chain_ref card:**
```
[⛓] Enron / El Paso power supply contract chain
─────────────────────────────────────
   1999  ── Original [¹]
   2000  ── Amendment 1
   2001  ── Amendment 2 (current) ★
   Side letter (2000)
2 highlighted members from this answer
[Open chain timeline →]
```

### Feedback integration (where Design 4 plugs in)

Every card has a small "wrong source" affordance in its corner. Click → feedback dialog pre-fills `scope='citation'` and `target` with the citation row's id. One click to flag a bad citation.

For `ocr_span` specifically, a "the text doesn't match the image" affordance is prominent because OCR errors are common — this becomes a major signal for OCR-quality improvement.

### Edge cases

| Case | Behavior |
|---|---|
| xlsx row deleted/edited after citation | Render with "this row may have changed since indexing"; show stored preview |
| OCR confidence < 0.5 | Force dual rendering: show image bbox first, OCR text as caption |
| RAPTOR node had a leaf chunk removed | Render with "(based on 3 of 4 original chunks; one source has been removed)" |
| Aggregate Q plan no longer valid (schema changed) | Show with "Plan valid at T; schema has since changed. [Re-run]" |
| Chain has a member retracted | Surface in chain timeline as struck-through |
| Citation to deleted doc | "Source no longer available" + preserved preview text + audit-log explanation |

### Storage and cost

- Citations table grows ~5–10× current chunk-citation count (more granular references).
- 100K-doc corpus: ~50M citation rows ≈ 4 GB. Postgres-fine.
- Render cost: per-card render is client-side; no extra LLM calls.
- Zero net latency cost for the user beyond the existing citation render.

### References

- [Anthropic Citations API (PDF spans baseline)](https://www.anthropic.com/news/introducing-citations-api)
- [Hebbia spreadsheet-shaped output for analysts](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign)
- Multi-modal citation in 2026 RAG: [Atlan platforms comparison](https://atlan.com/know/enterprise-rag-platforms-comparison/)
- [NotebookLM source grounding pattern](https://www.latent.space/p/notebooklm)

---

---

## Design 6 — Domain Vocabulary (closes Gap A from problem_review)

### Problem

The problem statement requires schema to support "the vocabulary used in the domain." Today we have L4 entity aliases (surface-form synonyms like "M. Ambani" ↔ "Mukesh Ambani") and HyDE/Contextual Retrieval to *work around* vocabulary mismatch at query time. We do not have an explicit, user-editable, audit-trail-carrying vocabulary layer for concept-level synonyms ("indemnification" ↔ "hold harmless"), acronym expansions ("GST" ↔ "Goods and Services Tax"), or domain term definitions.

### 2026 best-in-class

Palo Alto Networks documented the production pattern in 2026: **explicit synonym dictionaries + hybrid retrieval + LLM-aware query expansion**. Standard query-expansion technique combines synonyms, ontological concepts, and contextual terms ([Sahin Ahmed Medium](https://medium.com/@sahin.samia/query-expansion-in-enhancing-retrieval-augmented-generation-rag-d41153317383), [Palo Alto blog](https://live.paloaltonetworks.com/t5/engineering-blogs/bridging-the-language-gap-our-journey-to-a-synonym-aware-rag/ba-p/1236616)). The 2026 consensus: explicit table + embedding fallback.

### Data model

```sql
CREATE TABLE domain_vocabulary (
  id              UUID PRIMARY KEY,
  domain_id       UUID NOT NULL,
  canonical_term  TEXT NOT NULL,         -- "indemnification"
  synonyms        TEXT[] NOT NULL DEFAULT '{}',
                                         -- ["hold harmless", "save harmless"]
  acronym_of      TEXT,                  -- if this entry is an acronym
  expansion       TEXT,                  -- "Goods and Services Tax" for "GST"
  definition      TEXT,                  -- domain definition (user-editable)
  embedding       VECTOR(768),           -- for similarity-based lookup
  source          TEXT CHECK (source IN ('user_defined','discovered','imported'))
                  NOT NULL DEFAULT 'discovered',
  confidence      NUMERIC(3,2),          -- for discovered entries
  created_at      TIMESTAMPTZ DEFAULT now(),
  active          BOOLEAN DEFAULT true,
  UNIQUE(domain_id, canonical_term)
);

CREATE INDEX idx_vocab_synonyms ON domain_vocabulary USING gin(synonyms);
CREATE INDEX idx_vocab_embedding ON domain_vocabulary USING hnsw (embedding vector_cosine_ops);
```

### Pipeline integration

**Query pipeline (new step 2.5)** — between query rewriting and planner, vocabulary expansion lookup:
1. Tokenize the query (and rewrites from HyDE etc.)
2. For each token/phrase, check explicit synonyms in `domain_vocabulary`
3. Augment the BM25 query set (channel ①) with synonyms (deterministic, auditable)
4. Augment the embedding query set with synonym embeddings averaged in (soft expansion)
5. Resolve acronyms to expansions inline (BM25 sees both)
6. Plan inspector shows: *"Expanded 'GST' → 'Goods and Services Tax' [vocabulary entry v_421]; added synonyms for 'indemnification' [v_88]"*

**Ingestion pipeline (new step 12e)** — vocabulary discovery alongside L2b emergent fields:
- When L2b cross-doc clustering finds emergent fields whose names cluster with semantically similar meanings ("non_compete" + "non_competition_clause" + "restrictive_covenant"), surface as **candidate vocabulary entry** for the domain
- Threshold: name-embedding similarity ≥ 0.85, doc count ≥ 5, across same `doc_type_proposal`
- User accepts/rejects in `/schema › Vocabulary` tab (or auto-promotes at higher confidence)

### UI surface

New `/schema › Vocabulary` tab:

```
┌─────────────────────────────────────────────────────────────┐
│ 🛠 Schema  [Typed][Inferred][Collisions][▶Vocabulary]       │
├─────────────────────────────────────────────────────────────┤
│  Domain: legal_contracts                                     │
│                                                              │
│  ▼ Concept synonyms (47 entries)              [+ add]        │
│    indemnification        ↔  hold harmless, save harmless   │
│    non-compete            ↔  non-competition, restrictive   │
│                              covenant                        │
│    force majeure          ↔  act of god, superseding event  │
│    [edit] [merge] [delete]                                   │
│                                                              │
│  ▼ Acronym expansions (12 entries)            [+ add]        │
│    NDA   →  Non-Disclosure Agreement                         │
│    MAE   →  Material Adverse Effect                          │
│    SLA   →  Service Level Agreement                          │
│                                                              │
│  💡 Discovered (pending review, 6)                          │
│    "termination_clause" / "term_and_termination" /          │
│     "exit_provisions" — same concept across 14 docs         │
│     [Accept as vocabulary] [Ignore]                          │
└─────────────────────────────────────────────────────────────┘
```

### Failure modes & cost

| Symptom | Behavior |
|---|---|
| User adds incorrect synonym | Reversible; audit-logged in `corrections` (Design 4) |
| Auto-discovered candidate is wrong | User can reject; rejection persists |
| Two domains have conflicting vocabularies for same word | Vocabulary is `domain_id`-scoped — different domains, different entries |
| Embedding fallback returns false synonym | Confidence threshold gates use; lower-conf entries are suggestions only |

Cost: vocabulary lookup is local (~1ms per query). Discovery clustering reuses L2b infrastructure — incremental cost negligible.

### References
- Palo Alto Networks synonym-aware RAG: [paloaltonetworks.com blog](https://live.paloaltonetworks.com/t5/engineering-blogs/bridging-the-language-gap-our-journey-to-a-synonym-aware-rag/ba-p/1236616)
- Query expansion techniques: [medium.com/@sahin.samia](https://medium.com/@sahin.samia/query-expansion-in-enhancing-retrieval-augmented-generation-rag-d41153317383)
- Milvus synonym expansion overview: [milvus.io](https://milvus.io/ai-quick-reference/how-does-synonym-expansion-work)

---

## Design 7 — Hierarchical Containment + Instance Lineage (closes Gaps B + C)

### Problem

The problem statement requires schema to support hierarchical/containment relationships ("a File contains Cases, a Case contains Notes") **and** every extracted thing to know its "parent / container chain" (lineage). Today we have generic L5 typed edges with free-text predicates — works at the data-model level but provides no first-class containment semantics, no cascade, no native ancestor/descendant queries, and no lineage chain on extracted entities beyond their immediate `doc_id`.

**Observation:** Gap B (schema-level hierarchies) and Gap C (instance-level lineage) are coupled — both solved by Postgres ltree.

### 2026 best-in-class

- **Postgres ltree** is the right tool. Built-in hierarchical type with `@>` (ancestor), `<@` (descendant), `~` (regex), `||` (concat). Automatic cascade on subtree move ([PG 18 docs F.22](https://www.postgresql.org/docs/current/ltree.html), [Pinnacle modeling guide](https://pinnsg.com/modeling-hierarchical-data-postgres/)).
- **Ontology pattern**: explicit `containment_kind` distinguishes contains/part-of/associative/attribute. Multi-level hierarchies modeled through cascading class relationships ([LARK Infolab April 2026](https://www.larkinfolab.nl/2026/04/23/how-do-ontologies-handle-hierarchical-relationships-in-graph-data/)).
- **OntoKG (arxiv 2604.02618)**: schema-from-the-outset pattern; explicit ontology aids LLM-guided extraction.
- **OpenLineage standard** for lineage tracking ([Atlan 2026 guide](https://atlan.com/know/data-lineage-tracking/)).

### Data model

```sql
-- Schema-level: declare entity-type hierarchies
ALTER TABLE schema_relationships ADD COLUMN kind TEXT NOT NULL DEFAULT 'associative'
  CHECK (kind IN ('contains','part_of','references','associates','attribute_link'));
ALTER TABLE schema_relationships ADD COLUMN cardinality TEXT
  CHECK (cardinality IN ('one_to_one','one_to_many','many_to_many'));
ALTER TABLE schema_relationships ADD COLUMN cascade_delete BOOLEAN DEFAULT false;
ALTER TABLE schema_relationships ADD COLUMN single_parent BOOLEAN DEFAULT true;
  -- For 'contains' relations, default single_parent=true (tree); set false for DAG

-- Instance-level: lineage_path is the materialized ancestry
ALTER TABLE extracted_entities ADD COLUMN lineage_path ltree;
ALTER TABLE extracted_entities ADD COLUMN parent_entity_id UUID
  REFERENCES extracted_entities(id);

CREATE INDEX idx_entities_lineage_gist ON extracted_entities USING gist(lineage_path);
CREATE INDEX idx_entities_lineage_btree ON extracted_entities USING btree(lineage_path);
CREATE INDEX idx_entities_parent ON extracted_entities(parent_entity_id);

-- Citation provenance includes lineage snapshot
ALTER TABLE citations ADD COLUMN lineage_path_at_cite_time ltree;
```

### How it works in practice

**Schema-level declaration** (in a domain YAML):
```yaml
entities:
  - { name: File, description: "Top-level case file" }
  - { name: Case, description: "Individual legal case within a file" }
  - { name: Note, description: "Working note attached to a case" }
relationships:
  - { name: file_contains_case, kind: contains, from: File, to: Case,
      cardinality: one_to_many, cascade_delete: true, single_parent: true }
  - { name: case_contains_note, kind: contains, from: Case, to: Note,
      cardinality: one_to_many, cascade_delete: true, single_parent: true }
```

**Instance-level path** (set at extraction time):
```
file_123                      -- top-level
file_123.case_456             -- a Case inside file_123
file_123.case_456.note_789    -- a Note inside that Case
```

### Query helpers

```sql
-- All descendants of a File
SELECT * FROM extracted_entities WHERE lineage_path <@ 'file_123';

-- All ancestors of a Note
SELECT * FROM extracted_entities WHERE lineage_path @> 'file_123.case_456.note_789';

-- All siblings of a Case
SELECT * FROM extracted_entities
  WHERE lineage_path ~ 'file_123.*{1}' AND entity_type = 'Case';
```

Exposed as helper functions in the API:
```
GET /entities/{id}/descendants
GET /entities/{id}/ancestors
GET /entities/{id}/siblings
GET /entities/{id}/breadcrumb     -- formatted lineage
```

### Pipeline integration

**Indexing step 16.5** (new — after schema-driven extraction in §5 step 18):
- For each extracted entity, determine `parent_entity_id` from the schema's `contains` relationships
- Compute `lineage_path = parent.lineage_path || entity.id`
- Store both on the entity row

**Maintenance hook**: if an entity is re-parented (rare — usually via correction in Design 4), the entity AND all descendants get their `lineage_path` recomputed in one transaction. ltree's path semantics make this a single update.

### UI surface

- **Schema Studio** renders entity-type hierarchy as a tree:
  ```
  ▶ File (contains)
    ▶ Case (contains)
      ▶ Note
      ▶ Decision
    ▶ Vendor (associates)
  ```
- **Doc Detail panel** shows the lineage breadcrumb:
  ```
  Lineage:  workspace › project_alpha › client_xyz › case_2023 › contract_47 › clause_12 (this)
  ```
- **Citation cards** show breadcrumb on hover.
- **Explore › Entities** has a "Containment" view that walks the tree.

### Generation behavior

Generation receives lineage in citation annotations. Answers can reference the chain explicitly: *"Clause 12 of Contract 47 (Case 2023, Client XYZ) sets the cap at $25M [¹] — the parent contract was last amended in 2001."*

### Failure modes

| Symptom | Behavior |
|---|---|
| Parent entity not yet extracted (out-of-order ingest) | `lineage_path` set to NULL initially; backfilled when parent lands; Procrastinate queue handles dep ordering |
| Re-parent operation cascades to many descendants | Single ltree update; O(descendants_count) but fast |
| Schema declares cycle (e.g., A contains B, B contains A) | Validation refuses; surfaced in Schema › Collisions |
| Entity has multiple parents (DAG case) | **Important: lineage_path is for STRUCTURAL containment, not entity relationships.** A clause is contained in exactly one contract (single-parent ✓). A Person is *related to* many contracts via L5 edges, but is not *contained in* a contract — they're a separate L4 entity. So most "multi-parent" intuitions are actually relationship-graph cases, handled by L5, not by lineage_path. True DAG containment (rare: e.g., a paragraph referenced from two contracts) → `single_parent=false` allows; lineage_path becomes ambiguous → use closure table fallback (Wave C). |

### References
- Postgres ltree docs: [postgresql.org docs F.22](https://www.postgresql.org/docs/current/ltree.html)
- Modeling hierarchical data in Postgres: [pinnsg.com](https://pinnsg.com/modeling-hierarchical-data-postgres/)
- Ontology hierarchies in graph data (LARK April 2026): [larkinfolab.nl](https://www.larkinfolab.nl/2026/04/23/how-do-ontologies-handle-hierarchical-relationships-in-graph-data/)
- OntoKG (schema-from-the-outset): [arxiv 2604.02618](https://arxiv.org/pdf/2604.02618)
- Data lineage 2026 guide: [atlan.com](https://atlan.com/know/data-lineage-tracking/)
- Lineage vs. provenance: [snowflake.com](https://www.snowflake.com/en/fundamentals/data-lineage/lineage-vs-provenance/)

---

## Design 8 — Conversational Context for Follow-ups (closes Gap D)

### Problem

The problem statement requires "conversational context for follow-up questions within a session." Today walkthrough §4 step 1 says *"Session ID looked up; chat history loaded for follow-up context"* — that is a one-line mention, not a design. We do not have an explicit mechanism for:
- **Anaphora resolution** ("his", "that contract", "the same vendor")
- **Entity carry-forward** (turn N established Mr. Sharma; turn N+1 "his loans" inherits)
- **Topic / filter carry-forward** (turn N was about Q2 2025; turn N+1 "what about Q3" inherits date range)
- **Prior result-set refinement** (turn N returned 5 results; turn N+1 "just the ones in petrochem" filters prior set)

### 2026 best-in-class

- **MTRAG benchmark** ([MIT TACL](https://direct.mit.edu/tacl/article-pdf/doi/10.1162/TACL.a.19/2540217/tacl.a.19.pdf)): performance "saturates after 4–6 user turns; assistant turns retained for coreference"
- **AILS-NTUA SemEval-2026 Task 8** ([arxiv 2603.10524](https://arxiv.org/html/2603.10524)): "pronoun coreference and implicit topic carryover are dominant phenomena"
- **SELF-multi-RAG**: summarized conversational context drives improved retrieval
- **Context-aware query rewriting + semantic caching** ([HF community](https://discuss.huggingface.co/t/multi-turn-rag-for-technical-documentation-using-context-aware-query-rewriting-semantic-caching-is-this-a-sound-approach/172433))
- **Learn-to-Retrieve in conversational QA** ([arxiv 2409.15515](https://arxiv.org/pdf/2409.15515))
- **GraphRAG with entity-relationship tracking for multi-turn** ([arxiv 2506.19385](https://arxiv.org/html/2506.19385v2))

### Data model

```sql
CREATE TABLE chat_sessions (
  id              UUID PRIMARY KEY,
  user_id         UUID,
  workspace_id    UUID NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now(),
  last_active_at  TIMESTAMPTZ DEFAULT now(),
  -- Carry-forward context (updated per turn)
  carry_forward_entities  UUID[] DEFAULT '{}',
                           -- entity IDs active from prior turns
  carry_forward_filters   JSONB DEFAULT '{}',
                           -- {date_range, doc_type, vertical, ...}
  prior_result_set_id     UUID
                           -- last turn's result set (for refinement queries)
);

CREATE TABLE chat_turns (
  id              UUID PRIMARY KEY,
  session_id      UUID NOT NULL REFERENCES chat_sessions ON DELETE CASCADE,
  turn_index      INT NOT NULL,
  user_query      TEXT NOT NULL,
  resolved_query  TEXT,                  -- after anaphora resolution
  context_used    JSONB,                 -- snapshot of ChatContext for audit
  answer          TEXT,
  citations       JSONB,
  result_set_id   UUID,                  -- for prior_result_set_id reference
  created_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE(session_id, turn_index)
);
```

### ChatContext object — the structured carry-forward

```typescript
interface ChatContext {
  session_id: UUID;
  last_turn_id: UUID | null;

  // Tier 3 — structured carry-forward (unbounded; never expires)
  carry_forward_entities: UUID[];     // every L4 entity ever active in session
  carry_forward_filters: {            // structured filters from prior turns
    date_range?: { from: string, to: string };
    doc_type?: string[];
    vertical?: string;
    [k: string]: unknown;
  };
  prior_result_set_id: UUID | null;   // for "filter the previous results"

  // Tier 2 — Mem0-style rolling summary of older turns (unbounded session)
  older_turn_summary: string;         // rolling-compressed; updated on threshold

  // Tier 1 — hot turns for the retrieval-side anaphora resolver
  // K=6 is configurable (per MTRAG: retrieval saturates here, not generation)
  last_k_verbatim_turns: Array<{
    turn_index: number;
    user_query: string;
    answer_summary: string;
    entities_introduced: UUID[];
    role: 'user' | 'assistant';
  }>;
}
```

**Generation prompt assembly** at any turn N: concatenate `older_turn_summary` (Tier 2) + `last_k_verbatim_turns` (Tier 1) + `carry_forward_*` structured state (Tier 3) + current query. Subject to LLM context window — at Gemini Flash 1M tokens this never realistically saturates for KB sessions.

**Retrieval-side rewriter input** at turn N: ONLY `last_k_verbatim_turns` + `carry_forward_*`. Past 6 turns is MTRAG-confirmed noise for retrieval purposes.

### Query pipeline integration (new step 0.5)

Between query intake and intent classifier:

```
0.5 ChatContext Resolution (Gemini Flash, ~$0.0003, ~200ms):
   - Load chat_session.carry_forward_*
   - Load last 4 turns (user + assistant summaries)
   - Prompt: "Resolve anaphora in CURRENT query using PRIOR context.
              Update carry-forward state based on this turn.
              Output JSON:
                { resolved_query, anaphora_resolved: [{from, to}],
                  new_entities, new_filters, refinement_of_prior: bool }"
   - Update chat_session row with new carry-forward state
   - resolved_query passed to step 1 (intent classifier) and downstream
```

### Generation integration

When generation produces an answer:
- Any new entities mentioned in the answer → added to `carry_forward_entities` (for next turn's coreference)
- Any filters used in this turn's plan → carried into `carry_forward_filters`
- The result set → stored as `result_set_id` for "filter the previous results" patterns

### UI surface (plan inspector)

```
┌─────────────────────────────────────────────────────────────┐
│ ▼ How I answered                                            │
├─────────────────────────────────────────────────────────────┤
│  Conversational context (turn 4 of 4 used)                  │
│   Inherited entities:                                       │
│     • Mr. Sharma (P-541) — from turn 2                      │
│   Anaphora resolved:                                        │
│     "his" → Mr. Sharma                                      │
│     "the loans" → from prior result set [rs_87]             │
│   Filters carried:                                          │
│     date_range = Q2-2025 (from turn 1)                      │
│                                                             │
│  Resolved query:                                            │
│    "What are Mr. Sharma's loans in Q2 2025 from prior       │
│     result set rs_87?"                                      │
│                                                             │
│  [original]: "What about his loans?"                        │
└─────────────────────────────────────────────────────────────┘
```

This **renders the context resolution explicitly**, so the user sees what got carried forward — full transparency in line with our existing plan-inspector pattern.

### Three-tier memory (not a turn cap)

**Important clarification:** the conversation itself is *not* capped at 6 turns. ChatGPT and Claude keep the full conversation history in the generation context (1M / 200K context windows make this cheap), and we do the same. MTRAG's "saturates after 4–6 turns" finding is about a *narrower* thing: adding more than 4–6 turns of context to the **retrieval-side query rewriter** gives diminishing returns. The cap belongs on the rewriter input, not the conversation memory.

Three tiers, each with a different scope:

| Tier | What | Used by | Cap |
|---|---|---|---|
| **Tier 1 — Hot turns** | Last K=6 *verbatim* turns | Retrieval-side anaphora resolver (the MTRAG finding applies here) | K=6 (configurable) |
| **Tier 2 — Summarized older turns** | Rolling-window compressed: Mem0-style ([arxiv 2504.19413](https://arxiv.org/pdf/2504.19413)) — extract salient facts, distill to compact memory | Generation prompt context (kept in the LLM's window alongside Tier 1) | unbounded; one Flash summarization call when window grows past threshold |
| **Tier 3 — Structured carry-forward** | `ChatContext { carry_forward_entities[], carry_forward_filters{}, prior_result_set_id }` | Planner directly | unbounded; never expires until session does |

**All turns stored unbounded in `chat_turns` for audit + replay.** Generation gets all three tiers. The effective memory is **unbounded for entities/filters/results** (via Tier 3) and **log-compressed for prose** (via Tier 2). Concretely at turn 50 in a long session:

- Anaphora resolver sees turns 44–49 verbatim (Tier 1) + Tier 3 carry-forward state with every entity ever mentioned still active
- Generation LLM sees a ~200-token summary of turns 1–43 (Tier 2) + turns 44–50 verbatim (Tier 1) + Tier 3 carry-forward
- Cost stays bounded: Flash resolver always processes ~6 turns of text regardless of session length
- Nothing is deleted

This matches what ChatGPT/Claude actually do at the conversation level, while applying MTRAG's retrieval-efficiency finding where it actually applies.

### Failure modes

| Symptom | Behavior |
|---|---|
| Anaphora resolves wrong ("his" attached to wrong person) | User-facing display lets user see; one-click "this is wrong" feedback (Design 4) creates correction; next turn re-resolves |
| Multiple candidate entities for "him/her/it" | Surface as disambiguation: *"By 'his' do you mean Mr. Sharma or Mr. Khanna?"* |
| Prior result set has expired/deleted | Drop refinement; resolve as standalone query; note in plan inspector |
| Topic shift detected (new entity, no prior reference) | Reset carry-forward; start fresh context for the new topic |
| Session > 6 turns | Older turns summarized; carry-forward remains structured |

### Cost & latency

- **Per-turn ChatContext resolution** (anaphora + carry-forward update on Tier 1): 1 Gemini Flash call, ~$0.0003, ~200ms — only on follow-up queries, not first turn
- **Rolling Tier-2 summarization**: 1 Gemini Flash call when older-turns text exceeds threshold (default: every ~10 turns past K=6), ~$0.001 each — amortized to ~$0.0001/turn
- **Generation prompt size grows with session length** but log-compressed via Tier 2 — at turn 50, total context ~5K tokens of history vs. 50K if naive; cost stays bounded
- **Net overhead vs. single-turn baseline**: ~3% latency / ~3% cost on follow-up queries — same as before, but now genuinely unbounded session memory rather than 6-turn cap

### References
- MTRAG benchmark: [MIT TACL paper](https://direct.mit.edu/tacl/article-pdf/doi/10.1162/TACL.a.19/2540217/tacl.a.19.pdf)
- AILS-NTUA SemEval-2026 Task 8: [arxiv 2603.10524](https://arxiv.org/html/2603.10524)
- Learn-to-Retrieve in conversational QA: [arxiv 2409.15515](https://arxiv.org/pdf/2409.15515)
- Conversational Intent-Driven GraphRAG: [arxiv 2506.19385](https://arxiv.org/html/2506.19385v2)
- Context-aware query rewriting + semantic caching: [HF community discussion](https://discuss.huggingface.co/t/multi-turn-rag-for-technical-documentation-using-context-aware-query-rewriting-semantic-caching-is-this-a-sound-approach/172433)

---

## Design 9 — Layered Configuration (closes Gap E)

### Problem

The problem statement requires: *"Pipeline behavior (model choices, thresholds, limits, prompts) must be configurable without code changes. Configuration should be layerable: sensible defaults, with overrides at finer-grained scopes."*

Today we have a `config/` folder mentioned in the planned layout and an adapter table in architecture.md §8. We have not specified:
- The layering scheme (what scopes exist; resolution order)
- Where each config value lives (YAML at boot vs. DB at runtime)
- How effective config is surfaced to the user
- What's tunable vs. baked-in code

### 2026 best-in-class

- **Hydra + OmegaConf** (Meta open source, widely adopted in ML production) — hierarchical YAML composition with CLI/programmatic override ([Hydra docs](https://hydra.cc/docs/intro/))
- **OmegaConf** as the primitive: hierarchical config tree, merging, struct validation via dataclasses ([decoding-ai](https://www.decodingai.com/p/mastering-ml-configurations-by-leveraging))
- **Killer pattern**: base + variation overrides, layered like Lego ([Medium 10 patterns](https://medium.com/@ThinkingLoop/10-hydra-yaml-config-patterns-that-keep-you-sane-04eed3d1c28f))

### Layering scheme

Six layers, resolved most-specific → most-general:

```
1. Per-user override               (rare; for admins to debug)
2. Per-doc override                (admin sets a specific doc's params)
3. Per-doc-type override           (workspace setting per doc-type)
4. Workspace runtime override      (set via UI/API at workspace level)
5. Domain YAML                     (config/domains/<domain>.yaml)
6. Global defaults                 (config/defaults.yaml)
```

Resolution: first match wins, walking from layer 1 to layer 6.

### Data model

```sql
CREATE TABLE config_overrides (
  id              UUID PRIMARY KEY,
  scope_kind      TEXT NOT NULL CHECK (scope_kind IN
                    ('user','doc','doc_type','workspace')),
  scope_id        TEXT NOT NULL,
                    -- user_id / doc_id / doc_type_name / workspace_id
  config_key      TEXT NOT NULL,
                    -- dot-notation: "extraction.l3.clause.rare_threshold"
  config_value    JSONB NOT NULL,
  reason          TEXT,
  set_by          UUID,
  set_at          TIMESTAMPTZ DEFAULT now(),
  active          BOOLEAN DEFAULT true,
  UNIQUE(scope_kind, scope_id, config_key)
);

CREATE INDEX idx_config_lookup ON config_overrides(scope_kind, scope_id, config_key)
  WHERE active = true;
```

### File layout

```
config/
├── defaults.yaml          ← global defaults (Hydra base)
├── domains/
│   ├── legal_contracts.yaml
│   ├── corporate_email.yaml
│   ├── financial_filings.yaml
│   └── mixed_demo.yaml    ← CUAD+Enron+SEC for the demo
├── doc_types/
│   ├── contract.yaml
│   ├── bank_statement.yaml
│   ├── email.yaml
│   ├── drawing.yaml
│   ├── land_record.yaml
│   ├── id_xlsx.yaml
│   └── handwritten_note.yaml
└── prompts/
    ├── extraction.yaml
    ├── planner.yaml
    ├── generation.yaml
    ├── conflict_detector.yaml
    └── chat_context.yaml
```

### Example: `config/defaults.yaml`

```yaml
extraction:
  l2:
    type_list: [PERSON, ORG, MONEY, DATE, LOCATION, EVENT, ACTIVITY,
                PROJECT, FACILITY, REGULATION, PRODUCT, CONCEPT]
    confidence_threshold: 0.6
  l2b:
    auto_promotion:
      prevalence_threshold: 0.80
      stability_threshold: 0.90
      value_type_confidence: 0.90
      min_doc_count: 20
  l3:
    rarity_threshold: 0.95
    classifier_confidence_min: 0.7

retrieval:
  rerank:
    top_k: 50
    timeout_ms: 1000
    fallback: mxbai-rerank-large-v2
  ircot:
    max_hops: 2
    cost_ceiling_usd: 0.05

models:
  extraction_llm: gemini-2.5-flash
  hard_query_llm: gemini-2.5-pro
  embedder: gemini-embedding-001
  reranker: cohere-rerank-3.5
  faithfulness: hhem-2.1

doc_chains:
  detection:
    title_similarity_threshold: 0.70
    sender_recipient_overlap: 0.50

source_authority:
  defaults:
    audited_filing: 1.00
    executed_contract: 0.90
    approved_policy: 0.80
    lab_report: 0.80
    meeting_minutes: 0.70
    internal_memo: 0.60
    email: 0.50
    forwarded_email: 0.30
    draft: 0.30
    handwritten_note: 0.20
```

### Example: `config/domains/legal_contracts.yaml`

```yaml
defaults: defaults.yaml          # inherit + override

extraction:
  l3:
    enabled_unit_types: [clause]
    clause_taxonomy: cuad_41_types

vocabulary:
  preload: vocabularies/legal.yaml

doc_chains:
  detection:
    amendment_phrases: ["amends", "amendment", "side letter", "supersedes"]
```

### Resolution API

```python
def resolve_config(
    key: str,                          # "extraction.l3.rarity_threshold"
    workspace_id: UUID,
    domain: str | None = None,
    doc_type: str | None = None,
    doc_id: UUID | None = None,
    user_id: UUID | None = None,
) -> Any:
    # 1. Check config_overrides at most-specific scope first
    for scope_kind, scope_id in [
        ('user', user_id),
        ('doc', doc_id),
        ('doc_type', doc_type),
        ('workspace', workspace_id),
    ]:
        if scope_id is None: continue
        v = db.fetch_override(scope_kind, scope_id, key)
        if v is not None: return v
    # 2. Check domain YAML
    if domain:
        v = hydra.get(f"domains/{domain}", key)
        if v is not None: return v
    # 3. Fall back to global defaults
    return hydra.get("defaults", key)
```

### UI surface

**Settings › Auto-discovery › Effective Config** (per the locked 10-surface IA · `ui_design.md` §6.10; was originally designed as a Schema-page sub-view in the pre-prototype IA):

```
┌─────────────────────────────────────────────────────────────┐
│ ⚙ Effective Configuration · domain=legal_contracts          │
├─────────────────────────────────────────────────────────────┤
│  Filter: [all keys] [extraction] [retrieval] [models] [...] │
│                                                              │
│  extraction.l2b.auto_promotion.prevalence_threshold = 0.80  │
│    layer: defaults                                          │
│                                                              │
│  extraction.l3.rarity_threshold = 0.95                      │
│    layer: defaults  [Override at workspace ▼]               │
│                                                              │
│  extraction.l3.clause_taxonomy = "cuad_41_types"            │
│    layer: domain (legal_contracts)                          │
│                                                              │
│  retrieval.rerank.top_k = 75                                │
│    layer: workspace_override (set by admin@org 2026-05-21)  │
│    [view default: 50]  [revert]                             │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

Every value shows **which layer produced it** + a one-click override + a revert. Audit log captures every override change.

### What's tunable vs. baked-in

**Tunable** (in config):
- Model choices (LLM, embedder, reranker, faithfulness)
- All numerical thresholds (auto-promotion, rarity, IRCoT cap, rerank top-K, etc.)
- All prompts (extraction, planner, generation)
- Doc-type registry (which extractors run for which doc types)
- Vocabulary defaults
- Source-authority scale
- Doc-chain detection heuristics

**Baked-in** (code-level, not configurable):
- The pipeline ordering itself (parse → chunk → extract → resolve)
- The retrieval-channel set (the 10 channels)
- The planner grammar
- The conflict-resolution rule precedence (chain → status → authority → recency)

### Failure modes

| Symptom | Behavior |
|---|---|
| Bad config value (e.g., threshold = -1) | Validated at boot via OmegaConf struct-config |
| Conflict between layers | Most-specific wins by design; surfaced in inspector with full layer chain |
| Override breaks the system | Revert button in UI; audit log preserves history |
| Config file missing | Boot fails fast with clear error pointing at missing file |

### References
- Hydra docs: [hydra.cc](https://hydra.cc/docs/intro/)
- OmegaConf + Hydra mastery guide: [decodingai.com](https://www.decodingai.com/p/mastering-ml-configurations-by-leveraging)
- 10 Hydra/YAML patterns: [Medium ThinkingLoop](https://medium.com/@ThinkingLoop/10-hydra-yaml-config-patterns-that-keep-you-sane-04eed3d1c28f)
- Hydra configuration system tutorial: [Imperial College ReCoDE](https://imperialcollegelondon.github.io/ReCoDE-DeepLearning-Best-Practices/learning/Learning_about_hydra/)

---

## Remaining tier-1 gaps for the next batch

These are the three items I left out of this design pass. Real but smaller in design surface. Next batch on confirmation:

| Gap | Why deferred from this batch | Next-batch design surface |
|---|---|---|
| #5 Temporal validity | Half-solved by Designs 3+2 (chains + recency in conflict resolution); remaining is bi-temporal AS-OF queries on facts | `valid_from/valid_to` on relationships + extracted_entities; AS-OF query support; AS-AT-INGEST vs. AS-AT-TRUE distinction |
| #8 Schema ops beyond add | Each op (rename/split/merge/delete) has its own design with cascade semantics | One section per op + cascade through audit log, regression set, chat history, saved queries |
| Meta M1–M4 | Decisions, not designs | Demo environment choice, pitch narrative, demo choreography, eval pass bar |

---

## Summary of what this design pass closes

Cross-reference into `red_team.md`:

- **F1 (aggregation gap)** — RESOLVED by Design 1.
- **F2 (Boolean/set gap)** — RESOLVED by Design 1's set_op semantics.
- **F5 (negative-query overconfidence)** — RESOLVED by Design 2's conflict-detection-with-semantic-fallback + Design 4's user-correction loop.
- **F6 (versioning)** — RESOLVED by Design 3's doc-chains.
- **#9 in §9 of red_team (aggregate citation semantics)** — RESOLVED by Design 1 + Design 5 (aggregate as universal citation type with audit artifact).
- **The "two docs disagree" hand-wave in architecture.md §6 step 8** — RESOLVED by Design 2.
- **The Enron thread gap** in scenarios — RESOLVED by Design 3.
- **"What happens when the answer is wrong?"** (the most-asked production question) — RESOLVED by Design 4.
- **Citation outside PDFs (xlsx, OCR, image, RAPTOR, aggregate, atomic-unit, entity, chain)** — RESOLVED by Design 5's universal envelope + per-type renderers.
- **NotebookLM/Glean-style feedback gaps cited in industry reviews** — RESOLVED by Design 4 + 5 reinforcing each other.
- **"Vocabulary used in the domain" requirement (Gap A)** — RESOLVED by Design 6 (domain_vocabulary table + query expansion at step 2.5 + L2b discovery clustering).
- **Hierarchical/containment + parent/container-chain requirements (Gaps B + C)** — RESOLVED by Design 7 (schema_relationships.kind + extracted_entities.lineage_path ltree).
- **Conversational context for follow-ups (Gap D)** — RESOLVED by Design 8 (ChatContext object + LLM anaphora resolver + three-tier memory: Tier 1 hot K=6 verbatim turns for the retrieval-side rewriter per MTRAG, Tier 2 rolling Mem0-style summary of older turns, Tier 3 unbounded structured carry-forward state. Conversation itself is unbounded — same as ChatGPT/Claude).
- **Layered configuration requirement (Gap E)** — RESOLVED by Design 9 (Hydra/OmegaConf YAML for boot + config_overrides DB for runtime; 6-layer resolution with effective-config inspector).

**Every explicit PDF requirement is now addressed by a designed solution.** Remaining items (temporal AS-OF, schema-ops beyond add, meta M1–M4) are out of scope of the assignment or polish — none changes core capability.
