---
doc_id: drawing-001-arch-datacentre-revB
doc_type: architectural_drawing
effective_date: 2025-01-28
parties: [Acme Corp Pvt Ltd, Deshpande Architects + Engineers LLP]
status: superseded
chain_id: chain_datacentre_drawing_revisions
parent_doc: drawing-001-arch-datacentre-revA
---

# ARCHITECTURAL DRAWING — REVISION B (LOAD-BEARING WALL REPOSITIONED)

**Project:** Acme Whitefield Datacentre Phase-2 — Main Building
**Drawing No.:** DAE-WCD-ARCH-001
**Revision:** B (Wall repositioned per Structural)
**Sheet:** 1 of 12
**Scale:** 1:100
**Date:** 28 January 2025
**Drawn by:** Ar. Rajesh Iyer
**Checked by:** Ar. Amit Deshpande
**Approved by:** Ar. Amit Deshpande

---

## Revision History

| Rev | Date | Reason | Reference |
|---|---|---|---|
| A | 12 Jan 2025 | Initial issue for coordination | — |
| **B** | **28 Jan 2025** | Internal load-bearing wall repositioned from Grid line C (5.4m E) to Grid line D (8.1m E) per Structural calculation SSC-WCD-CALC-001 dated 22 Jan 2025 | SSC-WCD-CALC-001 |

---

## Changes from Rev A

### Wall Repositioning (Grid line C → Grid line D)

**Before (Rev A):** Internal load-bearing wall at Grid line C, 5.4m from East face.

**After (Rev B):** Internal load-bearing wall at **Grid line D**, 8.1m from East face.

**Reason:** Structural engineer (Dr. Murali Sundar, SSC) determined that the proposed wall location at Grid line C was inadequate for the First Floor equipment loading (UPS + battery + chillers, max 24.8 kN/m²). Repositioning to Grid line D reduces the worst-case slab span from 16.5m to 13.8m, allowing the current 280mm slab design to be retained.

### Server Hall Sizing (Updated)

| Zone | Rev A area (sq.m) | Rev B area (sq.m) | Change |
|---|---|---|---|
| Server hall (East wing) | 1,840 (484 racks) | 1,758 (456 racks) | -82 sq.m (-28 racks; -5.8%) |
| Server hall (West wing) | 1,200 (315 racks) | 1,282 (336 racks) | +82 sq.m (+21 racks) |
| **Total server-hall area** | **3,040 (799 racks)** | **3,040 (792 racks)** | -7 racks net (-0.9%) |

The total rack capacity reduces by 7 (from 799 to 792 = -0.9%). Acme has confirmed this is acceptable.

### Door Schedule Adjustment

Door D4-7 (corridor door between East server hall and central corridor) shifts 2.7m to the south to align with the new wall position. All other doors unchanged.

### Other Aspects (Unchanged)

| Item | Rev A | Rev B |
|---|---|---|
| External envelope | Same | Same |
| Setbacks | Same | Same |
| MEP routing | Same | Same (Phoenix MEP confirmed; small power-cable rerouting only) |
| Roof | Same | Same |
| Finishes | Same | Same |
| Site plan | Same | Same |

---

## Structural Endorsement

Per SSC-WCD-CALC-001 dated 22 January 2025:

✓ Wall position at Grid line D adequate for design loads
✓ Slab design (280mm M30 RCC) adequate
✓ Foundation design (proportional reinforcement adjustment) finalised
✓ Seismic detailing per IS 13920:2016 confirmed

Sign-off: Dr. Murali Sundar, Principal Structural Engineer, SSC (date: 26 January 2025)

---

## Coordination Updates

- **MEP (Phoenix):** Confirmed via email 24 Jan 2025 that power-cable routing through the new wall location requires only minor changes (2 dropper points relocated). No cost impact.
- **Electrical (Phoenix):** Server-hall busbar layout unchanged; floor-PDU positions to be finalised in coordination meeting 5 Feb 2025.
- **Plumbing (Phoenix):** No impact.
- **Fire safety:** Compartmentation review completed — 1-hour fire-rated wall design adequate.

---

## Approvals + Sign-off

**Drawn:** Ar. Rajesh Iyer
**Checked:** Ar. Amit Deshpande
**Approved:** Ar. Amit Deshpande
**Endorsed (Structural):** Dr. Murali Sundar, SSC
**Client acknowledgement:** Ms. Priya Iyer (Acme — VP Legal, who also serves as Project Owner representative) — confirmed via email 27 January 2025

**Distribution:**
- Acme Corp (Client)
- Sundar Structural Consultants (Structural)
- Phoenix MEP Services (MEP)
- Mahalaxmi Infrastructure (Main Contractor)
- BBMP (Planning Authority — revised submission, ref BBMP/PE/2024/1842-R1)

**Status:** Issued for Construction (SUPERSEDED by Rev C dated 22 March 2025 — minor revisions for owner-requested changes only)
