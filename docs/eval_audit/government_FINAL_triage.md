# government — genuine eval issues needing a human decision

10 queries could not be grounded to any corpus doc (real answer bugs or corpus gaps).

## government-q011 [ANSWER_WRONG]
- **Q:** What is the location of the Sector 18 road tender (CIDCO)?
- **expected:** Navi Mumbai Sectors 47-49 (Pushpak Greenfield Township).
- **issue:** The expected answer states the location is Navi Mumbai Sectors 47-49 (Pushpak Greenfield Township), but the document specifies Navi Mumbai Sector 14.
- **corpus actually says:** The document states the tender is for resurfacing roads in Navi Mumbai Sector 14.

## government-q003 [CITATION_WRONG]
- **Q:** What is the CIDCO tender T-018 estimated value?
- **expected:** INR 184 crore (excluding GST).
- **issue:** The document does not contain information about tender T-018; it details tender CIDCO/T-2025/NV-RR-014.
- **corpus actually says:** The provided document is for tender CIDCO/T-2025/NV-RR-014, not T-018. The estimated cost for tender CIDCO/T-2025/NV-RR-014 is INR 5.20 crore (excluding GST).

## government-q010 [CITATION_WRONG]
- **Q:** What is the consenting authority for the PMAY Beed scheme?
- **expected:** Office of the District Magistrate, Beed + Maharashtra State Housing Department.
- **issue:** The document details fund allocation and implementation responsibilities for the PMAY-G scheme in Beed but does not specify a 'consenting authority'.
- **corpus actually says:** The Government of Maharashtra, General Administration Department, is the consenting authority for the PMAY Beed scheme.

## government-q022 [ANSWER_WRONG]
- **Q:** Two RTI responses give different counts for non-Aadhaar ration cards in Beed. Which is authoritative?
- **expected:** The latest-dated, signed welfare audit is authoritative. Earlier counts are interim.
- **issue:** The provided document does not contain information about two different RTI responses or which one is authoritative. It only provides a single welfare audit report.
- **corpus actually says:** The document "RATION-CARD-WITHOUT-LINKED-AADHAAR ANOMALY LIST — BEED DISTRICT" from the Department of Food, Civil Supplies and Consumer Protection — Government of Maharashtra, dated 28 July 2025, states that there are 3,514 ration cards in Beed district without linked Aadhaar. This document is autho

## government-q021 [ANSWER_WRONG]
- **Q:** The land record shows Kamla Jadhav as owner of Survey 47/3; the mutation register shows a different owner. How to resolve?
- **expected:** The chain of mutations determines current owner. System should walk the chain to find latest entry.
- **issue:** The provided documents do not contain information on how to resolve a discrepancy between a land record and a mutation register regarding ownership. They only provide examples of land records and mutation orders.

## government-q024 [CITATION_WRONG]
- **Q:** The FIR says INR 24 cr value; the police investigation report later revises to INR 18 cr. Which prevails for prosecution?
- **expected:** Investigation report (post-FIR) typically refines the value; the prosecution will rely on the investigation finding. FIR is initial complaint.
- **issue:** The provided document is an FIR and does not contain any information about a police investigation report revising the value or which document prevails for prosecution.
- **corpus actually says:** The FIR value of INR 24 crore prevails for prosecution.

## government-q015 [ANSWER_WRONG]
- **Q:** Walk the Kamla Jadhav mutation chain on Survey 47/3.
- **expected:** (Mutation application → consideration → final entry in 7/12 record.)
- **issue:** The expected answer provides a simplified three-step chain (application, consideration, final entry), while the document details a more comprehensive process including application, legal verification (which encompasses checking for objections), and the final mutation order that updates the title register.
- **corpus actually says:** The mutation chain involves: application filing, legal verification (including checking for objections), and the issuance of a mutation order which removes the deceased owner and adds the legal heirs to the title register, effective from the order date.

## government-q025 [CITATION_WRONG]
- **Q:** An old land record + a new mutation register entry disagree on the owner. How to resolve?
- **expected:** Mutation register (latest entry) controls. Old land record is historical.
- **issue:** The provided documents are examples of land records and mutation orders but do not contain any general rule or guidance on how to resolve a disagreement between an old land record and a new mutation register entry regarding ownership. They only show specific instances of mutations.
- **corpus actually says:** When an old land record (7/12 extract) shows a deceased owner and a new mutation register entry reflects new ownership, the new mutation order supersedes the old record. The mutation order, issued by the Tehsildar, legally removes the deceased owner's name and adds the legal heirs in their respectiv

## government-q038 [CITATION_WRONG]
- **Q:** Provide an English-language summary of the Marathi-only 7/12 land record.
- **expected:** (Translated English summary of the 1989 Marathi 7/12 — preserving owner, area, mutations history.)
- **corpus actually says:** The 7/12 extract for Survey No. 47/3 in Vadi Bhosalewadi village (Beed taluka, Beed district) details 1.24 hectares of irrigated land used for sugarcane and bajra. The land was held by the deceased Vasantrao Bapu Jadhav, and the mutation to his legal heirs (including widow Kamla Jadhav) is pending.

## government-q033 [CITATION_WRONG]
- **Q:** Total amount across all govt-tender contracts in the corpus.
- **expected:** (Aggregate sum of tender values: CIDCO + others.)
- **corpus actually says:** The total amount across all government-tender contracts in the corpus is INR 62.20 crore for the PMAY Beed allocation, INR 5.14 crore for the CIDCO tender (L2 bidder), INR 24 crore for a land dispute FIR, and INR 176.42 crore for Mahalaxmi's bid on the CIDCO T-018 tender.
