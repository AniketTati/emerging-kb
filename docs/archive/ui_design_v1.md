# UI Design — What the User Sees and Does

**Audience:** the engineer building the front-end, and the reviewer who wants to know what the demo will look like.
**Approach:** mockups of every page the user will touch, with the underlying data made visible.

---

## The mental picture: a "smart workbook"

Imagine a giant Microsoft Excel workbook that the system **fills in automatically** by reading the uploaded documents.

- Each **sheet** = one doc type's structured data (Contracts, Transactions, Components, Residents…)
- Each **row** = one atomic unit (a clause, a transaction, a component, a resident)
- Two special sheets:
  - **People & Orgs** — the canonical directory (the entity layer)
  - **Relationships** — who↔what links across all docs

Users interact with the workbook through chat (asking questions) and the explore pages (browsing, filtering, correcting).

---

## Information architecture

```
/                       Home — dashboard
/upload                 Drag-drop + live ingestion
/chat                   Q&A with citations + plan inspector
/explore                Tabs: Docs · Doc Types · Atomic Units · Entities ·
                              Relationships · Topics · Anomalies
/schema                 View, edit, version, promote-discovered
/audit (admin)          Per-query logs for review
/swagger                API documentation
```

Below: each page as a mockup.

---

## Page A — Home / Workspace dashboard

```
┌────────────────────────────────────────────────────────────────┐
│ 🏠 Home                                          [+ New Upload] │
├────────────────────────────────────────────────────────────────┤
│  Your Knowledge Base                                            │
│  87 documents · 12 doc types (4 typed + 8 inferred)             │
│  1,247 atomic units · 312 canonical entities · 489 relationships│
│                                                                 │
│  ┌─────────────┐ ┌──────────────┐ ┌─────────────┐               │
│  │ Documents   │ │ Atomic Units │ │ Entities    │               │
│  │ 87          │ │ 1,247        │ │ 312         │               │
│  │ 24 contracts│ │ 412 clauses  │ │ 218 people  │               │
│  │ 12 bnk stmt │ │ 387 txns     │ │ 64 orgs     │               │
│  │ 18 residnts │ │ 224 rows     │ │ 14 events   │               │
│  │ …           │ │ …            │ │ …           │               │
│  └─────────────┘ └──────────────┘ └─────────────┘               │
│                                                                 │
│  ⚡ What the system just learned   (live stream)                │
│   • 14:32  auto-promoted 4 fields → CardiacCathReport schema    │
│            stent_type, placement_artery, complications,         │
│            primary_surgeon                                       │
│   • 14:30  new doc-type proposed: cardiac_catheterization_report│
│            (5 docs, 7 fields tracking)                          │
│   • 14:28  field `contrast_volume_ml` crossed 60% prevalence    │
│            (approaching auto-promotion threshold)               │
│   • 14:25  entity merge: "Dr. Mehta" ↔ "Dr. R. Mehta" (0.94)    │
│                                                                 │
│  ⚠ Top anomalies                                                │
│   • 4-hr delivery clause       contract_xyz.pdf   rarity 0.99   │
│   • ₹3L outflow, unknown party hdfc_jan.pdf       rarity 0.93   │
│                                                                 │
│  💬 [Ask a question...]                                         │
└────────────────────────────────────────────────────────────────┘
```

The **"what the system just learned"** stream is the live visibility into emergent schema. Every auto-promotion, every new doc-type proposal, every prevalence-threshold crossing, every entity merge — all appear here as they happen.

---

## Page B — Upload with live ingestion status

```
┌────────────────────────────────────────────────────────────────┐
│ 📤 Upload                                                       │
├────────────────────────────────────────────────────────────────┤
│   ┌──────────────────────────────────────────────────┐         │
│   │   📁 Drop files / folders / ZIPs here             │         │
│   └──────────────────────────────────────────────────┘         │
│                                                                 │
│   Live ingestion (last 10)                                      │
│   ┌─────────────────┬──────────┬──────────────────┬───────┐    │
│   │ File            │ Type     │ Stage            │ Time  │    │
│   ├─────────────────┼──────────┼──────────────────┼───────┤    │
│   │ offer_priya.pdf │ contract │ ● parsing        │ 4s    │    │
│   │ hdfc_jan.pdf    │ bank st. │ ● contextualizing│ 12s   │    │
│   │ aakash_note.jpg │ note     │ ● extracting     │ 18s   │    │
│   │ plant_x.pdf     │ drawing  │ ✓ ready          │ 56s   │    │
│   │ aurangabad.xlsx │ id sheet │ ● indexing 3/8k  │ 2m    │    │
│   │ parcel_1234.pdf │ land rec │ ✗ failed_ocr ⓘ   │ 22s   │    │
│   └─────────────────┴──────────┴──────────────────┴───────┘    │
│                                                                 │
│   Click any row → detail panel:                                 │
│     • per-stage timestamps                                      │
│     • errors with explanation                                   │
│     • entities found so far                                     │
│     • OCR confidence                                            │
│     • [Re-run failed stage]                                     │
└────────────────────────────────────────────────────────────────┘
```

