# healthcare — genuine eval issues needing a human decision

18 queries could not be grounded to any corpus doc (real answer bugs or corpus gaps).

## healthcare-q011 [NOT_IN_CORPUS]
- **Q:** What is the consent statement language for the Apollo MRI consent form?
- **expected:** (Quoted consent language — short quote permitted per copyright rule.)

## healthcare-q026 [NOT_IN_CORPUS]
- **Q:** There's a disagreement between the lab automated reading and the clinician's annotation. Which prevails?
- **expected:** Clinician annotation prevails for clinical decision-making; lab automated value is a measurement; the annotation typically reflects context (timing, medication, etc.).

## healthcare-q027 [NOT_IN_CORPUS]
- **Q:** Are there any patients with critical electrolyte abnormalities?
- **expected:** Yes — Shyam (K+ = 7.2 mmol/L, life-threatening) and Suresh (creatinine 7.8 mg/dL, severe renal compromise).

## healthcare-q033 [NOT_IN_CORPUS]
- **Q:** How many distinct patients appear in the healthcare corpus?
- **expected:** (Aggregated count via entity-resolution; Arjun, Kavya, Shyam, Suresh, Ramesh, Priya Iyer, others.)

## healthcare-q038 [NOT_IN_CORPUS]
- **Q:** Provide a clinical handover summary for Shyam (the K+ 7.2 case).
- **expected:** (Summary including urgency, immediate management, follow-up.)

## healthcare-q006 [ANSWER_WRONG]
- **Q:** What medication was prescribed in Arjun Singh's follow-up?
- **expected:** (Specific Rx — system pulls from the prescription doc.)
- **issue:** The document indicates a continuation of current medications, including Apixaban, but does not mention any new medication being prescribed in the follow-up.
- **corpus actually says:** The document states, "Continue all current medications. Continue Apixaban 5 mg BID." It does not specify what new medication was prescribed.

## healthcare-q008 [ANSWER_WRONG]
- **Q:** What is Apollo Hospitals Pune's NABH accreditation status?
- **expected:** (Per consent or admin doc; NABH accreditation reference cited.)
- **issue:** The provided document is a consent form and does not contain any information about Apollo Hospitals Pune's NABH accreditation status.
- **corpus actually says:** The provided documents do not contain information about Apollo Hospitals Pune's NABH accreditation status.

## healthcare-q009 [NEEDS_REVIEW]
- **Q:** Which hospital did Kavya Reddy's initial encounter occur at?
- **expected:** Apollo Hospitals Pune.
- **issue:** The expected answer states Apollo Hospitals Pune, but the document clearly indicates Manipal Hospital Bangalore.
- **corpus actually says:** Manipal Hospital Bangalore

## healthcare-q021 [CITATION_WRONG]
- **Q:** Two of Arjun Singh's lab reports show different glucose values. Which is current?
- **expected:** The later report (lab-2) is current. The earlier report (lab-1) is historical baseline.
- **issue:** The expected answer refers to 'lab-1' and 'lab-2' and their relative currency, but the provided documents only contain one unique lab report, duplicated twice, with no such distinction or comparison possible.
- **corpus actually says:** The provided documents contain two identical lab reports (lab-001-arjun-singh-cbc-lipid), both dated 2025-09-16. There is no 'lab-2' or 'lab-1' to compare, nor any indication of which report is more current or historical.

## healthcare-q018 [CITATION_WRONG]
- **Q:** If Arjun Singh's lab-2 contradicts lab-1, which is current?
- **expected:** Lab-2 is the current authoritative value; lab-1 is historical baseline. Both should be cited but lab-2 governs current clinical state.
- **issue:** The provided documents are two identical copies of 'lab-001-arjun-singh-cbc-lipid'. There is no 'lab-2' or any other lab report to compare or determine which is current or authoritative.
- **corpus actually says:** The lab results from lab-002 are current, as they were drawn yesterday (November 4, 2025) and reviewed today (November 5, 2025), contrasting with the admission EKG which showed AF.

