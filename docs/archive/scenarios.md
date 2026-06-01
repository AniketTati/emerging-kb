# Enterprise Scenarios — Stress-Testing the Architecture

**Purpose:** for each of 8 real-world companies/organisations with very different document profiles, list what documents they accumulate, who the users are, what queries they actually ask, and honestly grade whether our architecture handles them. This is the *real* test of domain-agnosticism.

Verdict legend: **✓** handles well · **⚠** partial / caveats · **✗** outside MVP scope (Wave C or beyond)

---

## 1. Reliance Industries — multi-vertical conglomerate

**Business:** Retail (Reliance Retail, AJIO, Trends, Jewels), telecom (Jio), oil & gas (Jamnagar refinery — world's largest), petrochemicals, power & green energy, media (Network18), financial services. 500K+ employees.

**Document profile (annual order of magnitude):**

| Type | Volume | Format |
|---|---|---|
| Vendor contracts (cross-vertical) | ~50K active | PDF (digital + scanned) |
| B2B customer invoices | ~10M/year | PDF, sometimes EDI |
| Internal emails | ~100M/year | EML |
| HR/employment records | 500K employees, multi-doc each | PDF, xlsx |
| Refinery CAD/P&ID drawings | ~100K | PDF + DWG |
| Lab quality reports (refinery batches) | millions/year | PDF + structured |
| SEBI/RBI/regulatory filings | thousands/quarter | PDF |
| Joint-venture & M&A agreements | hundreds | PDF (digital) |
| Land records (property, mines) | tens of thousands | PDF (scanned, multilingual) |
| Patents / IP filings | thousands | PDF |
| Internal memos, board papers | thousands/year | PDF, docx |
| Press releases, annual reports | hundreds/year | PDF |
| Maintenance logs (plants) | continuous | xlsx, structured |
| Retail vendor PO/invoices | ~5M/year | PDF |

**Users & sample queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| Mukesh Ambani (Chairman) | "Summarize Q4 performance across all verticals with key risks." | global synthesis | ⚠ — RAPTOR top + L7 LazyGraphRAG would shine, partially Wave C |
| CFO | "Total vendor spend across petrochem in Q2 2025." | computation | ✓ via L3 invoices + SQL aggregation |
| Head of Retail | "Find vendor agreements expiring next 90 days where renegotiation is overdue." | retrieval + filter | ✓ L3 Contract fields + date filter |
| Jamnagar Plant Manager | "Maintenance schedule for Unit 3 cracker?" | scoped retrieval | ✓ scoped + L2 mentions |
| HR Head | "Find offer letters with non-compete > 18 months." | clause filter | ✓ L3 Clause filter |
| Legal Head | "All contracts with arbitration in Singapore?" | clause filter | ✓ L3 Clause filter |
| Compliance Officer | "Any pending regulatory replies overdue?" | scoped + temporal | ⚠ needs deadline-tracking entity |
| Sales (Jio) | "Customer churn patterns in Maharashtra rural last quarter." | analytics | ✗ requires data-warehouse joining, not in MVP |
| Refinery Quality Head | "Flag unusual lab readings in Jamnagar Q1." | anomaly | ✓ L3 LabReading + rarity |
| Board member | "Have we ever signed exclusivity with any Adani entity?" | negative | ✓ L4 entity filter; system refuses if no evidence |
| Cross-vertical | "Shared vendors between Retail & Jio with spend > 10cr." | multi-hop | ✓ HippoRAG PPR |
| Engineering | "Where did we have a delay caused by a single supplier issue last year?" | vague needle | ✓ atomic-unit + anomaly + RAPTOR |

**Where we shine:** doc-type-agnostic L3 plug-ins handle contracts AND lab reports AND maintenance logs AND drawings (with ColPali in Wave C). Identity resolution across verticals (same vendor in Retail and Petrochem) is exactly what L4 does. Anomaly scoring on lab readings comes free.

**Where we struggle:**
- Real-time churn analytics needs a data warehouse, not a KB.
- 100M+ emails → vector storage needs to graduate to Turbopuffer (cited in writeup, not built).
- Multi-tenant isolation per vertical (one Retail user shouldn't see Petrochem) → permissions are Wave C.
- CAD/DWG native (P&IDs) — we extract via ColPali in Wave C; native CAD geometry queries are out of scope.

---

## 2. D-Mart (Avenue Supermarts) — discount retail chain

**Business:** ~370 stores across India, no e-commerce focus, cash-and-carry pricing.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| Vendor purchase orders | ~100K/year | PDF, EDI |
| Vendor invoices | ~500K/year | PDF |
| Sale receipts (POS) | ~500M/year | structured |
| Daily inventory snapshots | 370 stores × daily | xlsx, structured |
| Lease agreements (per store) | ~370 | PDF (mix scanned/digital) |
| Employee records | ~50K | PDF, xlsx |
| GST filings | monthly × state | structured + PDF |
| Major supplier contracts | ~500 | PDF |
| Stockout reports | weekly × store | xlsx |
| Audit / safety inspection reports | quarterly × store | PDF |
| Marketing receipts | thousands | PDF |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| R.K. Damani (Founder) | "How are we doing this month vs last across categories?" | global summary | ⚠ (RAPTOR + L7) |
| CFO | "GST exposure for Karnataka Q3?" | computation | ✓ L3 fields + SQL |
| Andheri Store Manager | "Top 20 stockouts last week at my store." | scoped + ranked | ✓ scoped filter |
| Vendor Manager | "Vendors who missed delivery > 3 times this year." | analytics | ✓ via L3 Delivery records + group-by |
| Audit | "Flag invoices with mismatched line items." | anomaly | ✓ L3 anomaly + line-item check |
| Procurement | "Best price history for Tata salt." | temporal series | ✓ L3 LineItem filter + temporal |
| Operations | "Where did we have a hygiene issue this year?" | vague | ✓ RAPTOR + HyDE + anomaly |

**Where we shine:** invoices are perfect for L3 atomic-unit extraction (line items). Vendor consolidation is exact identity-resolution use case. Anomaly detection on invoices (duplicate, mismatched, unusual price) maps directly to per-line-item rarity.

**Where we struggle:**
- 500M POS receipts/year is not a knowledge base workload — that's OLAP. We could index aggregates but not transactions.
- Real-time stock alerts need streaming, not batch.

---

## 3. Tata Steel — manufacturing + mining

**Business:** Steel manufacturing at Jamshedpur, Kalinganagar; iron ore + coking coal mines. ~80K employees.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| CAD/engineering drawings | ~100K | PDF, DWG |
| Maintenance logs per machine | continuous | xlsx |
| Safety / near-miss incidents | thousands/year | PDF + form |
| Mining lease agreements | hundreds | PDF (scanned) |
| Environmental clearances | hundreds | PDF |
| Pollution / emissions reports | quarterly per plant | PDF + structured |
| Lab quality reports per batch | millions/year | structured + PDF |
| Customer contracts (Maruti, L&T, …) | hundreds | PDF |
| Supplier contracts (coal, ore) | hundreds | PDF |
| Employee records | ~80K | PDF, xlsx |
| Union/wage agreements | dozens (vintage scans) | PDF (scanned) |
| Patents / R&D reports | thousands | PDF |
| Land records (mines + plants) | thousands | PDF (scanned, multilingual) |
| Logistics docs (rakes, ports) | continuous | PDF, EDI |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| CEO T.V. Narendran | "Top operational risks this quarter." | global synthesis | ⚠ (L7) |
| Jamshedpur Plant Head | "Downtime patterns of blast furnace 3 last quarter." | scoped + anomaly | ✓ L3 MaintenanceLog + per-unit rarity |
| Mining Ops | "Drilling reports Jharia coal field this month." | scoped temporal | ✓ doc filter + L1 |
| Safety Officer | "All near-miss incidents Q1 root-cause = vendor part." | multi-hop | ✓ L3 IncidentReport + L5 caused_by edge |
| Customer Head | "Maruti contract due for renewal, key clauses to renegotiate." | scoped + clause | ✓ L3 Clause + entity |
| HR | "Find skilled welders for Kalinganagar Phase 2." | entity + skill match | ⚠ needs structured skills index |
| Compliance | "Pending environmental clearances by deadline." | temporal + status | ⚠ needs deadline-state tracking |
| Production | "Cooling tower design for plant 4." | CAD retrieval | ⚠ Wave C (ColPali) |
| QA | "Lab readings outside spec for batch Q4-2024." | anomaly | ✓ L3 LabReading + rarity |

**Where we shine:** safety incidents, lab readings, maintenance logs are all atomic-unit gold. Identity resolution across decades of supplier name changes (Indian Iron and Steel → IISCO → Tata Steel Long Products) is exactly the alias problem L4 solves.

**Where we struggle:**
- DWG/CAD native files — we'd convert to PDF + ColPali, but geometry-aware queries ("show me all bolted joints rated for 50 tonne") need a CAD-specific tool.
- Real-time SCADA data → not a KB workload.
- Land records in regional languages with old handwriting → Mistral OCR helps but not perfect.

---

## 4. Apollo Hospitals — healthcare chain

**Business:** 70+ hospitals India + clinics, pharmacies. 12K+ doctors.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| EMR / patient records | millions | structured + PDF |
| Lab/pathology reports | tens of millions/year | PDF + structured |
| Radiology / imaging reports | millions/year | PDF + DICOM |
| Doctor prescriptions | millions/year | handwritten scans + structured |
| Surgical reports | hundreds of thousands/year | PDF + structured |
| Hospital billing / claims | millions/year | PDF + EDI |
| Insurance claim docs | millions/year | PDF |
| Doctor employment contracts | tens of thousands | PDF |
| Equipment service contracts | thousands | PDF |
| Drug supplier contracts | hundreds | PDF |
| Government scheme docs (PMJAY etc.) | continuous | PDF |
| Clinical trial protocols + data | hundreds | PDF + structured |
| Patient consent forms | millions | PDF (often scanned) |
| NABH / JCI compliance reports | dozens/year | PDF |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| Chairman Dr. Prathap Reddy | "Operational summary chains-wide last quarter." | global | ⚠ (L7) |
| Chennai Hospital CMO | "ICU bed utilization + patient flow last 30 days." | computation | ✓ if EMR fields extracted |
| Lab head | "Abnormal pathology pattern last month?" | anomaly + temporal | ✓ L3 LabReading + rarity |
| Treating Doctor | "Mr. Sharma's complete history." | patient-scoped multi-doc | ✓ L4 Patient entity + scoped retrieval |
| Billing head | "Claim rejection patterns by insurance company." | analytics + group-by | ✓ L3 Claim + group-by |
| HR | "Doctor contracts expiring next 60 days." | temporal | ✓ |
| Compliance | "NABH gap analysis." | global comparison | ⚠ needs reference standard |
| Drug Safety | "Patients on drug A who got prescription for drug B (known interaction)." | multi-hop, privacy-sensitive | ⚠ requires permissions (Wave C) |
| Audit | "Doctors prescribing outside formulary > 10% of cases." | analytics + anomaly | ✓ |

**Where we shine:** patient-as-entity (L4) clusters all records for one person across visits, labs, prescriptions. Lab anomalies = L3 atomic-unit rarity. Handwritten prescriptions = our OCR + VLM pipeline.

**Where we struggle:**
- **Patient privacy is HARD** — row/field-level permissions are Wave C. Apollo would not deploy MVP without them.
- DICOM images need a medical imaging system; we index metadata only.
- Real-time clinical decision support is not KB; that's a CDSS.

---

## 5. HDFC Bank — banking

**Business:** Retail banking, corporate banking, treasury. ~120K employees.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| KYC documents (per customer) | tens of millions × multiple docs | PDF + image |
| Loan agreements | tens of millions | PDF |
| Loan disbursement memos | millions/year | PDF |
| Account statements | hundreds of millions/year | PDF + structured |
| Cheque images | billions historic | image |
| ATM/branch transaction logs | continuous | structured |
| Treasury / risk reports | thousands/year | PDF + xlsx |
| RBI compliance filings | continuous (monthly + ad-hoc) | PDF + structured |
| Customer complaints / SR | millions/year | structured + PDF |
| Employee records | ~120K | PDF, xlsx |
| Vendor contracts (IT, ATM, security) | thousands | PDF |
| Audit reports (internal + statutory) | hundreds/year | PDF |
| Branch/ATM property docs | thousands | PDF |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| CEO Sashidhar Jagdishan | "Top NPA exposure by sector." | analytics | ✓ if Loan fields extracted |
| Branch Manager | "Loans overdue > 30 days at my branch." | scoped + filter | ✓ |
| Credit officer | "Mrs. Sharma's complete credit history." | customer-scoped | ✓ L4 customer entity |
| Compliance / AML | "Transactions flagged this week with structuring patterns." | anomaly + multi-hop | ✓ L3 Transaction + rarity + graph |
| Customer service | "Open tickets for this customer." | scoped | ✓ |
| HR | "Branch managers with required NISM certifications." | structured + entity | ✓ |
| Risk | "Exposure to MFI sector by region." | analytics + group-by | ✓ |
| Audit | "Loan disbursements > 50L without senior signoff." | compliance | ✓ L3 + workflow check |

**Where we shine:** transactions are perfect L3 atomic-unit case (we already use this in our walkthrough). Customer-as-entity is exact L4 fit. AML structuring detection is exactly anomaly + HippoRAG multi-hop.

**Where we struggle:**
- Same privacy story as Apollo — strict row/field permissions are Wave C.
- Billion cheque images would force vector storage graduation.
- RBI live filings need API integration, not file upload.
- Real-time fraud alerting is not KB; it's streaming.

---

## 6. Government of Maharashtra — state government

**Business:** Governs 12cr citizens, 36 districts, multiple departments.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| Land records (7/12 extracts, etc.) | ~3-4cr parcels | PDF (scanned, Marathi + English) |
| Birth / death certificates | millions/year | PDF |
| Voter rolls | ~9cr entries | xlsx (large) |
| Income / property tax records | millions/year | PDF + structured |
| Court orders / judgments | millions historical | PDF + scanned |
| Tender documents + bids | thousands/year | PDF |
| RTI requests + responses | tens of thousands/year | PDF, email |
| Government circulars (GR) | hundreds/year | PDF |
| Police FIRs | millions | PDF (scanned) |
| Welfare scheme records (PDS, ration) | millions of households | xlsx, PDF |
| Education enrollment records | crores | xlsx |
| Public health records | crores | PDF + structured |
| District administrative orders | thousands/district/year | PDF |
| Cabinet papers, budget docs | hundreds/year | PDF |
| CAG audit reports | yearly | PDF (huge) |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| Chief Minister | "Implementation status of 10 flagship schemes by district." | global computation | ⚠ needs scheme-status tracking |
| District Collector | "Land records in dispute, Beed taluka." | scoped filter | ✓ L3 Parcel + dispute flag |
| Revenue officer | "Stamp duty defaults last quarter." | analytics | ✓ |
| Tehsildar | "Irrigation budget utilization, Beed." | computation | ✓ if budget records extracted |
| Court | "All pending land disputes by taluka." | aggregated query | ✓ |
| RTI applicant | "School enrollment in my taluka." | citizen-facing | ✓ |
| Auditor | "GST mismatches in PWD contracts." | anomaly + cross-doc | ✓ L3 + cross-reference |
| Welfare | "Households with ration card but no Aadhaar." | cross-doc set | ✓ L4 set difference |
| Citizen | "Find ration shop closest to my address." | geographic | ✗ needs geo-index (Wave C) |

**Where we shine:** land records = exact our case (doc-as-parcel + history entries). Voter rolls = ID xlsx case (each row a Resident entity). Identity resolution across welfare schemes (same person, different IDs) is the textbook L4 problem.

**Where we struggle:**
- Multilingual OCR (Marathi handwriting on land records 30 years old) — Mistral OCR 3 helps; some failure rate stays.
- Crore-scale voter rolls force xlsx batch ingestion + careful row-as-atomic-unit; doable but needs careful engineering.
- Strict access tiers (citizen RTI vs. cabinet papers) is a hard permissions story — Wave C.
- Geographic / spatial queries need PostGIS; not in MVP.

---

## 7. L&T Construction — engineering & construction

**Business:** Buildings, metros, defense, hydrocarbons, power transmission.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| CAD/BIM drawings (per project) | hundreds of thousands | PDF, DWG, RVT |
| Bill of materials (BoQ) | tens of thousands | xlsx, PDF |
| Project schedules (MS Project) | thousands | MPP, xlsx |
| Subcontractor contracts | tens of thousands | PDF |
| Daily safety reports per site | continuous × site | PDF (scanned, often) |
| Quality test reports (concrete, weld, etc.) | millions | PDF + structured |
| Vendor PO/invoices | hundreds of thousands/year | PDF |
| Government approvals + clearances | thousands | PDF |
| Land / site documents | thousands | PDF (scanned) |
| Drone / aerial surveys | thousands | image + video |
| RFI / RFQ tracking | tens of thousands/project | PDF, email |
| Photographs / progress (per site) | millions | image |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| MD | "Cost overrun by project, current quarter." | analytics | ✓ if project-cost extracted |
| Mumbai Metro Project Director | "Delay drivers across stations." | analytics + anomaly | ⚠ requires schedule-extract |
| Site Engineer | "Drawing for column C7, latest revision." | versioned doc retrieval | ✓ + revision tracking |
| Procurement | "Rebar prices last 6 months." | temporal series | ✓ L3 LineItem + temporal |
| Safety Head | "Fatal / near-miss incidents this year." | filter + anomaly | ✓ |
| Subcontractor manager | "Subcontractors with both delayed delivery AND safety incident." | multi-hop | ✓ HippoRAG |
| Quality | "Concrete batch readings out of spec last month." | anomaly | ✓ L3 + rarity |
| Civil engineer | "Show me all bolted joints rated for 50 tonne in plant X drawings." | CAD geometry query | ✗ requires CAD-aware tool |

**Where we shine:** subcontractor-as-entity, safety-incidents-as-atomic-units, BoQ-as-line-items. Multi-hop ("delayed sub + safety incident") is HippoRAG. Drawing version tracking = doc lineage we already support.

**Where we struggle:**
- Native DWG/RVT geometry queries — out of scope. We extract via ColPali (Wave C) + metadata.
- Site photo content understanding ("show me sites where formwork wasn't braced") would need a vision pipeline.
- Linking drone survey diffs to project progress — beyond MVP.

---

## 8. Cyril Amarchand Mangaldas — large law firm

**Business:** Full-service law firm, ~1000+ lawyers, multiple practice areas.

**Document profile:**

| Type | Volume | Format |
|---|---|---|
| Case files (litigation, advisory) | tens of thousands | mixed PDF |
| Court filings / judgments | hundreds of thousands | PDF (scanned + digital) |
| Contracts drafted (M&A, banking, etc.) | hundreds of thousands historic | PDF, docx |
| Legal opinions | tens of thousands | docx, PDF |
| Due diligence reports | thousands | PDF (large, structured) |
| Client emails | millions | EML |
| Tax / regulatory filings (on behalf of clients) | thousands/year | PDF |
| IP filings (patents, TM) | thousands | PDF |
| Time sheets (billing) | hundreds of thousands | structured |
| Conflict-check records | continuous | structured |
| Precedent library / treatises | thousands | PDF, docx |
| Internal memos | tens of thousands | docx |

**Users & queries:**

| Role | Query | Type | Verdict |
|---|---|---|---|
| Managing Partner | "Workload by partner this quarter." | analytics | ✓ |
| M&A Partner | "Precedent indemnity caps in our $100M+ deals." | clause filter + scope | ✓ L3 Clause + transaction-size filter |
| Associate | "Draft NDA based on our standard with party X." | template + entity | ⚠ generation, not pure retrieval |
| Litigation Lead | "All our cases against Company X." | entity filter | ✓ L4 |
| Conflict Check | "Are we conflicted on representing Y vs Z?" | cross-doc set check | ✓ |
| Senior partner | "Cases with unusually long pendency." | anomaly | ✓ L3 + temporal rarity |
| Tax | "Time billed by department last quarter." | analytics | ✓ |
| Knowledge Mgmt | "Find similar fact patterns to current case." | semantic + multi-hop | ✓ HyDE + RAPTOR |

**Where we shine:** law firms are *the* knowledge-base archetype. Clause precedent retrieval = our exact strength. Cross-doc conflict checks = identity resolution. "Similar case" = HyDE + RAPTOR + semantic. Time-billed = L3 atomic units (line items).

**Where we struggle:**
- Privileged-client information requires strict access tiers (Wave C).
- Generation use cases (drafting) extend beyond pure retrieval; we can do it but it's a different pipeline (template + retrieval + draft).
- Court e-filing integration is API work outside MVP scope.

---

## Cross-cutting patterns

### What every enterprise has in common

1. **Hetero doc mix is the norm**, not the exception. Every company above has PDFs (digital + scanned), spreadsheets, images, and structured data.
2. **Identity resolution across docs is critical** — same vendor / same patient / same customer / same parcel appears in dozens of docs. L4 isn't optional.
3. **Both retrieval AND computation are required.** "Show me X" + "Sum X by Y" both exist. Our L3 atomic-unit → structured fields → SQL aggregation handles computation; chunks + RAPTOR + rerank handle retrieval.
4. **Anomaly detection is implicit in many queries.** "Unusual", "flagged", "outside spec", "outlier" — all map to L3 rarity.
5. **Multi-hop reasoning is the default in real questions.** "Subcontractors with delayed delivery AND safety incident" requires graph traversal — HippoRAG.
6. **Permissions are everywhere.** Bank/hospital/government cannot deploy without row/field/entity-level ACL. **This is the single biggest gap between MVP and real-world deployment.**
7. **Real-time / streaming is NOT KB.** POS receipts, SCADA, ATM transactions, EMR vitals — those are OLAP/streaming domains. KB indexes documents.
8. **Specialized media (CAD, DICOM, BIM, video) requires domain tools beyond MVP.** We extract metadata via VLM/ColPali (Wave C) but can't answer geometry-aware questions.

### Where the MVP architecture is genuinely strong

- **Identity resolution + L4 entity layer** — every company benefits.
- **L3 atomic-unit + rarity** — works for clauses, transactions, lab readings, line items, components, parcels, rows.
- **Schema-emerges-from-data** — none of these companies can pre-define their schema completely; all want to add fields as they learn.
- **Citation grounding + refusal** — banks, hospitals, govt, law firms all need defensible answers.
- **Multi-resolution retrieval (chunks + RAPTOR + atomic units + graph)** — handles factoid + vague + multi-hop in one pipeline.
- **Hybrid retrieval + rerank** — the "boring is winning" insight from production systems generalises.
- **Per-doc-type plug-in extractor** — adding a doc type is a config + plug-in, not a core change.

### Where the MVP needs honest disclaimers (Wave C / beyond)

| Gap | Affects | Plan |
|---|---|---|
| Row/field-level permissions | Banks, hospitals, govt, law firms | Cite, deferred to Wave C. The architecture has `domain_id` everywhere ready for ACL. |
| Vector graduation at 100M+ chunks | Reliance, HDFC, Govt | Cite Turbopuffer / Qdrant migration path. Adapter interface keeps it swappable. |
| Multi-tenant isolation | All multi-vertical | Cite. Logically same as permissions. |
| Native CAD / BIM / DICOM | Tata, L&T, Apollo | ColPali (Wave C) for visual retrieval; geometry queries need domain tools. |
| Geographic / spatial queries | Govt, retail (location) | PostGIS extension, future work. |
| Real-time streaming | All | Out of scope by design — KB ≠ OLAP. |
| Temporal validity (valid_from/to) | Govt, law, healthcare | Cite. Bi-temporal schema, future work. |
| Generation use cases (drafting, summarisation outputs) | Law firms, consulting | Possible via existing LLM layer; not the focus of the MVP. |

### Translating to the demo

The eval set should sample queries representative of these scenarios. Concretely, for the 30-question demo eval:

- 5 needle queries (vocabulary mismatch, single-doc-in-corpus)
- 5 multi-hop (Reliance cross-vertical, L&T multi-criteria subcontractor)
- 5 rare-atomic-unit (Tata lab outliers, D-Mart anomalous invoices)
- 5 entity-scoped (Apollo patient history, HDFC customer history)
- 5 negative / refusal (Reliance "ever signed exclusivity with Adani?")
- 5 long-form synthesis (Tata "summarise environmental compliance posture")

This proves domain-agnosticism on the eval, not just by waving hands.

---

## Bottom line

**The architecture covers ~80% of real enterprise KB queries across all 8 scenarios.** The remaining 20% breaks down as:

- **~10% needs permissions** (banking, healthcare, government, legal — all require ACL before production deployment). Deferred to Wave C, cited in writeup.
- **~5% needs specialized media handling** (CAD geometry, DICOM, video). Out of MVP scope.
- **~5% is OLAP / streaming workloads** (POS, SCADA, ATM). Not a KB concern by design.

Everything else — identity resolution, multi-hop, anomaly, vague queries, clause-level / transaction-level / component-level / row-level retrieval, schema evolution — is in the MVP. None of these companies would be face-palmed by the architecture; some would simply need Wave C extensions before production.