Updates flow over **Server-Sent Events (SSE)** — no polling, no protocol upgrade, auto-reconnect via the browser EventSource API. Server→client one-way streaming is all we need; WebSocket would have been overkill.

---

## Page C — Chat with right-side citation cards + plan inspector

```
┌────────────────────────────────────────────────────────────────┐
│ 💬 Chat                                                         │
├──────────────────────────────────────┬─────────────────────────┤
│                                      │  📎 Citations            │
│ 👤 What's the non-compete in         │                          │
│    Priya's offer?                    │  [¹]                     │
│                                      │  ┌──────────────────┐    │
│ 🤖 Priya's offer letter includes a   │  │ offer_priya.pdf  │    │
│    non-compete clause of 12 months,  │  │ Page 9, §7.2     │    │
│    geographically scoped to India¹.  │  │ ──────────────── │    │
│                                      │  │ "Employee agrees │    │
│    This is typical for the corpus —  │  │ not to engage in │    │
│    rarity 0.30, well within the      │  │ any competing    │    │
│    common range of 6–18 months².     │  │ business for 12  │    │
│                                      │  │ (twelve) months  │    │
│ ▼ How I answered (click to expand)   │  │ within India..." │    │
│                                      │  │                  │    │
│  Plan JSON: { modes: [E, C], … }     │  │ [view in PDF]    │    │
│  Channels: ⑤ ⑦ ⑧                     │  └──────────────────┘    │
│  Top-3 candidates with scores        │                          │
│  Reranker chose CL-1 (0.97)          │  [²] Corpus stats        │
│  Faithfulness gate: HHEM 1.00 ✓      │  ┌──────────────────┐    │
│                                      │  │ Non-compete      │    │
│                                      │  │ clauses corpus:  │    │
│ [Ask a follow-up...]                 │  │ mean=14mo,       │    │
│                                      │  │ p10=6mo, p90=24mo│    │
│                                      │  └──────────────────┘    │
└──────────────────────────────────────┴─────────────────────────┘
```

Clicking citation `[¹]` jumps the right-pane preview straight to the highlighted bbox on the PDF.

---

## Page D — Explore: Atomic Units browser

```
┌────────────────────────────────────────────────────────────────┐
│ 🔍 Explore  [Docs│Doc Types│▶ Atomic Units│Entities│Rel│Topics│ │
├────────────────────────────────────────────────────────────────┤
│  Tabs:  ▶ Clauses (412) │ Transactions (387) │ Components (74) │
│         │ Rows: Resident (5237) │ Events (14) │ …               │
│                                                                 │
│  Filter:  Type [all ▼]   Rarity [▶ all] [0.8+] [0.95+]          │
│           Doc [any ▼]    Param [+ add]                          │
│                                                                 │
│  ┌────┬──────────────┬──────────────┬─────────────┬───────┐    │
│  │ Id │ Type         │ From doc     │ Parameters  │Rarity │    │
│  ├────┼──────────────┼──────────────┼─────────────┼───────┤    │
│  │CL-1│ Non-compete  │ offer_priya  │ 12mo, India │ 0.30  │    │
│  │CL-7│ Indemnity    │ contract_xyz │ $25M event  │ 0.91⚠ │    │
│  │CL-9│ Delivery     │ contract_xyz │ 4 hours     │ 0.99⚠ │    │
│  └────┴──────────────┴──────────────┴─────────────┴───────┘    │
│                                                                 │
│  Row click → expand panel:                                      │
│   • Full clause text                                            │
│   • PDF preview with bbox highlight                             │
│   • "Why anomalous?" chart vs. corpus distribution              │
│   • Related entities + relationships                            │
│   • [Ask the system about this] → chat-with-context             │
│                                                                 │
│  Bulk:  [Export CSV]   [Mark as reviewed]                       │
└────────────────────────────────────────────────────────────────┘
```

