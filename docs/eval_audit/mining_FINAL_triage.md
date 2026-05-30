# mining — genuine eval issues needing a human decision

11 queries could not be grounded to any corpus doc (real answer bugs or corpus gaps).

## mining-q002 [NOT_IN_CORPUS]
- **Q:** Who is the DGMS Director General?
- **expected:** (Per DGMS authority docs; system retrieves name.)

## mining-q016 [NOT_IN_CORPUS]
- **Q:** Trace the incident-003 (Mahalaxmi vendor) chain — initial → investigation → corrective.
- **expected:** (3-doc chain on Mahalaxmi equipment vendor + DGMS inspection finding.)

## mining-q007 [CITATION_WRONG]
- **Q:** Who is the safety officer at the Bokaro coal mine?
- **expected:** (Per DGMS inspection or mine staff register.)
- **corpus actually says:** The safety officer at the Bokaro Coal Block is the Safety Officer, Bokaro Operations.

## mining-q018 [ANSWER_WRONG]
- **Q:** Identify all amendment / corrigendum documents in the mining domain.
- **expected:** Parsa env corrigendum; Noamundi drilling rev2 + rev3; possibly others.
- **issue:** The expected answer lists 'Noamundi drilling rev2 + rev3' as amendment/corrigendum documents, but these are not present in the provided text. Only 'Parsa env corrigendum' is supported.
- **corpus actually says:** The document 'env-clearance-001-parsa-corrigendum' is an amendment/corrigendum document. The provided documents do not contain any other amendment or corrigendum documents, nor do they mention 'Noamundi drilling rev2 + rev3'.

## mining-q024 [CITATION_WRONG]
- **Q:** Two safety reports for the same shift give different fatality counts. Which is correct?
- **expected:** Final DGMS-confirmed count prevails over preliminary site reports. Often initial reports underestimate severity; investigation refines the count.
- **issue:** The provided document is a single safety incident report and does not discuss two different safety reports for the same shift with conflicting fatality counts, nor does it mention DGMS-confirmed counts prevailing over preliminary reports.
- **corpus actually says:** The Jharia roof collapse resulted in 3 fatalities. The shift log for the recovery shift mentions "ex-gratia disbursed to next-of-kin of three deceased" and lists the names of the three individuals who died: Sukan Hembrom, Devraj Mahato, and Pranay Tudu. The workspace evaluation queries also confirm 

## mining-q025 [CITATION_WRONG]
- **Q:** Different drilling consultants report different Fe% at the same Noamundi sample location. How resolve?
- **expected:** Compare methodology + sample handling. If both are valid, the later-dated report (with newer techniques) often prevails; otherwise both should be cited as independent estimates.
- **issue:** The provided documents discuss a drilling report for the Noamundi iron-ore block, including Fe% estimates, but do not contain any information on how to resolve discrepancies when different drilling consultants report different Fe% values at the same location.
- **corpus actually says:** Different drilling consultants reporting different Fe% at the same Noamundi sample location can be resolved by referencing the latest drilling report version. The mean Fe grade has been revised upward across each version of the study, reflecting accumulating sample density and increased weighting to

## mining-q035 [CITATION_WRONG]
- **Q:** How many DGMS inspection reports are in the corpus?
- **expected:** (Count from L1 manifest filter.)
- **corpus actually says:** The corpus contains one DGMS inspection report for Bokaro methane and one for Noamundi Iron-Ore Block.

## mining-q034 [CITATION_WRONG]
- **Q:** Combined approved capacity across all coal blocks.
- **expected:** (Sum of approved MTPA across all coal blocks; per current/latest approvals.)
- **issue:** The document only provides the approved capacity for the Parsa Coal Block (18 MTPA) and does not provide the combined approved capacity across all coal blocks.
- **corpus actually says:** The approved production capacity for the Parsa Coal Block is 18 MTPA (eighteen million tonnes per annum) of run-of-mine coal.

## mining-q036 [CITATION_WRONG]
- **Q:** Total acreage covered by all mining leases.
- **expected:** (Aggregate sum of all lease acreages, including scanned/bilingual leases.)
- **corpus actually says:** The Jharia Coalfield, Block-North-7 mining lease covers approximately 412 hectares.

## mining-q033 [CITATION_WRONG]
- **Q:** How many distinct mining sites appear in the corpus?
- **expected:** Noamundi, Parsa, Jharia, Bailadila, Bokaro, Rajpura — at least 6 distinct sites.
- **corpus actually says:** There are two distinct mining sites mentioned in the corpus: Rajpura-Dariba zinc-lead prospect and Jharia Coal Block. Additionally, Mahalaxmi Infrastructure Pvt Ltd was a vendor at "a mining site" (unnamed) implicated in incident-003.

## mining-q038 [NOT_IN_CORPUS]
- **Q:** Summarise the Bokaro methane explosion incident + investigation + corrective.
- **expected:** (Multi-paragraph summary of full incident chain.)
