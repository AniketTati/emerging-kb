---
doc_id: drawing-003-mep-layout-datacentre
doc_type: mep_drawing
effective_date: 2025-02-12
parties: [Acme Corp Pvt Ltd, Phoenix MEP Services Pvt Ltd]
status: live
---

# MEP COORDINATION DRAWING — DATACENTRE PHASE-2

**Project:** Acme Whitefield Datacentre Phase-2
**Drawing No.:** PMEP-WCD-MEP-001
**Revision:** B (post-Architecture Rev B coordination)
**Sheet:** 1 of 8
**Scale:** 1:75
**Date:** 12 February 2025
**Prepared by:** Mr. Sushil Patil, Senior MEP Engineer (Phoenix MEP)
**Approved by:** Mr. Vijay Iyer, Principal Engineer (Phoenix MEP)

---

## Scope

This drawing shows the integrated Mechanical-Electrical-Plumbing (MEP) layout for the Acme Whitefield Datacentre main building. It coordinates:

(i) HVAC (computer-room air conditioning + comfort cooling)
(ii) Electrical (busbar + PDU + UPS distribution)
(iii) Plumbing (DG fuel + chilled water)
(iv) Fire protection (sprinkler + clean-agent for server halls)
(v) BMS (Building Management System) cabling routes

---

## Electrical Distribution

### Incoming Supply

- **HT supply:** 11 kV from BESCOM substation (existing 2,500 kVA transformer + 1 nos new 2,500 kVA Phase-2 transformer)
- **LT distribution:** 415V/240V via cable bus duct + steel cable trays
- **UPS systems:** 4 × 480 kVA modular UPS (Tier IV redundancy N+1)
- **DG backup:** 4 × 1.5 MVA DG sets (synchronous operation, N+1)
- **Total IT load capacity:** 1,920 kVA usable (after 1.4 PUE allowance = ~1,370 kW IT load)

### Distribution Routing

| Floor | Main distribution | Sub-distribution |
|---|---|---|
| Ground | Two main panels (East + West halls) via underground cable trench | Floor PDUs at each rack row (24 PDUs total per server hall) |
| First | One sub-main panel (UPS + battery rooms + chiller plant) | Direct feeds to UPS + chiller equipment |
| Mezzanine | One sub-main panel (cooling plant) | Direct feeds to chillers + CRAC |

---

## HVAC + Cooling

### Server Hall Cooling

- **Configuration:** Hot-aisle / cold-aisle with containment
- **CRAC units:** 12 nos (East: 7, West: 5) — chilled-water DX units, N+1 redundancy each side
- **Chilled water plant:** 2 × 600 TR water-cooled chillers + 1 × 600 TR backup (N+1)
- **Cooling tower:** 3 nos atop mezzanine, with redundant water pumps
- **Set-point:** 24°C ± 1°C; humidity 45-55% RH
- **Air supply:** Underfloor (raised access floor plenum 600mm); return via overhead ducts

### Comfort Cooling (Offices + NOC)

Separate VRF system (2 × 80 TR outdoor units + 24 indoor cassette units).

---

## Fire Protection

| Zone | System |
|---|---|
| Server halls + UPS room | Inergen-based clean-agent system (gas suppression) + VESDA early-warning smoke detection |
| Office + NOC + corridor | Wet pipe sprinkler |
| Plant rooms | Pre-action sprinkler |
| Battery room | FM-200 clean agent |
| External | Hydrant + monitor pumps (250 kW set) |
| Detection | Addressable smoke detectors throughout + heat detectors in plant rooms |

Fire pump house: separate small structure with diesel + electric pump duty (auto-changeover).

---

## Plumbing

- **DG fuel storage:** 2 × 10,000 L underground tanks + 1 × 1,000 L day tank (in DG room)
- **Domestic water:** 50,000 L overhead tank + UV-treated + RO
- **Cooling tower makeup:** 24,000 L sump
- **Sewage:** Connected to BWSSB main sewer
- **Storm water:** Rainwater harvesting + recharge wells (12 nos) per BBMP requirement

---

## Coordination with Architecture (Rev B)

This drawing aligns with **architecture Rev B dated 28 January 2025**, which positions the internal load-bearing wall at **Grid line D (8.1m from East face)**.

Notable coordination items:
- Main electrical busbar runs along West side of wall (Grid line D); 2 dropper points relocated from Rev A position
- HVAC ductwork crosses Grid line D at 2 locations (additional fire dampers required)
- Cable trays cross Grid line D — all penetrations to be fire-stopped per code

---

## Pending Items

(i) Final coordination with Acme's IT team (rack-by-rack power + cooling map) — meeting scheduled 22 Feb 2025
(ii) BMS specification (vendor: Honeywell Niagara) — under selection
(iii) Energy efficiency review for IGBC LEED Platinum certification — third-party consultant engaged
(iv) Final DG set noise + vibration measurements (per KPCB NOC conditions) — site test after installation

---

**Prepared by:** Mr. Sushil Patil (MEP Engineer)
**Approved:** Mr. Vijay Iyer (Principal — Phoenix MEP)
**Date:** 12 February 2025
**Status:** LIVE — for coordination + procurement