The Atomic-Units tab is the "smart sheets" view literally rendered.

---

## Page E — Explore: Entity profile

```
┌────────────────────────────────────────────────────────────────┐
│ 🔍 Explore › Entities › Aakash Sharma                           │
├────────────────────────────────────────────────────────────────┤
│  👤 Aakash Sharma   (Person · P-88)                             │
│                                                                 │
│  Also called:  "A. Sharma",  "Aakash"                           │
│  Cluster confidence: 0.97 (HIGH)   [✓ Confirm] [✗ Split]        │
│                                                                 │
│  📑 Appears in 6 documents:                                     │
│    • aakash_note.jpg               [author]                     │
│    • hdfc_acc1234_jan_mar.pdf      [account holder]             │
│    • aakash_priya_wedding.jpg      [partner]                    │
│    • acme_directory.xlsx           [row 482]                    │
│    • board_minutes_q1.pdf          [attendee, p2]               │
│    • email_vendor_review.pdf       [sender]                     │
│                                                                 │
│  🔗 Relationships:                                              │
│    • partner_in_event → Wedding E-14                            │
│    • holds_account → HDFC ****1234                              │
│    • has_action_item → "review vendor invoices"                 │
│    • attended → Board meeting Q1 2024                           │
│    • employed_by → Acme Corp                                    │
│                                                                 │
│  📊 Activity timeline                                           │
│    Jan 2024 ─ Q1 board meeting attended                         │
│    Feb 2024 ─ 12 transactions on account                        │
│    Mar 2024 ─ handwritten note re: vendor review                │
│    Dec 2024 ─ wedding event                                     │
│                                                                 │
│  [Ask anything about this person]                               │
└────────────────────────────────────────────────────────────────┘
```

---

## Page F — Schema: Typed · Inferred · Collisions

