# construction — genuine eval issues needing a human decision

7 queries could not be grounded to any corpus doc (real answer bugs or corpus gaps).

## construction-q009 [NOT_IN_CORPUS]
- **Q:** What is the structural foundation depth at Noamundi datacentre site?
- **expected:** 3.8 m below FFL to founding on weathered gneiss (per drawing-004-structural-foundation).
- **issue:** The expected answer refers to a 'Noamundi datacentre site', but the document clearly states the project is 'Acme Whitefield Datacentre Phase-2'.
- **corpus actually says:** The structural foundation depth at the Acme Whitefield Datacentre Phase-2 site is 3.8 m below FFL to founding on weathered gneiss.

## construction-q023 [CITATION_WRONG]
- **Q:** Two purchase orders show different unit rates for steel. Which is authoritative?
- **expected:** Each PO is independently authoritative for its consignment. Different POs for different time periods can have different rates due to market fluctuation. The signed PO controls each delivery.
- **issue:** The provided document is a single purchase order and does not contain information about different unit rates for steel across multiple purchase orders, nor does it discuss which PO is authoritative in such a scenario.
- **corpus actually says:** The question "Two purchase orders show different unit rates for steel. Which is authoritative?" is a test query designed to evaluate the system's ability to handle conflicts and determine authority. The expected answer is that the system should identify the authoritative document based on the chain 

## construction-q011 [CITATION_WRONG]
- **Q:** What is the contract clause about labour PPE on the Acme datacentre site?
- **expected:** Per Clause 9 of EPC contract — Mahalaxmi shall maintain PPE for all workers + comply with Karnataka Factories Act + BOCW Act.
- **issue:** The expected answer states that Clause 9 requires Mahalaxmi to maintain PPE for all workers, but the provided document for Clause 9 does not contain this specific requirement.
- **corpus actually says:** Clause 9 of the EPC contract outlines Mahalaxmi's obligations regarding safety and compliance, including complying with the Karnataka Factories Act 1948 and the Building and Other Construction Workers (Regulation) Act 1996. It also requires Mahalaxmi to maintain Workmen Compensation insurance, Publi

## construction-q027 [ANSWER_WRONG]
- **Q:** Any drawings with explicit fire-rating for clean-agent suppression?
- **expected:** Yes — Inergen (server halls) + FM-200 (battery room) per MEP drawings + commissioning.
- **issue:** The expected answer mentions 'MEP drawings + commissioning' but the commissioning report does not contain information about fire-rating or clean-agent suppression. The MEP drawing specifies the clean agents and mentions fire-stopping for penetrations, which implies fire-rating, but does not explicitly state 'fire-rating for clean-agent suppression'.
- **corpus actually says:** The drawing specifies Inergen for server halls and UPS room, and FM-200 for the battery room. It also states that 'all penetrations to be fire-stopped per code' where cable trays cross Grid line D, which is an internal load-bearing wall.

## construction-q034 [CITATION_WRONG]
- **Q:** How many distinct sub-contractors are on the Acme datacentre project?
- **expected:** At least 5: Phoenix MEP (MEP), Sundar Structural (structural), Deshpande Architects (architect), Sai Labour Agency (labour), various material vendors (JSW Steel, Schneider, Tarkett, etc.).
- **corpus actually says:** There are four distinct sub-contractors on the Acme datacentre project: Mahalaxmi Infrastructure Pvt Ltd (Main Contractor), Deshpande Architects + Engineers LLP (Architect), Sundar Structural Consultants Pvt Ltd (Structural Engineer), and Phoenix MEP Services Pvt Ltd (MEP Engineer).

## construction-q028 [CITATION_WRONG]
- **Q:** Any incidents with >1 month investigation period?
- **expected:** Safety incident-001 (May 22 incident → Jun 18 corrective action = 27 days investigation + remediation).
- **issue:** The expected answer states a 27-day investigation + remediation period, which is less than 'greater than 1 month'. The documents do not mention any incident with an investigation period exceeding one month.
- **corpus actually says:** The incident occurred on May 22, 2025, and the corrective action report was dated June 18, 2025. The detailed investigation report was dated June 1, 2025. This indicates an investigation and remediation period of 27 days (May 22 to June 18) or 10 days for the detailed investigation (May 22 to June 1

## construction-q032 [ANSWER_WRONG]
- **Q:** How many safety incidents occurred over the project's duration?
- **expected:** 1 lost-time injury (Mr. Dinesh Kumar, 22 May 2025). No fatalities. 4 first-aid only incidents in April.
- **issue:** The expected answer states 4 first-aid only incidents in April, but the document 'safety-002-monthly-safety-report' states there were 6 first-aid only incidents in April.
- **corpus actually says:** 1 lost-time injury (Mr. Dinesh Kumar, 22 May 2025). No fatalities. 6 first-aid only incidents in April.
