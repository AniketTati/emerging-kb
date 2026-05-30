---
doc_id: structural-calc-001-datacentre-loadtable
doc_type: structural_calculation
effective_date: 2025-01-22
parties: [Acme Corp Pvt Ltd, Sundar Structural Consultants Pvt Ltd]
status: live
stressors: [conflict_resolution_target]
---

# STRUCTURAL CALCULATIONS — LOAD ASSESSMENT + LOAD-BEARING WALL POSITIONING

**Project:** Acme Whitefield Datacentre Phase-2 — Main Building
**Calculation ref:** SSC-WCD-CALC-001
**Date:** 22 January 2025
**Prepared by:** Dr. Murali Sundar, Principal Structural Engineer
**Reviewed by:** Dr. Ravi Iyer, Senior Structural Engineer

---

## Executive Summary

This calculation report addresses the **internal load-bearing wall positioning** for the datacentre Phase-2 main building, in response to the architectural drawing DAE-WCD-ARCH-001 Rev A (dated 12 January 2025).

**Conclusion:** The wall position proposed by the Architect at **Grid line C (5.4m from the East face)** is **STRUCTURALLY INADEQUATE** for the design loads. The wall must be relocated to **Grid line D (8.1m from the East face)** to achieve safe load-paths under the proposed First Floor equipment loading.

---

## Design Load Assessment

### First Floor Loading (UPS + Battery Equipment)

The First Floor of the datacentre carries UPS + battery equipment + a chiller plant room. The load summary:

| Equipment | Load (kN/m²) | Footprint area (sq.m) |
|---|---|---|
| UPS units (4 nos) | 18.4 | 84 |
| Battery banks (2 banks × 480 cells) | 24.8 | 56 |
| Chiller pad (2 chillers) | 32.4 | 48 |
| Battery room walking + service areas | 5.0 | 84 |
| Backup tank (DC bus, 280L electrolyte) | 14.8 | 16 |
| **Aggregated worst-case zone** | **24.8 kN/m²** | over central span 18m × 8m |

This is **substantially higher** than the standard architectural assumption of 5 kN/m² for office floors. The architect's drawing did not account for these heavy equipment loads.

### Slab Span + Wall Position Requirements

For the proposed RCC slab (280mm thick, M30 grade concrete, Fe500 reinforcement) under the worst-case 24.8 kN/m² loading:

| Wall position | Max slab span | Slab depth required | Steel area required | Verdict |
|---|---|---|---|---|
| Grid line C (5.4m from East) | 9.4m east + 16.5m west | 320mm thick (Grade M30) | 1,184 mm² per m | INADEQUATE (would require 320mm slab; current design is 280mm) |
| Grid line D (8.1m from East) | 8.1m east + 13.8m west | 280mm thick (Grade M30) | 884 mm² per m | ADEQUATE — within current design parameters |
| Grid line E (10.0m from East) | 10.0m east + 11.9m west | 280mm thick (Grade M30) | 942 mm² per m | ADEQUATE but reduces East server hall by ~7% |

### Recommendation

The wall must be relocated from **Grid line C** to **Grid line D**. This:

(i) Reduces the worst-case slab span from 16.5m to 13.8m;
(ii) Allows the current 280mm slab design to be retained (avoids re-design of First Floor);
(iii) Keeps the East server hall at 8.1m × 36.8m = 298 sq.m on this row (still ~5% reduction vs Rev A, but vastly safer);
(iv) Avoids the ~INR 18 lakh cost premium of thickening the entire First Floor slab.

---

## Code References

| Code | Clause | Applied |
|---|---|---|
| IS 875 Part 2:1987 | Live loads on floors | Imposed live loads + equipment loads |
| IS 456:2000 | Plain + Reinforced Concrete Code | Flexural design + serviceability |
| IS 13920:2016 | Ductile Detailing for Earthquake | Detailing of load-bearing wall reinforcement |
| IS 1893 Part 1:2016 | Seismic design | Bangalore in Zone II (low seismic risk) |
| NBC 2016 | National Building Code (datacentres) | Equipment loads + service paths |

---

## Coordination Note (to Architect)

Ar. Amit Deshpande,

We have completed structural review of your Rev A drawing. As detailed above, **the load-bearing wall must be repositioned from Grid line C to Grid line D**. Please:

(i) Revise the architectural drawings (Rev B) to show the wall at Grid line D;
(ii) Update the door + window schedule (one corridor door D4-7 will shift);
(iii) Re-coordinate with Phoenix MEP (Mr. Sushil Patil) — server-hall power-routing assumptions may shift slightly.

The structural drawings (S-001 onwards) are being prepared in parallel based on the Grid line D assumption. Final issuance of structural drawings is contingent on architectural Rev B.

For client (Acme Corp — Ms. Priya Iyer / Mr. Rakesh Sundaram) information: this change is needed to ensure code-compliance + safety; cost impact is positive (avoids the slab-thickening cost premium of ~INR 18 lakh).

---

## Sign-off

**Prepared by:** Dr. Murali Sundar, Principal Structural Engineer
**Reviewed by:** Dr. Ravi Iyer, Senior Structural Engineer
**Stamped by:** Dr. Murali Sundar (PE Karnataka — CoR 184228)
**Date:** 22 January 2025

**Distribution:**
- Deshpande Architects (Ar. Amit Deshpande) — for revision
- Mahalaxmi Infrastructure (Mr. Rakesh Iyer — PM)
- Acme Corp (Ms. Priya Iyer)
- Phoenix MEP (Mr. Sushil Patil)
