---
doc_id: sow-001-acme-nimbus
doc_type: statement_of_work
chain: chain_acme_nimbus_sow
parent_doc: msa-002-acme-nimbus
effective_date: 2024-06-20
parties: [Acme Corp Pvt Ltd, Nimbus Technologies Pte Ltd]
governing_law: Singapore
status: live
---

# STATEMENT OF WORK NO. 1

**Under the Master Services Agreement** between **Acme Corp Pvt Ltd** ("Customer") and **Nimbus Technologies Pte Ltd** ("Vendor") dated **June 1, 2024** (the "MSA").

**SOW Effective Date:** June 20, 2024

---

## 1. Description of Services

1.1 Build-out of Customer's production cloud infrastructure in Nimbus's Singapore region (`ap-southeast-1-prod`), including:
- (a) 3-availability-zone VPC with peering to Customer's existing Mumbai region;
- (b) Kubernetes cluster (50 worker nodes) with autoscaling, ServiceMesh (Istio), and observability (Prometheus + Grafana + Loki);
- (c) Managed PostgreSQL 17 cluster (1 primary + 2 read replicas, 8TB SSD each);
- (d) Object storage (S3-compatible) — 200 TB initial allocation;
- (e) Vault deployment for secrets management;
- (f) Backup + restore automation to a separate region.

## 2. Deliverables

1. **Milestone M1 (week 2):** VPC + networking complete, peering tested. — Acceptance criteria: documented network diagrams; peering latency <30ms.
2. **Milestone M2 (week 5):** Kubernetes cluster operational; demo application deployed end-to-end.
3. **Milestone M3 (week 8):** Database + storage provisioned and load-tested.
4. **Milestone M4 (week 11):** Backup/restore automation validated via DR exercise.
5. **Milestone M5 (week 12):** Production cutover with sign-off.

## 3. Fees

3.1 **Fixed-fee component:** SGD 380,000, milestone-billed (15% / 20% / 20% / 20% / 25%).

3.2 **Recurring infrastructure costs:** Pass-through at cost + 8% management fee, invoiced monthly.

3.3 Payment terms per the MSA (NET-45).

## 4. Service Levels

4.1 The 4-hour Severity-1 Production SLA in Section 2.2 of the MSA applies to all production-grade environments built under this SOW upon production cutover (Milestone M5).

## 5. Term

5.1 SOW project term: **12 weeks** from the SOW Effective Date.

5.2 Ongoing managed services continue per Section 3.2 until terminated under the MSA.

## 6. Counterparties' Acceptance

**ACME CORP PVT LTD** — Robert Sharma, CEO — June 20, 2024

**NIMBUS TECHNOLOGIES PTE LTD** — Kenji Tanaka, Managing Director — June 20, 2024
