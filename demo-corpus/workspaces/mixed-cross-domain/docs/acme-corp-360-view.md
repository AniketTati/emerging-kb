---
doc_id: acme-corp-360-view
doc_type: cross_domain_view
effective_date: 2026-05-26
parties: [Acme Corp Pvt Ltd]
status: live
stressors: [cross_domain_finance]
---

# ACME CORP PVT LTD — 360° CROSS-DOMAIN VIEW

**Purpose:** A consolidated view of Acme Corp's footprint across all corpus domains. This document is for evaluation testing — it does NOT replace the source documents but provides a "what does the system know about Acme?" reference.

**Domains where Acme appears:**

- Legal (counterparty in 8 contracts)
- Finance (debtor + bank customer + audit subject)
- Construction (client for the Whitefield Datacentre Phase-2 project)
- (No appearance in Healthcare, Mining, or Government)

---

## Acme's Corporate Identity

| Field | Value |
|---|---|
| Legal name | Acme Corp Pvt Ltd |
| CIN | U72200DL2008PTC184218 |
| PAN | AAACA1842B |
| Incorporated | 8 April 2008, Delhi |
| Industry | Software publishing (B2B SaaS) |
| Registered office | 11th Floor, Tower B, Cyber Hub, DLF Phase 2, Gurugram |
| HDFC Bank CIF | HDFC-CORP-CIF-00184028 |
| Employees | 1,242 (June 2024); ~1,440 forecast (June 2025) |
| Revenue (FY 2023-24) | INR 184.2 crore |
| Revenue (FY 2024-25 est) | INR 220-235 crore |

---

## Acme in the LEGAL Domain

Acme is a counterparty in **8 contracts** in the legal domain:

| Contract | Counterparty | Role | Key feature |
|---|---|---|---|
| msa-001-acme-vertex | Vertex Industries | Customer (Acme = service buyer) | Payment terms NET-30 → NET-60 chain |
| msa-001-amendment-1 | Vertex Industries | Customer | Payment chain |
| msa-001-amendment-2 | Vertex Industries | Customer | Payment chain (winner) |
| msa-002-acme-nimbus | Nimbus Technologies | Customer | 4-hour delivery SLA (rare clause) |
| nda-mutual-001-acme-vertex | Vertex Industries | Both | Mutual NDA |
| license-001-acme-orion-mfn | Orion Consulting | Licensor | MFN clause |
| sow-001-acme-vertex | Vertex Industries | Customer | Statement of Work under MSA |
| employment-contract-001-acme | (HR — internal) | Employer | Standard employment terms |

**Key signatories from Acme:**
- Mr. Rakesh Sundaram (CEO) — signs all major commercial contracts
- Ms. Priya Iyer (VP Legal) — signs / countersigns / witnesses; see **entity-collision-priya-iyer** for disambiguation with Apollo patient
- Ms. Jayanti Iyer (CFO) — signs financial contracts + loan documentation

---

## Acme in the FINANCE Domain

Acme has multiple finance-domain touchpoints:

### Bank Statements

