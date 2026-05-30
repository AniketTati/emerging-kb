---
doc_id: sow-002-acme-nimbus
doc_type: statement_of_work
chain: chain_acme_nimbus_sow
parent_doc: msa-002-acme-nimbus
effective_date: 2025-02-10
parties: [Acme Corp Pvt Ltd, Nimbus Technologies Pte Ltd]
governing_law: Singapore
status: live
---

# STATEMENT OF WORK NO. 2 — DISASTER-RECOVERY REGION

Under the MSA between **Acme Corp Pvt Ltd** and **Nimbus Technologies Pte Ltd** dated **June 1, 2024**. This SOW supplements SOW No. 1 dated June 20, 2024.

**SOW Effective Date:** February 10, 2025.

## 1. Scope

1.1 Establish a fully-warm disaster-recovery region in Mumbai (`ap-south-1-dr`) mirroring the existing Singapore production environment built under SOW No. 1.

1.2 Set up replication, failover automation, and runbooks for a 4-hour RPO / 8-hour RTO target.

## 2. Deliverables

1. **D1:** Mumbai region provisioning (VPC + Kubernetes + PostgreSQL streaming replica).
2. **D2:** Storage replication (cross-region) + IAM mirroring.
3. **D3:** Automated failover scripts + runbooks.
4. **D4:** End-to-end DR exercise + sign-off.

## 3. Fees

3.1 **Fixed-fee:** SGD 240,000, milestone-billed (25% each).

3.2 Recurring infrastructure costs: pass-through + 8% management fee per the MSA.

3.3 Payment terms: NET-45.

## 4. Service Levels

4.1 Once cutover, the DR region inherits the 4-hour Severity-1 SLA from the MSA.

4.2 RPO target: 4 hours; RTO target: 8 hours, validated quarterly.

## 5. Term

5.1 Build phase: 8 weeks.

5.2 Ongoing managed services merged with SOW No. 1 thereafter.

---

**ACME CORP PVT LTD** — Robert Sharma, CEO — February 10, 2025

**NIMBUS TECHNOLOGIES PTE LTD** — Kenji Tanaka, Managing Director — February 10, 2025
