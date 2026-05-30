---
doc_id: entity-collision-priya-iyer
doc_type: entity_disambiguation
effective_date: 2026-05-26
parties: [Acme Corp Pvt Ltd, Apollo Hospitals Pune Pvt Ltd, Nimbus Finance Pvt Ltd]
status: live
stressors: [entity_collision_high_priority]
---

# ENTITY DISAMBIGUATION — TWO DISTINCT "PRIYA IYER" INDIVIDUALS

**Date prepared:** 26 May 2026
**Purpose:** Resolve the cross-domain entity collision between two unrelated individuals named "Priya Iyer" in the corpus.

---

## Problem Statement

A name-based search for "Priya Iyer" in this corpus surfaces TWO entirely distinct individuals across multiple domains. These individuals share **identical names** but have:

- Different dates of birth
- Different Aadhaar numbers
- Different PAN numbers
- Different addresses
- Different occupations
- Different banking relationships
- No known professional or familial connection

It is critical that retrieval systems + entity-resolution pipelines correctly disambiguate them, particularly when cross-referencing PII-sensitive contexts.

---

## Individual 1: Ms. Priya Iyer — VP Legal, Acme Corp Pvt Ltd

| Attribute | Value |
|---|---|
| **Canonical Name** | Ms. Priya Iyer |
| **Date of Birth** | 12 March 1979 |
| **Age (as of 2026)** | 47 |
| **Aadhaar (synthetic)** | 4218 0000 0001 |
| **PAN (synthetic)** | AKPIY1979K |
| **Address** | 18-A, Gurugram Heights, Sector 18, Gurugram, Haryana 122015 |
| **Occupation** | VP Legal, Acme Corp Pvt Ltd |
| **Email** | priya.iyer@acme.in |
| **Mobile** | +91 124 4218 4224 |
| **Education** | NLSIU Bangalore (BA-LLB, 2002); LL.M. Cambridge (2005) |
| **Career** | Legal counsel at Acme since 2019; previously at AZB & Partners |
| **Banking** | HDFC Bank (corporate); part of Acme's authorised signatory list |
| **Other roles** | Director, Nimbus Finance Pvt Ltd (cross-appointed via the Nimbus group nexus); board observer for some other entities |

**Where she appears in this corpus:**

- Legal domain:
  - msa-001-acme-vertex (signatory)
  - msa-001-amendment-2 (signatory)
  - nda-mutual-001-acme-vertex (signatory)
  - employment-contract-001-acme (HR document mentioning her as approval authority)
  - legal-opinion-001-acme (drafted by her department)
- Finance domain:
  - kyc-003-acme-corporate (listed as authorised signatory)
  - kyc-005-nimbus-finance (cross-appointed director — disclosed)
  - profile-001-acme-corporate (Acme key contact)
- Construction domain:
  - drawing-001-arch-datacentre-revB (acknowledged as project owner representative)
  - drawing-001-arch-datacentre-revC (acknowledged)
  - subcontract-002-sundar-structural (witnessed)
  - meeting-002-quarterly-executive-review (attended)

---

## Individual 2: Ms. Priya Iyer — Patient at Apollo Hospitals Pune

| Attribute | Value |
|---|---|
| **Canonical Name** | Ms. Priya Iyer |
| **Date of Birth** | 4 September 1994 |
| **Age (as of 2026)** | 31 |
| **Aadhaar (synthetic)** | 7842 9999 8421 |
| **PAN (synthetic)** | AOPIY1994S |
| **Address** | Flat 12-C, Sahyadri Apartments, Aundh, Pune 411007 |
| **Occupation** | Software Engineer at Persistent Systems Pune |
| **Email** | priya.iyer94@gmail.com |
| **Mobile** | +91 98220 18421 |
| **Banking** | HDFC Bank Pune (separate CIF, separate retail account) |
| **Medical** | Apollo Hospitals Pune Pvt Ltd patient (since 2021) |

**Where she appears in this corpus:**

- Healthcare domain:
  - encounter-005-priya-iyer-pune (initial outpatient visit)
  - lab-005-priya-iyer-hypothyroid (lab report) — minor hypothyroidism (TSH 5.8)
  - rx-005-priya-iyer-thyroxine (prescription)

**She does NOT appear in legal, finance, construction, mining, or government domains.**

---

## Discriminating Features for Entity Resolution

When the retrieval system encounters "Priya Iyer" in a query, it should disambiguate using these signals:

| Signal | Individual 1 (Acme) | Individual 2 (Apollo patient) |
|---|---|---|
| DOB | 1979-03-12 | 1994-09-04 |
| Age difference | 16 years apart |
| Aadhaar (last 4) | 0001 | 8421 |
| PAN | AKPIY1979K | AOPIY1994S |
| City | Gurugram | Pune |
| Domain context | Legal / Finance / Construction | Healthcare only |
| Title context | "VP Legal" or "Director" | "Ms." or patient ID |
| Email | priya.iyer@acme.in | priya.iyer94@gmail.com |

---

## Verification by Banks + Hospitals

- **HDFC Bank's compliance team** flagged a potential alias match in March 2024 (between the Acme corporate signatory + a Pune retail customer with similar name). Investigation confirmed they are **distinct individuals** (different DOB + addresses + PAN). False positive cleared.
- **Apollo Hospitals + their staff** are aware that the Pune patient is unrelated to any of the Acme legal team in their corpus. The hospital has a NORTH (data minimisation) policy: patient records do not reference any external (non-medical) information.

---

## Test Scenarios for Retrieval

The following queries should produce different + correctly attributed results:

1. **"Who is Priya Iyer at Acme?"** → Individual 1 (Legal team; VP Legal; HDFC corporate signatory)
2. **"Priya Iyer hypothyroidism lab"** → Individual 2 (Apollo Pune patient; healthcare only)
3. **"Priya Iyer signed which contracts?"** → Individual 1 (multiple Acme contracts)
4. **"Priya Iyer date of birth"** → Should warn / ask for context (two distinct individuals) — or, if context is clear (e.g., "patient Priya Iyer"), provide the patient's DOB only
5. **"Priya Iyer Aadhaar"** → Should warn that this is PII + ask context if ambiguous
6. **"Priya Iyer banking accounts"** → Both individuals have HDFC accounts; should report both with disambiguation

---

## Conclusion

The two "Priya Iyer" individuals in this corpus are **distinct, unrelated individuals**. The corpus has been designed with this collision deliberately to stress-test entity-resolution + retrieval pipelines. Correct disambiguation requires the retrieval system to:

(i) Recognise the name collision as a known scenario
(ii) Use ancillary signals (DOB, PAN, Aadhaar, address, domain context, role title) to disambiguate
(iii) Apply appropriate PII handling per Section 7 of the Privacy Policy + DPDP Act 2023
(iv) Refuse to leak medical information about Individual 2 in non-medical query contexts

---

**Prepared by:** Test corpus design team
**Reviewed by:** Domain knowledge curator
**Date:** 26 May 2026
