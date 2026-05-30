---
doc_id: complaint-001-desai-resolution
doc_type: customer_complaint
effective_date: 2025-03-08
parties: [Aniket Desai, HDFC Bank Ltd]
status: live
chain_id: chain_acme_complaint_resolution
parent_doc: complaint-001-desai-initial
---

# CUSTOMER COMPLAINT — FINAL RESOLUTION

**Reference:** HDFC-GRV-2025-02-1842 (final response)
**Date:** 8 March 2025 (within 30-day statutory deadline)
**Mode of communication:** Registered email + SMS + signed PDF dispatched by courier

---

## Final Resolution Summary

After completing root-cause investigation, the Bank confirms the following:

**(1) Both wire transactions (HDFC-WIR-2025-02-184228 dated 6 Feb 2025 AND HDFC-WIR-2025-02-184229 dated 7 Feb 2025) were duplicate processing of a single customer-authorised instruction.**

**(2) The duplicate (the 7 Feb 2025 transaction) was caused by an internal system fault.**

**(3) The Bank accepts full responsibility and is reversing the duplicate transaction in favour of the customer.**

---

## Investigation Findings (Root Cause Analysis)

The Bank's IT + Operations teams conducted a joint root-cause analysis (RCA reference: HDFC-INC-2025-02-218). Findings:

1. On 6 February 2025 at 14:18 IST, customer initiated a wire transfer of USD 142,000 via NetBanking corporate portal. Transaction processed normally; SMS + email confirmations sent.

2. The transaction was queued for SWIFT execution. SWIFT message MT103 was generated and dispatched at 14:22 IST.

3. **System bug identified:** Due to a defect in the wire-processing batch job (introduced in software release v4.18.2 deployed 5 Feb 2025, one day prior), certain SWIFT-acknowledged transactions were incorrectly **re-queued** for processing after the SWIFT acknowledgement received post-cutoff time. This caused **6 transactions across the Mumbai cluster (including the customer's)** to be erroneously processed a second time on 7 Feb 2025 morning.

4. The duplicate transactions did not trigger SMS/email confirmation because the customer's authorisation timestamp was inside a 24-hour window, and the duplicate-prevention layer correctly treated it as the original transaction (incorrect logic — should have prevented the re-queue itself, not relied on notification suppression).

5. **Of the 6 affected transactions** (cluster-wide):
   - 4 were detected by automated reconciliation on 7 Feb evening; recall instructions were issued automatically.
   - 2 (including the customer's) were not detected because the receiving bank had already credited the beneficiary before the recall message arrived.

6. **For the customer's transaction:**
   - Recall instruction was sent to receiving bank (Citibank NY) at 13:18 IST on 11 Feb 2025 (after the customer raised the dispute) — too late, already credited to Hudson Bay Holdings LLC.
   - Beneficiary (Hudson Bay Holdings LLC) confirmed receipt of duplicate USD 142,000 on 11 Feb 2025 17:42 IST and offered to return the duplicate funds.
   - Beneficiary returned USD 141,728.42 (net of intermediary bank fees) on 13 Feb 2025.

---

## Action Taken — Financial

The Bank has credited the customer's account on **5 March 2025** with:

| Item | Amount (INR) | Note |
|---|---|---|
| Refund of duplicate USD 142,000 wire | 1,17,72,400 | Converted at 6-Feb-2025 rate (USD/INR 82.90) — at customer's option |
| Intermediary bank fee reimbursement | 22,584 | Citibank NY + correspondent fees |
| Compensation interest @ 8% p.a. for 21 days | 54,184 | Per RBI Compensation Policy (Customer Service Annex II) |
| Goodwill compensation | 50,000 | Per HDFC's Internal Ombudsman Scheme — discretionary, given the prolonged inconvenience to a HNW customer |
| **Total credit** | **1,18,99,168** | |

Refund + compensation transferred via NEFT credit reference: HDFC-COMP-2025-03-184228 dated 5 Mar 2025 10:42 IST.

---

## Action Taken — Operational + Technical

1. **System fix:** Software defect identified + patched in release v4.18.3 deployed cluster-wide on 22 Feb 2025. Patch verified by independent QA team.

2. **Process changes:** Wire-processing batch job now includes idempotency-key checking on every queue read (not only at queue write). Reconciliation cadence increased from daily to every 4 hours for cross-border wires.

3. **Other affected customers:** All 5 other customers affected by the same defect have been similarly compensated. No customer was left out-of-pocket.

4. **Regulatory reporting:** The incident has been reported to the RBI's Cyber Security & IT Risk Group via the prescribed monthly report (filed 5 Mar 2025).

5. **Internal accountability:** The root-cause review identified one inadequately tested code change as the proximate cause. The release process has been reinforced with mandatory end-to-end pre-production testing for any change touching wire processing.

---

## Customer Response

The Bank's Grievance Officer (Ms. Smita Sharma) personally called the customer on 6 March 2025 to confirm receipt of refund + explain the resolution. The customer:

(i) Acknowledged receipt of refund + compensation;
(ii) Accepted the explanation of root cause;
(iii) Did NOT pursue escalation to Banking Ombudsman;
(iv) Requested (informally) a brief written letter from the Branch Head + Cluster Compliance, which is attached to this resolution.

Per customer's standing request, this matter is considered **closed**.

---

## Closure

| Field | Value |
|---|---|
| Complaint status | Closed — Satisfied |
| Closure date | 8 March 2025 |
| Days taken | 24 days (well within 30-day SLA) |
| Customer satisfaction (1-5) | 4 / 5 (per follow-up call) |
| Reopen window | 30 days (until 7 April 2025); after which complaint is permanently archived |

---

**Approved (Cluster Compliance Officer):** *Smita Sharma*
**Approved (Branch Head):** *Anuradha Kapoor*
**Approved (Internal Ombudsman, Mumbai zone):** *Ravi Ramamurthy*

Date: 8 March 2025
Place: Mumbai
