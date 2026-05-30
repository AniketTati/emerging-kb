# finance — genuine eval issues needing a human decision

4 queries could not be grounded to any corpus doc (real answer bugs or corpus gaps).

## finance-q009 [ANSWER_WRONG]
- **Q:** Who is the audit partner on Marigold Pharma's FY 2024-25 audit?
- **expected:** Ernst & Young LLP (per 10-K).
- **issue:** The document identifies the audit firm but not the specific audit partner, which is what the question asks for.
- **corpus actually says:** The document states that Ernst & Young LLP are the statutory auditors and issued an unqualified opinion, but it does not mention the name of the audit partner.

## finance-q035 [ANSWER_WRONG]
- **Q:** Total HDFC fee income from Acme per annum.
- **expected:** Approximately INR 1.4-1.8 crore per year (per profile-001-acme-corporate).
- **issue:** The expected answer states approximately INR 1.4-1.8 crore, while the document states INR 84 lakh (which is 0.84 crore).
- **corpus actually says:** INR 84 lakh (FY 2024-25 estimate)

## finance-q034 [CITATION_WRONG]
- **Q:** How many wire transactions appear in finance domain threads?
- **expected:** 5 wire threads (wire-001 through wire-005).
- **corpus actually says:** There are 4 wire transactions involving HDFC Bank and 1 wire transaction involving Kotak Mahindra Bank in the finance domain threads, totaling 5 wire transactions.

## finance-q024 [ANSWER_WRONG]
- **Q:** The PMLA rule says INR 10 lakh CTR threshold; HDFC's internal compliance bulletin says INR 5 lakh. Which is operative?
- **expected:** Internal threshold (INR 5 lakh) is the OPERATIVE compliance trigger; regulatory minimum is INR 10 lakh. Bank applies the tighter internal threshold for review while regulatory reporting follows the INR 10 lakh PMLA threshold.
- **issue:** The expected answer states HDFC's internal compliance bulletin, but the document refers to Federal Bank's internal compliance bulletin.
- **corpus actually says:** The PMLA rule and RBI Master Direction on KYC both state an INR 10,00,000 single transaction or aggregate threshold. Federal Bank's internal compliance bulletin (FED-COMP-2024-08) tightened its internal risk threshold for review to INR 5,00,000 per cash deposit.