The Schema page has three tabs reflecting the three states of every field: **Typed** (auto-promoted, first-class), **Inferred** (the system is still gathering evidence), **Collisions** (naming conflicts the system can't resolve alone).

### F.1 — Tab: Typed schema

```
┌────────────────────────────────────────────────────────────────┐
│ 🛠 Schema   [▶ Typed]  [Inferred]  [Collisions]    v3 (current) │
├────────────────────────────────────────────────────────────────┤
│  Doc-types and entity types currently typed                     │
│                                                                 │
│  ▶ Contract (412 instances)                          [Edit]     │
│  ├ Fields:                                                      │
│  │   parties             [list of Person/Org]                   │
│  │   term_years          [number]   "duration in years"         │
│  │   indemnity_cap       [money]    "max indemnity amount"      │
│  │   governing_law       [text]                                 │
│  │   signed_date         [date]    ✨ auto 2026-05-21 @ doc 38  │
│  └ Atomic unit: Clause   (41 CUAD types, per-type params)       │
│                                                                 │
│  ▶ CardiacCathReport (47 instances)        ✨ auto @ doc 20     │
│  ├ Fields:                                                      │
│  │   procedure_date       [date]            ✨ auto @ doc 20    │
│  │   stent_type           [enum: 3 values]  ✨ auto @ doc 20    │
│  │   placement_artery     [enum: 5 values]  ✨ auto @ doc 20    │
│  │   complications        [text]            ✨ auto @ doc 20    │
│  │   primary_surgeon      [text]            ✨ auto @ doc 20    │
│  │   patient_id           [text]            ✨ auto @ doc 20    │
│  │   contrast_volume_ml   [number]          ✨ auto @ doc 31    │
│  │   stent_brand          [text]            ✨ auto @ doc 28    │
│  └ [Edit] [Undo last promotion] [Add field]                     │
│                                                                 │
│  ▶ BankStatement (12) …                                         │
│  ▶ Resident (5,237) …                                           │
│                                                                 │
│  📜 Schema history                                              │
│  • v3 (today)  Added Contract.bonus_band (manual)               │
│  • v2 (3d ago) Auto-promoted CardiacCathReport (8 fields)       │
│  • v1 (initial)                                                 │
│  [view diff] [rollback]                                         │
│                                                                 │
│  [+ Add type manually]  [+ Add field]  [+ Import schema YAML]   │
└────────────────────────────────────────────────────────────────┘
```

Click `[Edit]` on a typed field → impact preview *"will re-extract from 47 docs, ~$2, ~2 min"* before committing. Click `[Undo last promotion]` → field reverts to inferred state; existing data preserved (just unlabeled).

### F.2 — Tab: Inferred schema (the growing thing)

```
┌────────────────────────────────────────────────────────────────┐
│ 🛠 Schema   [Typed]  [▶ Inferred]  [Collisions]                 │
├────────────────────────────────────────────────────────────────┤
│  Doc-types proposed by the system, not yet auto-promoted        │
│                                                                 │
│  ▾ cardiac_catheterization_report          47 docs · stable     │
│    All 8 fields auto-promoted ✓  ·  2 fields still inferred:    │
│    ┌──────────────────────┬──────┬───────┬────────────────────┐ │
│    │ Field                │ Prev │ Stab  │ Status             │ │
│    ├──────────────────────┼──────┼───────┼────────────────────┤ │
│    │ fluoroscopy_time_min │ 72%  │ 0.87  │ close to threshold │ │
│    │ post_procedure_meds  │ 78%  │ 0.81  │ emerged at doc 30  │ │
│    └──────────────────────┴──────┴───────┴────────────────────┘ │
│                                                                 │
│  ▾ vendor_purchase_order                   12 docs · forming    │
│    No fields auto-promoted yet (need ≥20 docs of type)          │
│    ┌──────────────────────┬──────┬───────┬────────────────────┐ │
│    │ Field                │ Prev │ Stab  │ Status             │ │
│    ├──────────────────────┼──────┼───────┼────────────────────┤ │
│    │ vendor_name          │ 100% │ 0.94  │ promotable @ n=20  │ │
│    │ po_number            │ 100% │ 0.96  │ promotable @ n=20  │ │
│    │ line_items           │ 100% │ 0.92  │ list, per-line     │ │
│    │ delivery_date        │  92% │ 0.85  │ tracking           │ │
│    │ tax_gst              │  75% │ 0.74  │ tracking           │ │
│    └──────────────────────┴──────┴───────┴────────────────────┘ │
│                                                                 │
│  ▾ board_meeting_minutes                   3 docs · early       │
│    Too few docs for confident inference — keep ingesting.       │
│    8 fields proposed, all single-doc evidence.                  │
│                                                                 │
│  [show low-confidence emergent fields drawer ▼]                 │
└────────────────────────────────────────────────────────────────┘
```

Click any field → see the value distribution, sample values, which docs contributed, and the "why isn't this auto-promoted yet" reason.

### F.3 — Tab: Collisions

```
┌────────────────────────────────────────────────────────────────┐
│ 🛠 Schema   [Typed]  [Inferred]  [▶ Collisions]      2 items    │
├────────────────────────────────────────────────────────────────┤
│  Naming or type ambiguities — the system needs you to pick.     │
│  (You don't confirm auto-promotions; you only resolve genuine   │
│   conflicts the system can't decide alone.)                     │
│                                                                 │
│  ⚠ Naming collision                                             │
│    Inferred `vendor_id` (vendor_purchase_order, 12 docs)        │
│      values: "VEN-001", "VEN-002", "VEN-AB-77", ...             │
│    vs.                                                          │
│    Typed `vendor_id` (Contract, 412 docs)                       │
│      values: GSTIN-format ("29ABCDE1234F1Z5", ...)               │
│                                                                 │
│    Different value shapes — same name. Pick one:                │
│    [ Rename PO's to `po_vendor_code` ]                          │
│    [ Rename Contract's to `vendor_gstin` ]                      │
│    [ Mark both as "same concept, multiple formats" ]            │
│    [ Ignore — keep typed; PO field stays inferred ]             │
│                                                                 │
│  ⚠ Ambiguous value type                                         │
│    `severity` (incident_report, 19 docs)                        │
│    15 distinct values across 19 docs                            │
│    Likely enum?   Or free text?                                 │
│    [ Treat as enum ]   [ Treat as text ]   [ See sample values ]│
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

These are the *only* places in the schema flow where you click to confirm. Everything else is automatic.

---

## Page F.5 — Doc Detail (drill-down from anywhere)

Click any document reference — from `/upload`, `/explore`, citation cards, entity profiles, anywhere — and a Doc Detail panel slides in showing *everything* the system has extracted from that one doc.

```
┌────────────────────────────────────────────────────────────────┐
│ 📄 cardiac_001.pdf                          [×] close [⤢] full │
├────────────────────────────────────────────────────────────────┤
│  File:      cardiac_001.pdf · 3 pages · 1.2 MB                  │
│  Ingested:  2026-05-21 14:32:11   ✓ ready                       │
│                                                                 │
│  ▾ DOC-TYPE PROPOSAL                                            │
│      cardiac_catheterization_report     confidence 0.91         │
│      Inferred from: this doc + 46 similar in corpus             │
│      Schema status: TYPED ✨ (auto-promoted, 8 fields)          │
│                                                                 │
│  ▾ L2b EMERGENT FIELDS (this doc)                               │
│      procedure_date     "2024-03-15"          [date]      ✓     │
│      stent_type         "drug-eluting"        [enum]      ✓     │
│      stent_brand        "Boston Sci Promus"   [text]      ✓     │
│      placement_artery   "LAD"                 [enum]      ✓     │
│      complications      "none"                [text]      ✓     │
│      primary_surgeon    "Dr. Mehta"           [text]      ✓     │
│      patient_id         "AH-001234"           [text]      ✓     │
│      contrast_volume_ml 80                    [number]    ✓     │
│      ✓ = field promoted to typed schema                         │
│                                                                 │
│  ▾ L2 MENTIONS (universal types · 5)                            │
│      Dr. Mehta              PERSON   p1                          │
│      Boston Scientific      ORG      p2                          │
│      LAD                    CONCEPT  p1                          │
│      AH-001234              CONCEPT  p1                          │
│      2024-03-15             DATE     p1                          │
│                                                                 │
│  ▾ L3 ATOMIC UNITS                                              │
│      (none — doc-type has no atomic-unit extractor registered)  │
│                                                                 │
│  ▾ L4 ENTITIES RESOLVED (3)                                     │
│      Dr. Mehta           → P-088 (existing, 6 docs)             │
│      Boston Scientific   → O-217 (new this doc)                 │
│      Patient AH-001234   → P-541 (new this doc)                 │
│                                                                 │
│  ▾ L5 RELATIONSHIPS (2)                                         │
│      P-088 (Dr. Mehta) ─performed─→ P-541 (Patient)             │
│      P-541 ─received_implant─→ O-217 (Boston Scientific)        │
│                                                                 │
│  ▾ L1d RAPTOR DOC CARD                                          │
│      "Cardiac catheterization report from 2024-03-15…"          │
│                                                                 │
│  ▾ PDF preview pane                                             │
│      [page 1 ▾]    [highlight: stent_type | procedure_date | …] │
│                                                                 │
│  [Ask the system about this doc]   [Reprocess]   [Delete]       │
└────────────────────────────────────────────────────────────────┘
```

This panel is the single most important visibility surface for the "schema emerges from data" claim — it makes the system's understanding of any individual doc *fully inspectable* at any time.

---



```
┌────────────────────────────────────────────────────────────────┐
│ ⚠ Anomalies                                                     │
├────────────────────────────────────────────────────────────────┤
│  Top-rarity items across all atomic-unit sheets                 │
│                                                                 │
│  Clauses (8 flagged)                                            │
│    - 4-hour delivery clause   contract_xyz   0.99 ⚠⚠⚠           │
│    - $25M indemnity cap       enron_epe      0.91 ⚠             │
│    - 5-year non-compete       offer_abc      0.88               │
│                                                                 │
│  Transactions (12 flagged)                                      │
│    - ₹3L to unresolved cpty   hdfc_jan       0.93 ⚠             │
│    - ₹0.50 transfer           icici_feb      0.88               │
│                                                                 │
│  Residents (3 flagged)                                          │
│    - DOB year 1880            aurangabad     0.97 ⚠             │
│    - Duplicate ration card    aurangabad     0.99 ⚠⚠            │
│                                                                 │
│  Click any → "Why is this anomalous?" chart vs corpus           │
└────────────────────────────────────────────────────────────────┘
```

---

## Where you intervene vs. what the system does

| System auto-does | What you see | When you intervene |
|---|---|---|
| Parse, OCR, contextualize | live ingest table | only if a doc fails |
| Propose doc-type label | Doc Detail + Doc Types tab | only to override label |
| Extract emergent fields (L2b) | Doc Detail + Schema › Inferred | never — viewing only |
| Cluster fields across docs | Schema › Inferred prevalence updates live | never |
| **Auto-promote** to typed schema | Home Learning feed + Schema › Typed badge | only to undo if wrong |
| Detect naming collision | Schema › Collisions tab | resolve: rename / merge / ignore |
| Build RAPTOR summaries | doc cards in Doc Detail | view only |
| Extract atomic units | Explore › Atomic Units | view; mark "needs review" |
| Compute rarity scores | Anomalies dashboard | filter; mark reviewed |
| Resolve entities (high conf) | Entity profile auto-updates | only on low-conf merges |
| Discover relationships | Entity profile graph | view only |
| Generate briefings & mind maps | Read; refresh | view only |
| Answer queries | Chat + plan inspector | ask; inspect; flag wrong |
| Audit-log every query | admin view of logs | review |

You explicitly drive: **uploads, asking questions, resolving the rare schema collision, and editing typed schema after the fact.** Everything else is automated with visible transparency.

**You never click "approve" on an auto-promotion. You never click "yes, add this field." If the system is confident enough by its own thresholds, it acts; you watch and override if you disagree.**

---

## What the demo flow looks like end to end

1. **Open Home — empty workspace.** 0 docs, 0 doc-types, 0 fields. Schema page is blank.
2. **Drop the first 5 docs.** Live ingest table shows `parsing → … → ready`. Within seconds, Home Learning feed fires:
   *"new doc-type proposed: power_supply_agreement (5 docs, 11 fields tracking)"*.
   `/schema › Inferred` already shows the new type with all 11 fields and their per-field prevalence.
3. **Click one doc → Doc Detail panel slides in.** Show the proposed doc-type, all emergent fields with values, the L2 mentions, the resolved entities, the RAPTOR doc card. *"Everything the system knows about this one doc."*
4. **Drop 15 more docs of varied subtypes.** Watch the Learning feed:
   - prevalence numbers tick upward
   - field `term_years` crosses 80% → auto-promotion notification fires live
   - `/schema › Typed` gains a CommercialContract type with a ✨ badge
5. **Show Explore tabs:** Doc Types (now 4 inferred + 1 typed), Atomic Units (412 clauses across all contracts), Entities (312 canonical with merge-confidence indicators).
6. **Open `/chat` — ask questions on the actual demo corpus:**
   - **Retrieval:** *"What's the indemnity cap in our license agreements?"* → cited multi-doc answer with per-contract caps + rarity inline.
   - **Aggregation (Q-mode):** *"Total aggregate indemnification cap across all our contracts."* → planner emits Q-mode JSON visible in inspector; templated answer: *"Across N indemnification clauses, total aggregate cap is $X. Top 5 by cap value: …"*; clickable audit-artifact citation with downloadable CSV of all contributing rows.
   - **Multi-hop:** *"Which contracts share the same arbitration venue as the Enron/EPE agreement?"* → L4 entity + L5 relationship walk.
   - **Conflict resolution:** *"What's the indemnity cap in the Enron / El Paso power supply contract?"* → conflict card surfaces original ($25M, 1999) vs amendment ($50M, 2001) with chain-aware resolution.
   - **Negative / refusal:** *"What was our Q4 2026 revenue?"* → graceful refusal with semantic near-misses listed ("no evidence in corpus, searched X / Y / Z").
7. **Open `/schema › Inferred`** — show the still-emerging fields (`fluoroscopy_time_min` at 72%, *"close to threshold"*). Drop 3 more docs → watch one promote live.
8. **Schema swap demo:** switch from legal_contracts schema to corporate_email schema on the *same uploaded data*. The Atomic Units sheet repopulates with email-typed units in seconds. No re-parse.
9. **Open `/explore > Anomalies`** — show the 4-hour delivery clause flagged at rarity 0.99. Click → "why anomalous?" chart vs corpus.
10. **Open `/audit`** — single query's full trace (plan JSON, channels, candidates, scores, judges, decision) — proving the system is auditable end to end.

That's the live demo, ~10 minutes. The "watch the schema grow" moment in step 4 — auto-promotion firing on screen with no human click — lands as hard as the schema-swap moment in step 8.
