---
doc_id: audit-001-acme-internal-q3
doc_type: audit_report
effective_date: 2024-12-20
parties: [Acme Corp Pvt Ltd, Mehta + Associates LLP]
status: live
---

# INTERNAL AUDIT REPORT — Q3 FY 2024-25 (October-December 2024)

**Engagement:** Internal audit of Treasury + Finance functions
**Audit firm:** Mehta + Associates LLP (Internal Auditors of Acme Corp)
**Audit team:** Mr. Naveen Mehta (Partner), Ms. Ayesha Patel (Manager), Mr. Suresh Kapoor (Senior Associate)
**Audit period:** 1 October 2024 - 31 December 2024
**Report date:** 20 December 2024
**Distribution:** Audit Committee (Mr. Sandeep Joshi — Chair); CFO (Ms. Jayanti Iyer); CEO (Mr. Rakesh Sundaram); Treasurer (Mr. Vikas Acharya); Statutory Auditor (KPMG India) for cross-reference

---

## 1. Engagement Scope

Per Acme's Internal Audit Charter + Audit Committee approval (resolution dated 14 August 2024), the Q3 FY 2024-25 audit focused on:

1.1 Treasury operations + cash management
1.2 Cross-border wire transfers + FEMA compliance
1.3 Counterparty credit-limit adherence
1.4 Bank reconciliation processes
1.5 Loan compliance (HDFC term loan covenants — per loan-001 + addenda)
1.6 Information system access controls (treasury systems)
1.7 Follow-up on prior audit findings (Q2 FY 2024-25 report dated 22 September 2024)

---

## 2. Overall Audit Opinion

**Satisfactory with observations.** The Company's treasury + finance operations are generally well-controlled. We identified **2 high-priority observations** and **5 medium-priority observations**, detailed below. Of the 8 observations from the prior quarter (Q2 FY24-25), **6 have been remediated** and **2 are in progress**.

---

## 3. Detailed Findings

### Finding 3.1 (HIGH) — Authorised Signatory List Outdated

**Observation:** The signatory list maintained by HDFC Bank (Gurugram Sector 18) was last updated by Acme on 14 March 2023. The list includes 2 individuals who are no longer with the Company:

- Mr. Rohit Bhansali — separated 18 August 2023 (treasury manager)
- Ms. Anita Doshi — separated 22 February 2024 (assistant treasurer)

**Risk:** Operational + fraud risk. While both individuals are former employees in good standing and there is no evidence of any unauthorised transactions, the failure to revoke their bank signatory authority is a control deficiency.

**Root cause:** No formal off-boarding procedure currently includes "revoke external bank signatory" as a checklist item.

**Recommendation:** Update HDFC signatory list within 7 working days. Add "revoke bank signatory" to HR's exit checklist. Quarterly cross-check between HR active-employee list + bank signatory lists.

**Management response:** Accepted. Updates submitted to HDFC on 22 December 2024 (in progress). HR exit checklist updated effective 1 January 2025.

### Finding 3.2 (HIGH) — Wire Transfer Dual-Authorisation Bypass

**Observation:** During October 2024, we identified **3 wire transfers** (aggregate USD 4.2 million) that were executed with single-signatory authorisation only, despite the Treasury Policy requiring dual-signatory authorisation for any USD transfer > USD 100,000.

The transactions:

| Date | Amount | Beneficiary | Approver |
|---|---|---|---|
| 14-Oct-2024 | USD 2.2M | Nimbus Technologies Pte Ltd (Singapore) | Treasurer alone (CFO travel) |
| 22-Oct-2024 | USD 1.2M | Nimbus Technologies Pte Ltd (Singapore) | Treasurer alone |
| 28-Oct-2024 | USD 0.8M | Pingo Labs Ltd (UK) | Treasurer alone |

**Risk:** Control failure. Single-signature exceptions are not permitted under Treasury Policy, regardless of operational urgency or CFO availability.

**Root cause:** In October, the CFO (Ms. Iyer) was on extended business travel for 18 days; the back-up signatory designation was unclear (CEO available but treasury policy requires CFO or designated alternate, not CEO).

**Recommendation:** (i) Designate a formal back-up signatory who is not the CEO (e.g., VP Finance) so that dual-authorisation never depends on CFO/CEO availability alone. (ii) For the 3 transactions identified, obtain after-the-fact ratification from the CFO + Audit Committee. (iii) If the Treasury Policy is intentionally to be relaxed (e.g., emergency provision), this must be documented in a policy amendment with Board approval.

**Management response:** Accepted. CFO has ratified the 3 transactions in writing (dated 18 December 2024). Designating Ms. Priya Iyer (VP Legal) as alternate dual-signatory effective 1 January 2025 — Board resolution to follow at next meeting.

### Finding 3.3 (MEDIUM) — Bank Reconciliation Lag

**Observation:** Bank reconciliations for the HDFC operating account are performed monthly, with a typical lag of 12-18 days after month-end. The reconciliation for September 2024 was completed on 22 October 2024 (22 days after month-end).

**Risk:** Reconciliation lag delays detection of unauthorised + duplicate transactions.

