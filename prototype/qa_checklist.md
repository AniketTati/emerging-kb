# Visual QA Checklist — KB Prototype

> Applied per page, per viewport. The runner (`prototype/qa.mjs`) screenshots each page at 3 viewports and produces a report file. This checklist is the source of truth for what we check.
>
> Source: [`docs/build_tracker.md`](../docs/build_tracker.md) §0.1.

## Viewports we check

| Name | Width × Height | Why |
|---|---|---|
| **desktop** | 1440 × 900 | Primary work surface |
| **tablet** | 1024 × 768 | Realistic split-screen / smaller laptop |
| **mobile** | 390 × 844 | Admin tools are desktop-first, but chat/upload should at least not break |

## Checklist (each item: ✓ pass · ⚠ minor · ✗ fail)

### 1. Sidebar / left nav
- [ ] Collapsed-state icons all render (no missing/broken)
- [ ] Hover-expand reveals labels cleanly (no flicker)
- [ ] Active section visually distinct (bg + text weight)
- [ ] Section dividers labelled (Primary / Studio / Admin)
- [ ] No overflow at any viewport
- [ ] Keyboard focus visible
- [ ] Logo renders correctly

### 2. Top bar / header
- [ ] Breadcrumb readable
- [ ] Right-side actions don't overlap title at narrow widths
- [ ] ⌘K hint present and aligned
- [ ] Theme toggle present
- [ ] No vertical misalignment of items

### 3. Primary content area
- [ ] Max-width sane (text isn't a wide ribbon on big monitors)
- [ ] Scroll behaves (sticky composer/header stays)
- [ ] Typography hierarchy clear (h1 → h2 → body)
- [ ] Line-length 60–80ch for prose
- [ ] Inline images/figures don't blow out the column

### 4. Right panel (when present)
- [ ] Width fixed and reasonable (350–400px)
- [ ] Header sticky
- [ ] Inner scrolling independent of main column
- [ ] Cards don't horizontal-scroll
- [ ] Doesn't collapse content below readable threshold

### 5. Interactive elements
- [ ] All buttons have visible hover state
- [ ] All buttons have ≥36px touch target on mobile
- [ ] Inputs show focus ring
- [ ] Links underline on hover or have other affordance
- [ ] Disabled states clearly muted

### 6. Icons & imagery
- [ ] Every icon renders (no broken/missing)
- [ ] Icon stroke widths consistent
- [ ] Icons aligned with their labels (vertical baseline)
- [ ] Logo / brand mark renders correctly

### 7. Typography & color
- [ ] Body contrast ≥ 4.5:1 against background (WCAG AA)
- [ ] No text below 12px except mono technical metadata
- [ ] Mono font reserved for IDs/timings/snippets
- [ ] Accent color used sparingly (≤ 3 instances per screen)

### 8. Empty / loading / error states
- [ ] Each list/feed/table has an explicit empty state
- [ ] Loading states are progressive (skeleton/stream, not centered spinner)
- [ ] Errors are inline and recoverable

### 9. Information density
- [ ] Whitespace appropriate for the surface
- [ ] No "wall of text" without visual breaks
- [ ] Related elements grouped, unrelated separated

### 10. Responsive
- [ ] At tablet: sidebar collapses by default
- [ ] At mobile: right panel collapses to a tab or drawer
- [ ] Tap targets respected
- [ ] No horizontal page scroll

### 11. Cross-page consistency (checked once, applied to all)
- [ ] Sidebar identical on every page
- [ ] Top-bar height identical
- [ ] Hover/focus patterns identical
- [ ] Spacing scale identical

### 12. Cross-cutting design rules (must hold on this page if applicable)
- [ ] Schema visible — every shown field value has its typed/inferred badge or one-click access to Schema Studio
- [ ] Schema editable — every shown field value has an edit affordance (inline edit OR jump-to-Studio)
- [ ] Doc Detail universal — any doc / citation / entity / clause opens the same slide-in panel
- [ ] ⌘K reachable from this page
- [ ] Long operations stream (no centered spinners)
- [ ] Derived values show confidence + source (answers, extracted fields, promoted fields, anomalies)

## How to run

```bash
cd prototype
npm install                      # one-time
npx playwright install chromium  # one-time
node qa.mjs                       # screenshots all pages, runs auto checks
node qa.mjs chat.html             # single page
```

Output:
- `prototype/qa/screens/<page>-<viewport>.png` — screenshots
- `prototype/qa/reports/<page>.md` — per-page report (auto-checks + manual checklist scaffold)
