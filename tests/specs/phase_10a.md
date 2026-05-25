# Phase 10a — Test Spec (G3)

> **Status:** G3 + G4 done 2026-05-25. Tests live in `ui/tests/` (Vitest + Playwright), not under `tests/` (which is Python). This spec documents what landed.
> **Inputs:** Phase 10a G1 plan ([build_tracker §5.17](../../docs/build_tracker.md), 15 decisions).

---

## 1. Scope

Three test surfaces:

- `ui/tests/api.test.ts` — Vitest unit tests for `ui/lib/api.ts` pure helpers (10 tests).
- `ui/tests/upload.spec.ts` — Playwright E2E asserting the `/upload` page renders + saving a screenshot artifact (2 tests).
- `tests/test_health.py` + `tests/test_api_query.py` (regression check) — confirm the new CORS middleware doesn't break the existing FastAPI request path.

## 2. Decision → test mapping

### Vitest (`ui/tests/api.test.ts`)

| Test | Asserts |
|---|---|
| `stageIndexFor("parsing")` → 0 | Decision #12 pip mapping (parse stage) |
| `stageIndexFor` for chunked/contextualized/embedded → 1 | Decision #12 (embed cluster) |
| `stageIndexFor("raptor_building")` → 2 | Decision #12 (raptor) |
| `stageIndexFor` for all *_extracting + identity_resolving → 3 | Decision #12 (extract cluster) |
| `stageIndexFor("ready")` → 4 | Decision #12 (terminal) |
| `stageIndexFor` for queued/failed/deleted → -1 | Decision #12 (off-line states) |
| `isTerminal("ready"/"failed"/"deleted")` → true | Decision #9 (SSE close + state semantics) |
| `isTerminal` for intermediate states → false | Decision #9 |
| `stageLabelFor("raptor_building")` → "raptor building" | UI display helper |
| `stageLabelFor("ready")` → "ready" | UI display helper |

### Playwright (`ui/tests/upload.spec.ts`)

| Test | Asserts |
|---|---|
| `upload page renders the sidebar, topbar, dropzone, and table` | Decision #11 (table) + #7 (dropzone) + sidebar + topbar present; saves screenshot artifact |
| `upload page redirects from root` | `/` redirects to `/upload` (App Router redirect) |

### Backend regression

- Full `pytest -q` still 541/541 after CORS middleware addition (decision #6) — no behavioral change for non-CORS requests.

## 3. G3 exit criteria

- `npm test` (vitest) — 10/10 GREEN.
- `npx playwright test` — 2/2 GREEN.
- `scripts/verify_phase_10a.sh` — 11 checks (compose smoke + node/npm + npm install + vitest + next build + CORS sanity + playwright install + playwright run + screenshot saved + no leak grep).
- Full backend pytest 541/541 still GREEN.
