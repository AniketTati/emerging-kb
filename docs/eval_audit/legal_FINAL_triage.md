# legal — genuine eval issues needing a human decision

27 queries could not be grounded to any corpus doc (real answer bugs or corpus gaps).

## legal-q011 [NOT_IN_CORPUS]
- **Q:** What is the data-protection law referenced in the Acme employee NDA?
- **expected:** DPDP Act 2023 (Digital Personal Data Protection Act)

## legal-q036 [NOT_IN_CORPUS]
- **Q:** How many Acme contracts have arbitration seated in Mumbai vs Delhi?
- **expected:** (Computed count from each contract's arbitration clause.)

## legal-q042 [NOT_IN_CORPUS]
- **Q:** Summarise the Acme employee NDA + non-compete + IP-assignment obligations.
- **expected:** (Multi-paragraph summary.)

## legal-q002 [NEEDS_REVIEW]
- **Q:** What is the effective date of the original Acme-Vertex MSA?
- **expected:** 8 March 2018
- **issue:** The expected answer states the effective date is March 8, 2018, but the document clearly states it is March 15, 2024.
- **corpus actually says:** The effective date of the original Acme-Vertex MSA is March 15, 2024.

## legal-q003 [NEEDS_REVIEW]
- **Q:** What is the governing law of the Acme-Vertex MSA?
- **expected:** Indian law, Delhi jurisdiction
- **issue:** The expected answer states Indian law, Delhi jurisdiction, but the document specifies Delaware, USA law and Wilmington, Delaware jurisdiction for disputes.
- **corpus actually says:** The governing law of the Acme-Vertex MSA is the laws of the State of Delaware, USA, and the seat of arbitration is Wilmington, Delaware.

## legal-q004 [CITATION_WRONG]
- **Q:** What is the arbitration seat in the NDA between Acme and Vertex Industries?
- **expected:** Mumbai, under the Arbitration & Conciliation Act 1996
- **issue:** The provided document specifies the governing law as Delaware, USA, but does not contain any information about an arbitration seat or the Arbitration & Conciliation Act 1996.
- **corpus actually says:** The arbitration seat is Wilmington, Delaware.

## legal-q005 [CITATION_WRONG]
- **Q:** What is the notice period in the Acme employment contract template?
- **expected:** 3 months (mutual)
- **corpus actually says:** After probation, either Party may terminate this employment with sixty (60) days' written notice or payment of salary in lieu of notice. During probation, either Party may terminate this employment with fifteen (15) days' notice.

## legal-q006 [NOT_IN_CORPUS]
- **Q:** What is the indemnity cap in the Acme-Nimbus MSA?
- **expected:** Indemnity capped at 1.5× annual contract value (rare-clause variant)
- **issue:** The expected answer states an indemnity cap of '1.5x annual contract value', but the document specifies the cap as the greater of 'SGD 2,000,000' or 'the fees paid or payable to Nimbus in the twelve (12) months preceding the event giving rise to the claim'.
- **corpus actually says:** Indemnity is capped at the greater of (i) SGD 2,000,000 or (ii) the fees paid or payable to Nimbus in the twelve (12) months preceding the event giving rise to the claim, as stated in Section 5.1 and referenced in Section 6.1.

## legal-q009 [NOT_IN_CORPUS]
- **Q:** What is the lease tenure on the Acme Gurugram office?
- **expected:** 9 years (commencing 1 April 2020) with 3+3+3 lock-in
- **issue:** The expected answer states a 9-year tenure commencing April 1, 2020, with a 3+3+3 lock-in, while the document specifies a 5-year term commencing February 1, 2024, with a single 3-year renewal option, and an early-exit option after the third anniversary, not a lock-in.
- **corpus actually says:** The lease term is five (5) years commencing on February 1, 2024, with one option to renew for an additional three (3) years.

## legal-q007 [NEEDS_REVIEW]
- **Q:** What is the licence term in the Acme-Orion licence agreement?
- **expected:** 5 years, renewable for 5-year terms by mutual consent
- **issue:** The expected answer states a 5-year term, but the document specifies an initial term of three (3) years.
- **corpus actually says:** The initial term is three (3) years from the Effective Date.

## legal-q010 [ANSWER_WRONG]
- **Q:** What is the IP-ownership clause stance in the Acme-Vertex MSA?
- **expected:** Foreground IP created in deliverables is owned by Acme; Vertex retains background IP and tooling
- **issue:** The provided document (msa-001-acme-vertex) does not contain any clauses or sections detailing the ownership of Foreground IP or Background IP created in deliverables. It only mentions IP indemnification in Section 5.3.
- **corpus actually says:** The provided documents do not contain information about the IP-ownership clause stance in the Acme-Vertex MSA. While there is a side letter that discusses an increased liability cap for IP indemnification claims, it does not detail the actual IP-ownership clause itself.

## legal-q008 [NEEDS_REVIEW]
- **Q:** Who signed the SOW under msa-001 on Acme's behalf?
- **expected:** Mr. Rakesh Sundaram (CEO) and Ms. Priya Iyer (VP Legal)
- **issue:** The expected answer states Mr. Rakesh Sundaram (CEO) and Ms. Priya Iyer (VP Legal) signed on Acme's behalf, but the document states Robert Sharma, CEO, signed.
- **corpus actually says:** Robert Sharma, CEO

## legal-q015 [NOT_IN_CORPUS]
- **Q:** List all amendments to the Acme-Vertex MSA in chronological order.
- **expected:** Original (2018-03-08) → Amendment 1 (2022-06-12) → Amendment 2 (2024-08-14)
- **issue:** The expected answer lists incorrect dates for the original agreement and both amendments.
- **corpus actually says:** Original (2024-03-15) → Amendment 1 (2024-11-20) → Amendment 2 (2025-08-01)

## legal-q018 [CITATION_WRONG]
- **Q:** Trace the supersession history of the Vertex SOW under msa-001.
- **expected:** SOW dated <date> issued under the MSA; system should reference current MSA effective version and SOW status.
- **issue:** The documents do not contain information about the supersession history of the Vertex SOW under msa-001. The SOW document mentions it is issued under a 'Vendor / SaaS Procurement Agreement' dated March 20, 2024, and msa-001-amendment-2 refers to a 'Master Services Agreement' dated March 15, 2024, but neither document traces the supersession history of any SOW.
- **corpus actually says:** The Vertex SOW under msa-001 has a supersession history where the original payment terms of NET-30 were superseded by Amendment No. 1, and then Amendment No. 2, which established NET-60 as the current payment terms.

## legal-q019 [NEEDS_REVIEW]
- **Q:** Which is the authoritative NDA between Acme and Vertex — mutual or unilateral?
- **expected:** Mutual NDA dated <later date> supersedes the earlier unilateral; current effective NDA is the mutual one.
- **issue:** The expected answer states the mutual NDA supersedes the unilateral one, but the document explicitly states the unilateral NDA supplements and does not supersede the mutual NDA.
- **corpus actually says:** The Unilateral Non-Disclosure Agreement dated October 22, 2024, supplements (and does not supersede) the Mutual Non-Disclosure Agreement dated January 8, 2024. Therefore, both are authoritative, with the unilateral one adding specific terms for a particular evaluation.

## legal-q021 [ANSWER_WRONG]
- **Q:** What is the indemnity cap that applies to the Acme-Vertex relationship?
- **expected:** Per Amendment 2 (current): <amended cap>. Original MSA had a different cap which has been superseded.
- **issue:** The expected answer claims Amendment 2 has an amended cap, but Amendment 2 does not mention or amend the indemnity cap. The expected answer also does not state the actual cap from the original MSA.
- **corpus actually says:** The original MSA (msa-001-acme-vertex) states in Section 5.1 that each party's total liability, with exceptions, shall not exceed the greater of (i) $1,000,000 or (ii) the fees paid or payable by Customer to Vendor in the twelve (12) months preceding the event giving rise to the claim. Amendment 2 (

## legal-q025 [CITATION_WRONG]
- **Q:** Two different lease addendums show different rent values for the Acme Gurugram office. Which is authoritative?
- **expected:** The latest signed addendum is authoritative; system must surface the lineage and the current value.
- **issue:** The provided document is a lease agreement, not an addendum, and it does not mention different rent values or which document is authoritative in case of discrepancies.
- **corpus actually says:** The authoritative lease addendum for the Acme Gurugram office is lease-001-acme-lakeview, which states a monthly rent of INR 27,75,000. The document acme-corp-360-view explicitly states that it is a synthesis and not an authoritative source for specific facts.

## legal-q022 [ANSWER_WRONG]
- **Q:** Which NDA terms govern: the mutual NDA or the unilateral NDA between Acme and Vertex?
- **expected:** The mutual NDA, executed later, supersedes the unilateral. Mutual = 2-year term, mutual obligations.
- **issue:** The expected answer states the mutual NDA supersedes the unilateral NDA, but the unilateral NDA explicitly states it 'supplements (and does not supersede)' the mutual NDA.
- **corpus actually says:** The Unilateral Non-Disclosure Agreement, effective October 22, 2024, supplements and does not supersede the Mutual Non-Disclosure Agreement dated January 8, 2024. Therefore, both NDAs govern.

## legal-q030 [ANSWER_WRONG]
- **Q:** Does any Acme contract include unlimited liability?
- **expected:** Reviewed; the Acme-Vertex contracts cap liability; the Acme-Nimbus MSA has uncapped liability for gross negligence / wilful misconduct (rare carve-out).
- **issue:** The expected answer claims uncapped liability for gross negligence/wilful misconduct in the Acme-Nimbus MSA, but the document explicitly caps all liability, including for confidentiality breaches and IP indemnification, and does not mention any carve-out for gross negligence or wilful misconduct.
- **corpus actually says:** The Acme-Nimbus MSA caps liability at the greater of SGD 2,000,000 or the fees paid/payable in the preceding 12 months, except for breaches of confidentiality or IP indemnification, which are also subject to this cap. There is no mention of uncapped liability for gross negligence/wilful misconduct.

## legal-q032 [ANSWER_WRONG]
- **Q:** Does any Acme contract create a perpetual obligation?
- **expected:** Yes — the Acme-Orion licence includes a perpetual obligation to maintain audit-trail records of usage.
- **issue:** The provided document, license-001-acme-orion-mfn, does not contain any mention of a perpetual obligation to maintain audit-trail records of usage.
- **corpus actually says:** I cannot answer this question. None of the provided documents contain information about any Acme contract creating a perpetual obligation.

## legal-q023 [ANSWER_WRONG]
- **Q:** Are there any conflicting termination notice periods across Acme's contracts?
- **expected:** Multiple termination periods exist (30 / 60 / 90 day) across contracts; each contract is independently authoritative.
- **issue:** The expected answer mentions 30, 60, and 90-day termination periods, but the documents only explicitly state a 90-day notice for convenience and a 30-day cure period for material breach. A 60-day termination period is not found.
- **corpus actually says:** The 'msa-002-acme-nimbus' document specifies a 90-day notice period for termination for convenience by the customer. The 'license-001-acme-orion-mfn' document mentions a 30-day cure period for termination due to material breach. The 'msa-001-amendment-2' document amends the term of the agreement but

## legal-q033 [ANSWER_WRONG]
- **Q:** Find any clauses requiring binding mediation BEFORE arbitration.
- **expected:** Pre-arbitration mediation clauses exist in some contracts (atypical); system should surface specific occurrences.
- **issue:** The expected answer suggests the system should surface specific occurrences of pre-arbitration mediation clauses, but the provided document does not contain any such clauses.
- **corpus actually says:** The document specifies that any dispute shall be referred to and finally resolved by arbitration administered by the Singapore International Arbitration Centre (SIAC) under the SIAC Rules then in force. It does not mention any requirement for binding mediation before arbitration.

## legal-q026 [CITATION_WRONG]
- **Q:** Which indemnity-cap clause prevails between the Acme-Nimbus MSA and a later side letter?
- **expected:** Side letter (later in time) prevails, IF it explicitly amends the indemnity clause; otherwise MSA controls.
- **issue:** The provided documents do not contain information about which indemnity-cap clause prevails between the Acme-Nimbus MSA and a later side letter. The side letter provided is between Helios Partners LLP and Acme Corp Pvt Ltd, not Acme Corp Pvt Ltd and Nimbus Technologies Pte Ltd.
- **corpus actually says:** The side letter (msa-001-side-letter) prevails, increasing the IP indemnification cap to $5,000,000 per contract year, notwithstanding the lower cap in Section 5.1 of the MSA.

## legal-q035 [CITATION_WRONG]
- **Q:** Total number of contracts in the legal domain by document type.
- **expected:** MSAs: 8 / NDAs: 8 / Employment: 8 / Leases: 5 / Vendor agreements: 5 / SOWs: 5 / Licences: 4 / Side letters: 2 / Opinions: 3 / Email threads: 3
- **corpus actually says:** Acme is a counterparty in 8 contracts in the legal domain: msa-001-acme-vertex, msa-001-amendment-1, msa-001-amendment-2, msa-002-acme-nimbus, nda-mutual-001-acme-vertex, license-001-acme-orion-mfn, sow-001-acme-vertex, and employment-contract-001-acme.

## legal-q037 [ANSWER_WRONG]
- **Q:** What is the total contract value across all live Acme MSAs?
- **expected:** (Sum of contract values where stated; some contracts are subscription-based without a fixed total, which should be noted as such.)
- **issue:** The expected answer implies that contract values might be stated or that some are subscription-based without a fixed total, but the documents do not provide a total contract value for any of the MSAs. They only refer to 'Fees' and 'annual Fees' without specifying a total contract value.
- **corpus actually says:** The documents do not state a total contract value for either MSA. MSA-001-amendment-2 mentions annual fees exceeding $750,000 for a volume discount, and MSA-002-acme-nimbus mentions a liability cap of SGD 2,000,000 or fees paid/payable in the preceding 12 months, but neither specifies a total contra

## legal-q048 [NEEDS_REVIEW]
- **Q:** What is the criminal-record clause in the Acme employment contract?
- **expected:** Standard background-check provision exists, but no specific criminal-record-disclosure clause is present.
- **corpus actually says:** The Acme employment contract includes a clause for immediate termination if an employee is convicted of a criminal offense.

## legal-q039 [CITATION_WRONG]
- **Q:** Summarise the obligations of Acme under the Acme-Nimbus MSA.
- **expected:** (Multi-paragraph summary covering SLA, payment, IP, indemnity, term, termination, confidentiality.)
- **issue:** The question asks for the obligations of Acme, but the provided document primarily details the obligations of Nimbus (e.g., service levels, data residency, IP indemnification). The only explicit obligations for Acme mentioned are payment of fees (Section 3.1, 3.3) and protecting Nimbus's confidential information (Section 8.1).
- **corpus actually says:** Acme's obligations under the Acme-Nimbus MSA are to pay for services and to classify incidents. The MSA includes a 4-hour delivery SLA for Severity-1 incidents, and if Nimbus Technologies breaches this SLA, Acme is entitled to a 15% service credit on the next invoice.
