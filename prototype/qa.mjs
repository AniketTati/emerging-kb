// Visual QA runner for the KB prototype.
// Screenshots every page at 3 viewports, runs auto checks, writes per-page reports.
//
// Usage:
//   node qa.mjs              # all pages
//   node qa.mjs chat.html    # one page

import { chromium } from 'playwright';
import { readdirSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const SCREENS_DIR = join(ROOT, 'qa', 'screens');
const REPORTS_DIR = join(ROOT, 'qa', 'reports');
mkdirSync(SCREENS_DIR, { recursive: true });
mkdirSync(REPORTS_DIR, { recursive: true });

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
];

function listPages(arg) {
  if (arg) return [arg];
  return readdirSync(ROOT)
    .filter((f) => f.endsWith('.html'))
    .filter((f) => f !== 'index.html');
}

async function runChecks(page) {
  // Auto checks that don't need a human to look at the screen.
  return await page.evaluate(() => {
    const findings = [];

    // 1. broken images / icons
    const brokenImgs = [...document.images].filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.src);
    findings.push({
      id: 'imgs.complete',
      ok: brokenImgs.length === 0,
      detail: brokenImgs.length === 0 ? 'all images loaded' : `${brokenImgs.length} broken: ${brokenImgs.join(', ')}`,
    });

    // 2. lucide icons should be svg, not <i data-lucide=...> still present
    const unresolvedLucide = document.querySelectorAll('i[data-lucide]').length;
    findings.push({
      id: 'icons.rendered',
      ok: unresolvedLucide === 0,
      detail: unresolvedLucide === 0 ? 'all lucide icons resolved to svg' : `${unresolvedLucide} <i data-lucide> tags still unresolved`,
    });

    // 3. no horizontal page scroll
    //    Mobile is informational only — KB Service is a desktop-first admin tool;
    //    full mobile responsive lands with the production Next.js UI in Phase 10a-d.
    const hasHScroll = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
    const vw = document.documentElement.clientWidth;
    const isMobile = vw < 600;
    findings.push({
      id: 'layout.no-hscroll',
      ok: !hasHScroll || isMobile,
      detail: hasHScroll
        ? `page is ${document.documentElement.scrollWidth}px wide vs ${vw}px viewport${isMobile ? ' (informational — desktop-first prototype; mobile responsive lands in production UI)' : ''}`
        : 'no horizontal page scroll',
    });

    // 4. small text scan — flag text smaller than the threshold UNLESS it's
    //    uppercase letterspaced (section label convention) or monospace
    //    (technical metadata: IDs, timings, scores).
    const all = document.querySelectorAll('*');
    let tooSmall = 0;
    const samples = [];
    all.forEach((el) => {
      const txt = el.textContent && el.textContent.trim();
      if (!txt) return;
      // skip elements that contain children with their own text — only check leaf-ish nodes
      if (el.children.length > 0 && [...el.children].some((c) => c.textContent && c.textContent.trim())) return;
      const cs = getComputedStyle(el);
      const fs = parseFloat(cs.fontSize);
      if (!fs || fs >= 10) return;
      // whitelist: uppercase section labels (small-caps convention),
      // mono metadata (IDs/timings/scores), and intrinsically small tags (<sup>, <sub>)
      const isUppercase = cs.textTransform === 'uppercase';
      const isMono = /mono|JetBrains/.test(cs.fontFamily);
      const isSupSub = /^(SUP|SUB)$/.test(el.tagName);
      if (isUppercase || isMono || isSupSub) return;
      tooSmall++;
      if (samples.length < 3) samples.push(`"${txt.slice(0, 40)}" ${fs}px`);
    });
    findings.push({
      id: 'typo.min-size',
      ok: tooSmall === 0,
      detail: tooSmall === 0 ? 'no non-mono / non-uppercase text under 10px' : `${tooSmall} elements (samples: ${samples.join('; ')})`,
    });

    // 5. buttons without text or aria-label
    const unlabeled = [...document.querySelectorAll('button')].filter(
      (b) => !b.textContent.trim() && !b.getAttribute('aria-label') && !b.querySelector('svg')
    ).length;
    findings.push({
      id: 'a11y.button-labels',
      ok: unlabeled === 0,
      detail: unlabeled === 0 ? 'all buttons have text/icon/label' : `${unlabeled} unlabeled buttons`,
    });

    // 6. anchors without href
    const danglingAnchors = [...document.querySelectorAll('a')].filter((a) => !a.getAttribute('href') || a.getAttribute('href') === '#').length;
    findings.push({
      id: 'a11y.anchor-href',
      ok: true, // informational
      detail: `${danglingAnchors} anchors without real href (informational only — prototype navigation)`,
    });

    // 7. dom size
    findings.push({
      id: 'perf.dom-size',
      ok: all.length < 3000,
      detail: `${all.length} dom nodes`,
    });

    // 8. user-facing copy — no engineering-roadmap leakage
    // Forbidden in production UI: wave labels, phase numbers, design names, internal libs.
    const bodyText = document.body.innerText;
    const forbidden = [
      /\bWave [ABC]\b/, /\bPhase \d+\b/, /\bDesign \d\b/,
      /\bHydra\b/, /\bOmegaConf\b/, /\bProcrastinate\b/,
      /\bRAPTOR\b/, /\bHippoRAG\b/, /\bColPali\b/, /\bAstute RAG\b/, /\bCRAG\b/,
      /\bgaps_design\b/, /\barchitecture\.md\b/,
      // "corrections" is fine as plain English; only flag the table reference
      /\bcorrections table\b/i, /\blogged to corrections\b/i,
    ];
    const hits = [];
    forbidden.forEach((re) => {
      const m = bodyText.match(re);
      if (m) hits.push(m[0]);
    });
    findings.push({
      id: 'copy.no-roadmap-leakage',
      ok: hits.length === 0,
      detail: hits.length === 0 ? 'no engineering-roadmap vocabulary in user-facing copy' : `forbidden vocab found: ${[...new Set(hits)].join(', ')}`,
    });

    return findings;
  });
}

