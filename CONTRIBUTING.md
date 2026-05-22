# Contributing to Knowledge Base Service

Thanks for looking at this. The project follows a strict gate-by-gate build discipline so that the design decisions, API contracts, tests, and implementation never drift out of sync. This doc is the short version. The long version lives in [`docs/build_tracker.md`](docs/build_tracker.md).

---

## What this project is

A domain-agnostic enterprise knowledge base. Upload heterogeneous documents (PDFs digital + scanned, spreadsheets, images, emails). Ask cited natural-language questions. The system auto-discovers structure as data arrives; user-defined schemas are a *view* on top, never a precondition.

Read [`docs/problem_statement.md`](docs/problem_statement.md) for the full technical brief.

---

## How the build is organized

Every phase moves through **6 gates** in order. We do not skip gates. We do not write production logic before the gates ahead of it are green.

```
G1 Plan → G1.5 Visual prototype (UI only) → G1.6 Wiring inventory (UI only)
       → G2 API contracts → G3 Test cases → G4 Build → G5 Run / verify
```

| Gate | Artifact | Definition of green |
|------|----------|---------------------|
| G1   | Plan in `docs/build_tracker.md` | Reviewed + signed off |
| G1.5 | Clickable HTML at `prototype/*.html` | User opens in browser, signs off; Playwright QA green |
| G1.6 | `prototype/wiring_inventory.md` entry | Every interactive element → endpoint or LOCAL |
| G2   | Entry in `docs/api_contracts.md` | Endpoint shapes locked |
| G3   | Failing tests at `tests/specs/<phase>.md` + `tests/test_phase_<N>_*.py` | Tests exist, are red |
| G4   | Code in `src/kb/...` | G3 tests now pass |
| G5   | `scripts/verify_phase_<N>.sh` | End-to-end smoke green |

If you find yourself writing code without the gates ahead of it green, **stop**. Go back and close the gate.

---

## Git workflow (the short version)

```
main  (protected · only fast-forward merges via PR)
  │
  ├─ phase-0/repo-skeleton ─────────────┐
  ├─ phase-1/schema-service ────────────┤
  ├─ phase-N/<short-name> ──────────────┘
  └─ feature/<short-name>                (out-of-band)
```

**One branch per phase.** Branch from `main` when the phase enters G1. PR opens at G5 verify. Squash-merge after review. Delete the branch. Tag at phase boundaries (`git tag phase-N-complete`).

**Commit messages use [Conventional Commits](https://www.conventionalcommits.org):**

```
feat(phase-1): G4 build — schema service CRUD endpoints; G3 tests now pass
test(phase-1): G3 specs — schema CRUD test scaffolds (red)
docs(phase-1): G2 — API contracts for /schema endpoints
fix(phase-1): off-by-one in version-rollback path
chore(phase-1): G5 — verify script + green run
```

**What never gets committed:**
- `prototype/qa/screens/`, `prototype/qa/reports/` (regenerable)
- `prototype/node_modules/`, `prototype/package-lock.json` (regenerable)
- `.env*`, `.claude/`, anything in `data/` / `pg-data/` / `minio-data/`

**Tests:** every G3 row must have at least one test in `tests/test_phase_<N>_*.py`. Tests run on every PR. Red tests block merge.

---

## How to get started

1. **Clone + read.** Start with this README, then [`docs/problem_statement.md`](docs/problem_statement.md), then [`docs/architecture.md`](docs/architecture.md), then [`docs/ui_design.md`](docs/ui_design.md).
2. **Look at the tracker.** [`docs/build_tracker.md`](docs/build_tracker.md) tells you exactly what state each phase is in. Pick a phase that's at G1 or G2 and is unclaimed.
3. **Look at the prototype.** Open [`prototype/index.html`](prototype/index.html) in a browser. Click through. The wiring inventory [`prototype/wiring_inventory.md`](prototype/wiring_inventory.md) tells you what backend endpoint each interactive element will call.
4. **Open an issue** before opening a PR for anything non-trivial. Discuss design at the issue, not in the PR.
5. **Branch + commit + PR** per the workflow above.

---

## Code style

- **Python:** ruff for lint, black for format, mypy strict. Configured via `pyproject.toml` once Phase 0 lands.
- **TypeScript / Next.js:** prettier + eslint-config-next. Tailwind for styling. Lucide for icons. Light theme default.
- **Commit messages:** Conventional Commits, lowercase, present tense.
- **No `Co-Authored-By: Claude` trailers** in commits — keep authorship clean.

---

## What we deliberately don't do

These are scope decisions, not oversights. Each one is enumerated in [`README.md`](README.md) §"What we explicitly *don't* claim" and in [`docs/problem_statement.md`](docs/problem_statement.md). Don't open PRs that try to add them unless you've discussed scope expansion in an issue first.

- Permissions / ACL (deferred)
- Native CAD / DICOM / BIM geometry (out of scope)
- Real-time streaming sources (KB ≠ OLAP, by design)
- Bi-temporal validity (deferred)
- Agentic actions (read-only by design)
- Multi-tenant isolation (deferred)
- Cross-lingual atomic-unit extraction (initially English-only)

---

## Code of conduct

Be technical, honest, and direct. Disagree with the design freely — that's how it gets better. Don't be a jerk about it.

---

## License

See [LICENSE](LICENSE).