**Recommendation:** Move to bi-weekly reconciliation (target: complete reconciliation within 7 days of month-end).

**Management response:** Accepted. Treasury team will move to bi-weekly reconciliation effective Q4 FY 2024-25 (next reconciliation: end Dec 2024 within 7 days = by 7 Jan 2025).

### Finding 3.4 (MEDIUM) — Loan Covenant Reporting Timeliness

**Observation:** Per the HDFC term loan agreement (loan-001), Acme is required to deliver quarterly compliance certificates within **45 days** of each quarter-end. The Q2 FY 2024-25 certificate (for quarter ending Sep 2024) was delivered to HDFC on 18 November 2024 — i.e., 49 days after quarter-end. Technically a breach of the timeliness covenant.

**Risk:** Reportable event under loan covenants. While HDFC's RM has informally acknowledged the delay without escalation, repeated delays could become a default trigger.

**Recommendation:** Internal SLA: Deliver compliance certificates within 30 days of quarter-end (15-day buffer).

**Management response:** Accepted. Treasurer + CFO to ensure 30-day delivery; Q3 FY 2024-25 certificate (for Dec 2024) target delivery: 30 January 2025.

### Finding 3.5 (MEDIUM) — System Access Control

**Observation:** Three former employees still have active credentials to the Treasury Management System (TMS):

- Mr. Bhansali (separated Aug 2023)
- Ms. Doshi (separated Feb 2024)
- Mr. Vikram Rao (separated Jun 2024 — internal transfer, no longer in treasury role)

**Risk:** Access-control + segregation-of-duties violation. While there is no evidence of misuse, residual access is a control deficiency.

**Recommendation:** Revoke all three credentials within 5 working days. Quarterly audit of TMS user list + active-employee cross-check.

**Management response:** Accepted. Credentials revoked 18 December 2024. Quarterly audit added to compliance calendar.

### Finding 3.6 (MEDIUM) — Counterparty Concentration

**Observation:** As of 30 November 2024, total exposure to HDFC Bank (deposits + LC + hedge) was **INR 78 crore**, which represents 79% of Acme's total counterparty exposure of INR 99 crore. Treasury Policy limits HDFC aggregate to INR 100 cr and concentration limit to 70%; therefore the 79% concentration is **a breach** of policy.

**Root cause:** Slow diversification + recent funds inflow from inter-co (Acme USA) deposited entirely into HDFC operating account.

**Recommendation:** Reduce HDFC concentration to <70% within 60 days. Move INR 10-15 cr to ICICI / other approved counterparty.

**Management response:** Accepted. Treasurer initiated INR 10 cr transfer to ICICI Liquid Fund on 19 December 2024. Concentration target: 65% by end of Q4 FY 2024-25.

### Finding 3.7 (MEDIUM) — FX Hedge Documentation

**Observation:** For 2 of 5 FX forwards open at quarter-end, the underlying commitment documentation (purchase orders + budget allocations) was not on file in the Treasury folder. The transactions appear legitimate (matching invoice trail in AP) but the formal hedge-effectiveness documentation was incomplete.

**Risk:** Hedge-accounting effectiveness may be challenged; Ind AS 109 effectiveness testing requires contemporaneous documentation.

**Recommendation:** Implement a hedge-documentation checklist; no forward shall be placed without all supporting documents on file.

**Management response:** Accepted. Checklist + folder structure implemented 22 December 2024.

### Finding 3.8 (LOW) — Petty Cash Reconciliation

**Observation:** Petty-cash reconciliation at the Gurugram office showed a small discrepancy of INR 1,842 (over) at 30 November 2024 count.

**Risk:** Minimal financial risk.

**Recommendation:** Reconciliation cadence increased from monthly to weekly; identify source of over-amount.

**Management response:** Accepted. Investigated — over-amount due to a small refund deposited but not recorded in October 2024. Reconciled + adjusted.

---

## 4. Status of Prior-Quarter (Q2 FY 2024-25) Findings

| Finding ID | Description | Status |
|---|---|---|
| 2.1 (HIGH) | Approval matrix outdated | Remediated |
| 2.2 (HIGH) | Trade reconciliation gaps | Remediated |
| 2.3 (MED) | TMS audit log gaps | Remediated |
| 2.4 (MED) | TDS reconciliation lag | Remediated |
| 2.5 (MED) | Vendor master deduplication | Remediated |
| 2.6 (MED) | Fixed-asset register update | In progress (target Q4 FY24-25) |
| 2.7 (LOW) | Forex desk SLA tracking | In progress (target Q4 FY24-25) |
| 2.8 (LOW) | Stationery overspend | Remediated |

---

## 5. Conclusion + Engagement Letter Compliance

The internal audit team has conducted this engagement in accordance with the Standards on Internal Audit issued by the ICAI. The Company's internal-control environment remains generally effective, with the noted exceptions which are being addressed.

The next internal audit (Q4 FY 2024-25) is scheduled for 18 March 2025.

---

**Signed:**

*Naveen Mehta* — Partner, Mehta + Associates LLP
*Ayesha Patel* — Audit Manager

Date: 20 December 2024
Place: New Delhi
