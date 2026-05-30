---
doc_id: commissioning-001-dg-test-report
doc_type: commissioning_report
effective_date: 2026-01-22
parties: [Acme Corp Pvt Ltd, Phoenix MEP Services Pvt Ltd, Cummins India]
status: live
---

# DG SET COMMISSIONING + LOAD TEST REPORT

**Project:** Acme Whitefield Datacentre Phase-2
**Equipment:** 4 × Cummins Diesel Generator Sets, 1.5 MVA each (S/Ns DG-001 through DG-004)
**Test Date:** 22 January 2026
**Test Conducted by:**
- Mr. Ranjit Kale (Commissioning Lead — Phoenix MEP)
- Mr. Anand Karmakar (Cummins India service engineer)
- Mr. Karthik Naidu (Phoenix Electrical Site Engineer)
- Mr. Anand Krishnamurthy (Mahalaxmi observer)
- Mr. Sanjay Tripathi (Acme observer)

---

## 1. Test Scope

(i) Individual DG-set start-up + steady-state operation
(ii) Synchronisation testing (2 DG parallel + all 4 parallel)
(iii) Load step testing (gradual + step load application)
(iv) Mains failure simulation + auto transfer (within 8 second SLA per spec)
(v) Fault recovery (single DG trip + load redistribution)
(vi) Fuel-on-demand control verification
(vii) Sound + vibration monitoring (per KSPCB NOC conditions)

---

## 2. Test Setup

- Load bank: 6 MVA total available (4 × 1.5 MVA load bank units provided by Cummins)
- Site load: Live equipment (chillers + UPS) provides ~1.2 MVA baseline real load
- Synthetic load bank: ~5 MVA for full-load testing
- Total test load: up to 6 MVA (full 4-DG capacity)

---

## 3. Test Results

### Individual DG-Set Start-up

| DG | Cold start (sec) | Steady-state achieved (sec) | Status |
|---|---|---|---|
| DG-001 | 4.2 | 18 | ✅ PASS |
| DG-002 | 3.8 | 14 | ✅ PASS |
| DG-003 | 4.4 | 18 | ✅ PASS |
| DG-004 | 4.0 | 15 | ✅ PASS |

(Specification: ≤ 8 sec cold start, ≤ 20 sec steady-state. All within spec.)

### Synchronisation (2-DG parallel)

| Configuration | Sync time (sec) | Load distribution (kW each) | Status |
|---|---|---|---|
| DG-001 + DG-002 | 12 | 750 + 750 | ✅ PASS (even split) |
| DG-003 + DG-004 | 14 | 750 + 750 | ✅ PASS |

### Synchronisation (4-DG parallel)

| Configuration | Sync time (sec) | Load distribution | Status |
|---|---|---|---|
| All 4 DGs parallel | 22 (from cold start) | 1500 + 1500 + 1500 + 1500 kW (equal) | ✅ PASS |

### Load Step Test

| Load step | Voltage dip | Recovery time | Status |
|---|---|---|---|
| 0 → 25% (1.5 MVA) | -8% V | 1.2 sec | ✅ PASS |
| 25% → 50% (3.0 MVA) | -12% V | 2.4 sec | ✅ PASS |
| 50% → 75% (4.5 MVA) | -14% V | 3.4 sec | ✅ PASS |
| 75% → 100% (6.0 MVA) | -16% V | 4.4 sec | ✅ PASS |

(Specification: dip ≤ 20% V, recovery ≤ 5 sec. All within spec.)

### Mains Failure Simulation

| Event | Auto-transfer time | Status |
|---|---|---|
| BESCOM mains opens | DG-001 + DG-002 picks up load in 7.2 sec | ✅ PASS (spec: ≤ 8 sec) |
| BESCOM mains re-establishes (after 30 min) | Re-transfer to mains in 12 sec | ✅ PASS (spec: ≤ 15 sec) |

### Fault Recovery (Single DG Trip)

| Scenario | Recovery | Status |
|---|---|---|
| DG-003 trip during operation (simulated fuel valve close) | DG-004 picks up + load redistribution + manual restart of DG-003 = 38 sec full restoration | ✅ PASS |
| DG-001 + DG-002 simultaneous trip (simulated controller fault) | DG-003 + DG-004 carry load; DG-001/DG-002 manual restart = 1.4 min full restoration | ✅ PASS |

### Sound + Vibration

| Metric | Measurement | Limit (KSPCB NOC) | Status |
|---|---|---|---|
| Sound (at 7 m from DG-1 enclosure) | 78 dB(A) | ≤ 75 dB(A) | ⚠ ABOVE limit by 3 dB |
| Vibration (DG #1 isolator base) | 6.4 mm/s | ≤ 5.0 mm/s | ⚠ ABOVE limit by 1.4 mm/s |
| Sound (at 7 m from DG-2) | 76 dB(A) | ≤ 75 dB(A) | ⚠ Just above |
| Sound (at 7 m from DG-3) | 73 dB(A) | ≤ 75 dB(A) | ✅ PASS |
| Sound (at 7 m from DG-4) | 74 dB(A) | ≤ 75 dB(A) | ✅ PASS |

⚠ **Action required:** Additional acoustic insulation on DG-1 + DG-2 enclosures (sound) + isolator re-balancing on DG-1 (vibration). Phoenix MEP + Cummins joint responsibility. Target closure: 5 February 2026.

---

## 4. Verdict

✅ **DG sets are operationally functional + within performance specifications**, with the exception of the sound + vibration items noted above.

These are minor commissioning items; the DG sets are otherwise ready for use. The acoustic + vibration corrective work is being completed within the next 2 weeks (target 5 February).

---

## 5. Pending Actions

(i) Acoustic insulation enhancement on DG-1 + DG-2 (target 5 Feb 2026)
(ii) Vibration isolator re-balancing on DG-1 (target 5 Feb 2026)
(iii) Re-test sound + vibration after corrective work
(iv) Re-submit final reading to KSPCB for compliance acknowledgement

---

## 6. Signatures

**Phoenix MEP (Lead):**
*Ranjit Kale* — Commissioning Lead

**Cummins India:**
*Anand Karmakar* — Service Engineer

**Mahalaxmi:**
*Anand Krishnamurthy* — Site Engineer

**Acme:**
*Sanjay Tripathi* — Procurement Head (observer)

Date: 22 January 2026