function statusGlyph(ok) {
  return ok ? '✓' : '✗';
}

async function qaPage(browser, pageFile) {
  const url = 'file://' + resolve(ROOT, pageFile);
  const pageStem = pageFile.replace(/\.html$/, '');
  const reportLines = [];
  reportLines.push(`# Visual QA report — \`${pageFile}\``);
  reportLines.push('');
  reportLines.push(`Generated: ${new Date().toISOString()}`);
  reportLines.push('');
  reportLines.push(`## Viewports captured`);
  reportLines.push('');

  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    const consoleErrors = [];
    page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(`console.error: ${msg.text()}`);
    });

    await page.goto(url, { waitUntil: 'networkidle' });
    // give lucide a moment to inject svgs
    await page.waitForTimeout(400);

    const shotPath = join(SCREENS_DIR, `${pageStem}-${vp.name}.png`);
    await page.screenshot({ path: shotPath, fullPage: true });

    const checks = await runChecks(page);

    reportLines.push(`### ${vp.name} — ${vp.width}×${vp.height}`);
    reportLines.push('');
    reportLines.push(`![${pageStem}-${vp.name}](../screens/${pageStem}-${vp.name}.png)`);
    reportLines.push('');
    reportLines.push('| Check | Status | Detail |');
    reportLines.push('|---|---|---|');
    for (const c of checks) {
      reportLines.push(`| \`${c.id}\` | ${statusGlyph(c.ok)} | ${c.detail} |`);
    }
    if (consoleErrors.length) {
      reportLines.push('');
      reportLines.push('**Console errors:**');
      reportLines.push('```');
      consoleErrors.forEach((e) => reportLines.push(e));
      reportLines.push('```');
    }
    reportLines.push('');

    await ctx.close();
  }

  reportLines.push('---');
  reportLines.push('');
  reportLines.push('## Manual checklist (apply by eye to each screenshot above)');
  reportLines.push('');
  reportLines.push('See [`prototype/qa_checklist.md`](../../qa_checklist.md) for the full list. Tick when reviewed:');
  reportLines.push('');
  reportLines.push('- [ ] §1 Sidebar / left nav');
  reportLines.push('- [ ] §2 Top bar / header');
  reportLines.push('- [ ] §3 Primary content area');
  reportLines.push('- [ ] §4 Right panel (if present)');
  reportLines.push('- [ ] §5 Interactive elements');
  reportLines.push('- [ ] §6 Icons & imagery');
  reportLines.push('- [ ] §7 Typography & color');
  reportLines.push('- [ ] §8 Empty / loading / error states');
  reportLines.push('- [ ] §9 Information density');
  reportLines.push('- [ ] §10 Responsive');
  reportLines.push('');
  reportLines.push('**Sign-off:** _name_ _date_');
  reportLines.push('');

  const reportPath = join(REPORTS_DIR, `${pageStem}.md`);
  writeFileSync(reportPath, reportLines.join('\n'));
  return { pageFile, reportPath };
}

const arg = process.argv[2];
const pages = listPages(arg);
console.log(`QA pass on ${pages.length} page(s): ${pages.join(', ')}`);

const browser = await chromium.launch();
const results = [];
for (const p of pages) {
  process.stdout.write(`  ${p} … `);
  try {
    const r = await qaPage(browser, p);
    results.push(r);
    console.log('done');
  } catch (e) {
    console.log(`FAILED: ${e.message}`);
  }
}
await browser.close();

// summary index
const summary = ['# QA Run Summary', '', `Generated: ${new Date().toISOString()}`, ''];
for (const r of results) {
  summary.push(`- [${r.pageFile}](${r.pageFile.replace(/\.html$/, '.md')})`);
}
writeFileSync(join(REPORTS_DIR, 'index.md'), summary.join('\n'));
console.log(`\nReports → prototype/qa/reports/`);
console.log(`Screens → prototype/qa/screens/`);