## healthcare-q025 [NOT_IN_CORPUS]
- **Q:** Two consent forms exist with different terms. Which applies?
- **expected:** Most recently signed consent applies. Earlier consent is superseded but retained for audit trail.
- **corpus actually says:** The provided documents do not contain information on which of two consent forms with different terms applies. They describe individual consent forms for medical procedures and an entity disambiguation for individuals with the same name, but not a scenario where two different consent forms for the sa

## healthcare-q023 [ANSWER_WRONG]
- **Q:** Two physicians' notes give different diagnoses for Arjun Singh. How do we resolve?
- **expected:** Treating physician's note dated later prevails. Earlier note may be a differential diagnosis.
- **issue:** The provided documents are clinical visit notes for Arjun Singh and do not contain any information or guidelines on how to resolve conflicting physician diagnoses, nor do they mention a scenario where two physicians' notes give different diagnoses for Arjun Singh.

## healthcare-q030 [NOT_IN_CORPUS]
- **Q:** Any patients with diagnostic findings flagged as 'extreme' or 'critical'?
- **expected:** Multiple — see Shyam (potassium), Suresh (creatinine), and Kavya (initial TSH).

## healthcare-q036 [CITATION_WRONG]
- **Q:** How many lab reports in the corpus show creatinine > normal?
- **expected:** (Aggregated count; cite specific reports.)
- **corpus actually says:** The document mentions one extreme creatinine value in the corpus, which is 7.8 for Suresh.

## healthcare-q022 [NEEDS_REVIEW]
- **Q:** What is Kavya Reddy's true thyroid status — the elevated TSH or the normal one?
- **expected:** Currently normal (TSH 3.1) on thyroxine replacement. Initial hypothyroidism (TSH 9.4) is being managed.
- **issue:** The expected answer states Kavya Reddy is 'on thyroxine replacement' and that 'Initial hypothyroidism (TSH 9.4) is being managed', but the documents explicitly state 'No need for levothyroxine' and that the earlier prescription was 'voided', indicating she is not on thyroxine and her initial elevated TSH was a spurious reading, not true hypothyroidism being managed.
- **corpus actually says:** Kavya Reddy's current thyroid status is normal (TSH 3.1 mIU/L). The initial elevated TSH (9.4 mIU/L) was considered spuriously elevated due to post-prandial sampling, and there is no biochemical evidence of hypothyroidism. She is not on thyroxine replacement.

## healthcare-q035 [CITATION_WRONG]
- **Q:** Across the corpus, how many patients have hypothyroidism?
- **expected:** (Aggregated from lab + encounter; Kavya Reddy confirmed; possibly others.)
- **corpus actually says:** Two patients, Kavya Reddy and Priya Iyer, have hypothyroidism.

## healthcare-q040 [CITATION_WRONG]
- **Q:** Synthesise the medication regimen recommendations across the corpus's chronic-disease patients.
- **expected:** (Synthesis from Rx records for chronic conditions.)
- **issue:** The question asks for a synthesis of medication regimen recommendations across the corpus's chronic-disease patients, but the provided document only contains information for a single patient (Arjun Singh) and does not offer a synthesis across multiple patients.
- **corpus actually says:** For chronic-disease patients, the medication regimen recommendations include: Telmisartan 40 mg PO once daily, Chlorthalidone 12.5 mg PO once daily, Apixaban 5 mg PO twice daily, Bisoprolol 2.5 mg PO once daily, Atorvastatin 20 mg PO once daily, Metformin 500 mg PO twice daily, and Sitagliptin 50 mg

## healthcare-q039 [CITATION_WRONG]
- **Q:** Compare Apollo Pune's consent process vs the rural Marathi intake form.
- **expected:** (Comparison: Apollo uses English + Aadhaar-linked digital consent; rural intake uses Marathi + thumb-impression + minimal documentation.)
- **corpus actually says:** The Apollo Pune consent form requires Aadhaar, PAN, signature, and medical history. The rural Marathi intake form is described as being in a mixed Marathi and English language.
