---
doc_id: statement-006-suspicious-aml-pattern
doc_type: bank_statement
effective_date: 2025-03-22
parties: [Sterling Trade Enterprises, Federal Bank Ltd]
status: live
stressors: [rare_aml_structured_transactions, conflict_aml_threshold]
---

# FEDERAL BANK — STATEMENT OF CURRENT ACCOUNT (AML REVIEW COPY)

**Account Holder:** Sterling Trade Enterprises (Proprietorship)
**Proprietor:** Mr. Vikram Sterling
**Address:** 4th Floor, Phoenix Tower, MG Road, Bengaluru - 560001
**Account No.:** FED-CA-440018822
**Branch:** MG Road, Bengaluru
**Statement period:** 15 March 2025 - 22 March 2025
**Generated:** 22 March 2025 (AML compliance escalation copy)

## ⚠ AML REVIEW STATUS

This account has been **flagged for review** by the Bank's Compliance / AML unit (Reference: AML-CASE-2025-03-1882). Reason: **possible structuring of transactions to evade INR 10,00,000 CTR (Cash Transaction Report) reporting threshold**.

This statement copy has been generated for the AML investigator (Ms. Priya Mohan) and forwarded to FIU-IND for review per PMLA 2002.

## Account Balance

- Opening (15 Mar 2025): INR 8,42,180.00
- Closing (22 Mar 2025): INR 12,84,420.00
- Total credits during period: INR 67,40,000.00
- Total debits during period: INR 62,97,760.00

## Transactions (chronological)

| Date | Time | Description | Debit | Credit | Channel | Balance |
|---|---|---|---|---|---|---|
| 17-Mar-2025 | 10:42 | Cash deposit (Branch) | — | 9,50,000 | Counter | 17,92,180 |
| 17-Mar-2025 | 11:18 | Cash deposit (Branch) | — | 9,50,000 | Counter | 27,42,180 |
| 17-Mar-2025 | 11:54 | Cash deposit (Branch) | — | 9,50,000 | Counter | 36,92,180 |
| 17-Mar-2025 | 13:22 | Cash deposit (Branch) | — | 9,50,000 | Counter | 46,42,180 |
| 17-Mar-2025 | 14:08 | Cash deposit (Branch) | — | 9,50,000 | Counter | 55,92,180 |
| 17-Mar-2025 | 14:48 | Cash deposit (Branch) | — | 9,50,000 | Counter | 65,42,180 |
| 17-Mar-2025 | 15:32 | Cash deposit (Branch) | — | 9,50,000 | Counter | 74,92,180 |
| 18-Mar-2025 | 09:18 | NEFT outward — "Mahalaxmi Trading Co" | 22,40,000 | — | NEFT | 52,52,180 |
| 18-Mar-2025 | 09:42 | NEFT outward — "Aryan Logistics Pvt Ltd" | 18,20,000 | — | NEFT | 34,32,180 |
| 18-Mar-2025 | 10:12 | NEFT outward — "Bharat Holdings (UAE)" — return: invalid IBAN | 22,37,760 | — | NEFT (returned) | 11,94,420 |
| 19-Mar-2025 | 14:22 | NEFT inward — return of failed transfer | — | 22,37,760 | NEFT | 34,32,180 |
| 19-Mar-2025 | 15:08 | RTGS outward — "Mahalaxmi Trading Co" | 18,40,000 | — | RTGS | 15,92,180 |
| 20-Mar-2025 | 11:18 | Card swipe — Phoenix Mall (POS) | 24,400 | — | POS | 15,67,780 |
| 20-Mar-2025 | 12:48 | UPI — "vikram@paytm" to personal | 1,42,000 | — | UPI | 14,25,780 |
| 21-Mar-2025 | 16:38 | Cheque deposit — drawn on HDFC Bombay | — | 84,000 | Cheque | 15,09,780 |
| 22-Mar-2025 | 09:18 | RTGS outward — "Mahalaxmi Trading Co" | 2,25,360 | — | RTGS | 12,84,420 |

## AML Analyst Notes (Priya Mohan, Compliance Officer)

**Pattern observed:** On **17 March 2025**, the account received **7 cash deposits, each of exactly INR 9,50,000**, all between 10:42 and 15:32 (a single business day, single branch). Total deposited: INR 66,50,000. **Each deposit is intentionally below the INR 10,00,000 single-transaction CTR threshold** under PMLA Rule 3.

**Cross-reference:** The same proprietor's KYC records show:
- Declared monthly turnover: INR 8-12 lakh
- Declared business: "Trading in textiles + commodities"
- The Mar 17 single-day cash deposit (INR 66.5 lakh) is ~6× the declared monthly turnover

**Beneficiary chain:**
- 5 of 7 cash deposits onward-transferred (within 24 hrs) to "Mahalaxmi Trading Co" (same beneficiary across multiple wires) and to "Aryan Logistics Pvt Ltd" (one wire).
- One wire attempted to Bharat Holdings (UAE) — returned for invalid IBAN.

**Tentative conclusion (pre-FIU review):** Pattern is consistent with **structured deposits ("smurfing")** to evade CTR reporting, with downstream layering through known shell counterparties. Funds may originate from undisclosed source.

**Action taken:**
- Account placed on **debit-freeze hold** pending FIU response (effective 22 Mar 2025 17:00)
- Suspicious Transaction Report (STR) filed with FIU-IND on 22 Mar 2025
- Account holder notified via registered letter + email (delivery confirmed)

## Threshold Reference (relevant regulation)

| Source | Cash transaction threshold |
|---|---|
| PMLA Rules 2005 Rule 3(B) | INR 10,00,000 single transaction OR aggregate of INR 10,00,000 in connected transactions in a month |
| RBI Master Direction on KYC (2016, as amended 2024) | INR 10,00,000 OR aggregate of connected transactions within a month |
| FATF Recommendation 11 | "structuring" of transactions below reporting threshold is itself a red flag |

**Note (Conflict):** In FY 2024 internal compliance bulletin (FED-COMP-2024-08), the bank's risk threshold for internal review was tightened to **INR 5,00,000 per cash deposit** (50% of the regulatory threshold). The current case far exceeds either threshold.

---

**Federal Bank Ltd — AML & Compliance Unit**
Reviewed: Priya Mohan, Sr. Compliance Officer | Approved escalation: Rakesh Sundaram, AML Head
Distribution: AML file + FIU-IND submission only. **DO NOT** distribute to branch staff or account holder.