| Statement | Period | Notes |
|---|---|---|
| statement-001-acme-hdfc-jan-2025 | Jan 2025 | +INR 4.4cr net |
| statement-002-acme-hdfc-feb-2025 | Feb 2025 | +INR 3.3cr net |
| statement-003-acme-hdfc-mar-2025 | Mar 2025 | +INR 2.2cr net (includes Addendum #2 fee) |
| statement-004-acme-hdfc-quarterly-summary | Q4 FY24-25 | Aggregated view |

### Loan Documents (HDFC Term Loan chain)

| Doc | Rate | Status |
|---|---|---|
| loan-001-acme-hdfc-original | 8.85% (Aug 2023) | SUPERSEDED |
| loan-001-acme-hdfc-addendum-1 | 9.40% (Jun 2024) | LIVE (rate revision) |
| loan-001-acme-hdfc-addendum-2 | 9.40% (Mar 2025) | LIVE (collateral substitution to Bangalore data centre) |

**Conflict resolution:** Per the chain logic, the **current interest rate is 9.40%** (per Addendum #1). The Addendum #2 did not touch the rate but reset the collateral.

### Other Finance Documents

| Doc | Type |
|---|---|
| kyc-003-acme-corporate | Corporate KYC + UBO declaration |
| audit-001-acme-internal-q3 | Internal audit (Q3 FY 2024-25 findings) |
| audit-002-acme-statutory-fy2324 | Statutory audit (KPMG, clean opinion) |
| treasury-001-acme-q1-cash-position | Q1 cash deployment |
| treasury-002-acme-fx-hedging | FX hedging policy |
| treasury-003-acme-fy26-budget-DRAFT | ⚠ DRAFT — not authoritative |
| treasury-004-acme-counterparty-credit-limits | Counterparty limits |
| complaint-001-desai-* | Employee complaint chain (Desai) |
| profile-001-acme-corporate | HDFC RM 360 profile |

---

## Acme in the CONSTRUCTION Domain

Acme is the **client** for the Whitefield Datacentre Phase-2 project. This project is **referenced in finance domain** as the collateral asset for Addendum #2.

### Project Overview

| Field | Value |
|---|---|
| Project | Acme Whitefield Datacentre Phase-2 |
| Site | Survey No. 184/2A, Whitefield Main Road, Bangalore 560066 |
| Main Contractor | Mahalaxmi Infrastructure Pvt Ltd (EPC) |
| Architect | Deshpande Architects + Engineers LLP |
| Structural Engineer | Sundar Structural Consultants Pvt Ltd |
| MEP Engineer | Phoenix MEP Services Pvt Ltd |
| Contract value (final) | INR 45.38 cr |
| Construction duration | 11 months (Apr 2025 - Feb 2026) |
| Mechanical Completion | 28 February 2026 |

### Key Construction Documents

- contract-001-acme-mahalaxmi-epc (EPC contract)
- subcontract-002-sundar-structural (structural consultancy)
- boq-001-datacentre (BoQ)
- 3 architectural drawings (Rev A → B → C, with load-bearing wall conflict)
- safety-001-incident-fall-* (3-doc safety incident chain)
- 2 monthly safety reports + 4 daily site reports
- progress-001 + progress-002 (monthly progress)
- 4 RFI threads (.eml)
- environmental-001 (KSPCB Consent to Operate)
- handover-001-mechanical-completion

---

## Acme's Cross-Domain References + Anomalies

(i) **Datacentre Phase-2 is collateral for HDFC loan:** Per loan-001-addendum-2, the Bangalore Whitefield data centre (the construction project) is the primary collateral for Acme's INR 80 crore HDFC term loan. This is a deliberate cross-domain reference.

(ii) **Acme also has a USD 80M ECB facility from NorthWind Capital India:** Per loan-002 + wire-002-northwind-loan-disbursement (.eml in finance), Acme has a separate USD 80M ECB facility with NorthWind Capital India as lender. HDFC Bank serves as facility agent.

(iii) **Acme's vendor for SaaS subscriptions is Vertex Industries:** Cross-references between legal (MSA), finance (bank statements showing Vertex payments), and procurement records.

(iv) **Acme's parent (Nimbus Tech Singapore) holds 22.4% stake:** Cross-references between legal (Acme's UBO disclosure), finance (KYC).

(v) **Apollo Hospitals is BOTH a vendor TO Acme + an Acme customer:** Acme's hospital management software (HMIS) is licensed to Apollo (legal MSA), while Apollo pays Acme via HDFC bank channels (finance bank statements). Apollo is also a separate Acme construction client (Pune maternity project — different from datacentre).

(vi) **Priya Iyer's name collision:** See entity-collision-priya-iyer.md — VP Legal Acme (DOB 1979) vs Pune Apollo patient (DOB 1994). Two distinct individuals.

(vii) **Aniket Desai (HDFC HNW customer) is at NorthWind Capital India — not at Acme:** Separate individual; serves as Acme's lender contact (via NorthWind). Sometimes confused due to overlap of HDFC banking.

---

## Acme's Risk Profile (Cross-Domain Synthesis)

(i) **Concentration risk with HDFC:** ~79% of total banking exposure with HDFC (per treasury-004 + profile-001).

(ii) **Interest rate exposure:** 9.40% on the HDFC term loan (post-Addendum #1) — fixed for tenure.

(iii) **Capex risk:** INR 45+ cr datacentre Phase-2 construction; on track for Feb 2026 completion.

(iv) **Sectoral risk:** SaaS / software services — mild post-COVID consolidation; not adverse.

(v) **Litigation risk:** Per audit-002 (Note 18), potential dispute with Nimbus Technologies regarding licence fees (INR 4.2 cr) — no provision made; legal counsel views as without merit.

(vi) **Operational risk:** May 2025 safety incident at Bangalore site (worker fall) — well managed; reputational impact minimised; CEO formally commended the response.

---

**This document is a synthesis. The authoritative source for any specific fact about Acme is the individual referenced document.**
