---
doc_id: hdfc-bank-cross-domain
doc_type: cross_domain_view
effective_date: 2026-05-26
parties: [HDFC Bank Ltd]
status: live
stressors: [cross_domain_finance]
---

# HDFC BANK LTD — CROSS-DOMAIN VIEW

**Purpose:** Aggregate view of HDFC Bank's footprint across the corpus.

**Domains where HDFC appears:**
- Finance (primary — many docs)
- Construction (referenced as facility-bank for the Acme datacentre project)
- Legal (referenced in some contracts as banker)
- (No direct appearance in Healthcare, Mining, Government — but their corporate customers are in those domains)

---

## HDFC Bank Profile

| Field | Value |
|---|---|
| Legal name | HDFC Bank Ltd |
| Headquartered | Mumbai |
| Branches involved in this corpus | Gurugram Sector 18, Bandra West, Bandra Kurla Complex, Lower Parel, Pune Camp |
| Key relationship | Acme Corp (CIF HDFC-CORP-CIF-00184028 — 11-year history) |

---

## HDFC's Corporate Banking Relationships

| Customer | CIF | Relationship | Key products |
|---|---|---|---|
| Acme Corp Pvt Ltd | HDFC-CORP-CIF-00184028 | Imperia Corporate (since 2014) | Term loan + CA + LC + forex |
| NorthWind Capital India Pvt Ltd | HDFC-CORP-CIF-00184382 | FI segment (NBFC, since 2024) | USD account + LC + ECB facility agent |
| Apollo Hospitals Pune Pvt Ltd | HDFC-CORP-CIF-00188421 | Healthcare segment (since 2014) | Operating CA + equipment loan |
| Aniket Desai (individual) | HDFC-IND-CIF-00482184 | Imperia HNW (since 2024) | Savings + Demat + Locker |
| Smita Desai (spouse) | HDFC-IND-CIF-00482185 | Privy League | Savings (joint) |
| Vertex Industries | HDFC-CORP-CIF-00184182 | (passively, via Acme MSA flows) | Operating CA |

---

## HDFC in the FINANCE Domain (Primary Documents)

### Bank Statements (4 monthly + 1 quarterly + 1 Apollo)

| Statement | Customer | Notes |
|---|---|---|
| statement-001 through statement-004 | Acme Corp | Q4 FY 2024-25 + quarterly summary |
| statement-008-apollo-loan-payment | Apollo Hospitals Pune | CT-scanner loan account |

### Loan Documents

| Doc | Customer | Notes |
|---|---|---|
| loan-001 chain (3 docs) | Acme | INR 80 cr term loan |
| loan-002-northwind-hdfc | Acme (via NorthWind) | USD 80M ECB facility |
| loan-003-apollo-equipment-financing | Apollo | INR 22.4 cr CT scanner |

### KYC + Compliance

| Doc | Customer |
|---|---|
| kyc-001-aniket-desai | Aniket Desai (individual HNW) |
| kyc-003-acme-corporate | Acme Corp Pvt Ltd |
| kyc-004-northwind-corporate | NorthWind Capital India Pvt Ltd |

### Complaints + Service

| Doc | Customer | Issue |
|---|---|---|
| complaint-001-desai-* (chain) | Aniket Desai | Duplicate USD wire (resolved) |
| complaint-004-hdfc-cheque-clearing | Vertex Industries | Cheque clearing delay (resolved) |

### Treasury + Audit

| Doc | Subject |
|---|---|
| treasury-001 through 004 | Acme treasury policy + memos |
| audit-001-acme-internal-q3 | Acme internal audit |
| audit-002-acme-statutory-fy2324 | Acme statutory audit (KPMG) |

### Wire Threads (4 of 5 involve HDFC)

| Thread | Customer |
|---|---|
| wire-001-acme-vertex-routine | Acme |
| wire-002-northwind-loan-disbursement | Acme via NorthWind |
| wire-004-billion-dollar-suspicious | (suspicious wire attempt to HDFC) |
| wire-005-apollo-vendor-payment | Apollo |

### Customer Profiles

| Profile | Customer |
|---|---|
| profile-001-acme-corporate | Acme |
| profile-002-northwind-corporate | NorthWind |
| profile-003-aniket-desai-retail | Aniket Desai |
| profile-005-apollo-hospitals | Apollo |

---

## HDFC in the CONSTRUCTION Domain (References)

HDFC Bank is referenced in construction documents in these contexts:

(i) **Performance bond for the EPC contract:** Mahalaxmi Infra provides a INR 4.41 cr Bank Guarantee from HDFC for the Acme datacentre project (per contract-001-acme-mahalaxmi-epc).

(ii) **Counterparty acknowledgement in loan-related documents:** The Acme HDFC term loan secures the datacentre asset (per loan-001-addendum-2 in finance domain, which references the construction project).

(iii) **Insurance:** HDFC ERGO (sister concern) provides Workmen Compensation + All-Risks Construction Insurance for the project.

---

## HDFC's Key People (Cross-References)

| Name | Role | Customer they support |
|---|---|---|
| Ms. Anuradha Kapoor | Senior RM — Corporate (Gurugram + Bandra West Branch Head) | Acme + Aniket Desai (parent role) |
| Mr. Raj Mehta | RM Imperia (Bandra West) | Aniket Desai (individual) |
| Mr. Praveen Iyer | Senior RM — Corporate/FI (BKC) | NorthWind India |
| Mr. Sandeep Bose | Senior RM — Healthcare (Pune Camp) | Apollo Hospitals Pune |
| Mr. Sumeet Bhandari | Head of Wire Operations Mumbai | Various |
| Ms. Smita Sharma | Cluster Compliance Officer (Mumbai West) | Complaint resolution + AML |
| Mr. Rajesh Saini | Branch Head Gurugram Sector 18 | Acme corporate |

---

## HDFC's Cross-Domain Risks (Synthesised)

(i) **Concentration:** Acme's exposure (deposit + loan + LC + hedge) at HDFC totals INR 174 cr — large but within policy

(ii) **Geographic spread:** Customer relationships span Gurugram + Mumbai + Pune + Bangalore — diverse

(iii) **Sector diversification:** Tech (Acme), NBFC (NorthWind), Healthcare (Apollo) — diverse

(iv) **PEP-related customers:** Aniket Desai (President NorthWind India) is classified as PEP-related due to his brother's former government role. Enhanced due diligence is on file.

(v) **AML watchpoints:** One suspicious wire attempt (USD 1.2B from "Citadel Holdings Mauritius") was correctly rejected + STR filed (wire-004). Internal audit + AML processes are functioning.

(vi) **Cross-domain entity collisions:** "Priya Iyer" name collision flagged March 2024 — distinct individuals (Acme legal vs Apollo Pune patient); false positive cleared.

---

**This is a synthesis document. For specific transaction details or facts, consult the source documents in their respective domains.**
