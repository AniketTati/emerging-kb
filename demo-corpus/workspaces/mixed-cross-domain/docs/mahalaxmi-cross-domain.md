---
doc_id: mahalaxmi-cross-domain
doc_type: cross_domain_view
effective_date: 2026-05-26
parties: [Mahalaxmi Infrastructure Pvt Ltd]
status: live
---

# MAHALAXMI INFRASTRUCTURE — CROSS-DOMAIN VIEW

**Purpose:** Mahalaxmi appears in both **Construction** (as main contractor on Acme datacentre + bidder on CIDCO tender) and **Mining** (as vendor implicated in incident-003). Disambiguating these contexts + the related entities is a test case for cross-domain entity resolution.

---

## Mahalaxmi Profile

| Field | Value |
|---|---|
| Legal name | Mahalaxmi Infrastructure Pvt Ltd |
| CIN | U45200MH2002PTC184118 |
| PAN | AABCM4218P |
| Founded | 2002 |
| Headquartered | Mahalaxmi Tower, Worli Sea Face, Mumbai 400018 |
| MD | Mr. Vinay Pandey |
| Operations Director | Mr. Bharat Sanghavi |
| Industry | Engineering-Procurement-Construction (EPC) + infrastructure |
| FY 2023-24 Revenue | INR 282 cr (3-year average) |
| Employee strength | ~1,200 (incl. site labour) |
| Active projects (2025-26) | 8 across Karnataka + Maharashtra + Gujarat |

---

## Mahalaxmi in CONSTRUCTION (Primary Role)

Mahalaxmi is the **main contractor** on the Acme Whitefield Datacentre Phase-2 project. Key contracts + docs:

- **contract-001-acme-mahalaxmi-epc** — INR 44.10 cr EPC contract (LSTK)
- **boq-001-datacentre** — Detailed BoQ
- **subcontract-001-phoenix-mep** — Sub-contracts to Phoenix MEP for MEP scope
- **safety-001-incident-fall-* (chain)** — Worker fall incident chain (May 2025)
- **safety-002 + safety-003** — Monthly safety reports
- **daily-001 through daily-004** — Daily site reports
- **inspection-001 + inspection-002** — Factory + MEP walk-down inspections
- **schedule-001-project-master-schedule** — Master schedule
- **progress-001 + progress-002** — Monthly progress reports
- **change-order-001 + change-order-002** — Cumulative variations
- **handover-001-mechanical-completion** — Final handover
- **labour-contract-001-sai-marathi-scan** — Labour-supply sub-contract (Sai Labour Agency)

Mahalaxmi has also bid on the CIDCO Sector 47-49 tender (T-018):
- **tender-001-cidco-road-package** — Tender notice
- **bid-001-mahalaxmi-cidco-response** — Mahalaxmi's bid response (INR 176.42 cr)

---

## Mahalaxmi in MINING (Cross-Domain Reference)

Mahalaxmi is referenced in the mining domain as a **vendor implicated in incident-003**. Cross-domain context:

- **incident-003 (mining domain):** Mahalaxmi Equipment Pvt Ltd was a vendor on a haul road maintenance contract at a mining site. The incident involves a multi-hop entity-resolution challenge:
  - "Mahalaxmi Equipment Pvt Ltd" (the equipment vendor) is part of the same Mahalaxmi group as "Mahalaxmi Infrastructure Pvt Ltd" (the construction main contractor)
  - DGMS inspector cross-referenced the incident to the parent group
  - Investigation cleared Mahalaxmi Infra of direct responsibility but did note the group's broader vendor management practices

Note: Without ancillary signals (CIN difference: Mahalaxmi Equipment has a separate CIN), it would be tempting to conflate the two. The corpus deliberately maintains both as related-but-distinct entities.

---

## Mahalaxmi's Other Projects (Mentioned in Bid)

Per bid-001-mahalaxmi-cidco-response, Mahalaxmi has past projects in Mumbai Metro + Pune Smart Cities + Nashik Smart City + Mahalaxmi-Bandra Bypass. These are credentials cited in the bid but the docs themselves are NOT in the corpus.

---

## Mahalaxmi's Risk Profile

(i) **Safety:** One LTI in May 2025 (Mr. Kumar fall); response was exemplary; corrective actions complete; Factory Inspector commendation.

(ii) **Quality:** Strong ISO certifications (9001 + 14001 + 45001); concrete test passing; cable-routing NCR-014 was minor + closed.

(iii) **Schedule:** Datacentre project tracking to 28 Feb 2026 completion (on track).

(iv) **Cross-domain reputation:** Group entity (Mahalaxmi Equipment) implicated in mining incident-003 — separate legal entity but reputational link with the parent.

(v) **Financial:** Solvent + creditworthy (Net Worth INR 84.2 cr per CIDCO bid)

---

## Key Mahalaxmi People (Cross-References)

| Name | Role | Where they appear |
|---|---|---|
| Mr. Vinay Pandey | Managing Director | Signs key contracts; visible in incident-fall chain; CIDCO bid signatory |
| Mr. Bharat Sanghavi | Director Operations | Cost engineering + BoQ signoff; CIDCO bid signatory |
| Mr. Rakesh Iyer | Project Manager (Datacentre) | Daily site reports; meeting minutes; progress reports |
| Mr. Anand Krishnamurthy | Site Engineer (Civil) | Daily reports; RFI threads; safety reports |
| Mr. Pradeep Bhargava | Senior Safety Officer | Safety reports; incident investigation |
| Mr. Pradeep Kale | Allocated PM for CIDCO bid (if won) | Bid response only |

---

## Disambiguation: Mahalaxmi Infrastructure vs Mahalaxmi Equipment

| Attribute | Mahalaxmi Infrastructure Pvt Ltd | Mahalaxmi Equipment Pvt Ltd |
|---|---|---|
| CIN | U45200MH2002PTC184118 | (different CIN — not specified in corpus) |
| Industry | EPC / construction services | Equipment supply (mining + construction) |
| Where it appears | Construction domain (primary) | Mining domain (incident-003 reference) |
| Relationship | Parent group | Sister concern under same Mahalaxmi group |
| Legal status | Distinct legal entity (Pvt Ltd) | Distinct legal entity (Pvt Ltd) |
| Shareholding link | Both ultimately controlled by Pandey family | Both ultimately controlled by Pandey family |

For entity-resolution + retrieval, these should be treated as **related but distinct**. Queries about construction projects → Mahalaxmi Infrastructure. Queries about mining-incident equipment → Mahalaxmi Equipment.

---

**This is a synthesis document. Source documents per domain manifest.**
