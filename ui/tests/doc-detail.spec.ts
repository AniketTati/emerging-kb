import { test, expect } from "@playwright/test";

/**
 * E2E — /files/[id] doc-detail audit view.
 *
 * Uses the demo corpus fixture. Asserts the page renders the header card,
 * the featured-clause hero, and that each accordion lazy-loads on click.
 * Also asserts that clicking a filename on /upload navigates here.
 */

const XLSX_NAME = "vertex-pricing-tiers.xlsx";
const PDF_NAME = "vertex-msa.pdf";

test("upload table filename click navigates to /files/[id]", async ({ page }) => {
  await page.goto("/upload");

  const xlsxLink = page.locator('[data-testid="file-row-link"]', {
    hasText: XLSX_NAME,
  });
  await expect(xlsxLink).toBeVisible({ timeout: 10_000 });
  await xlsxLink.click();

  await expect(page).toHaveURL(/.*\/files\/[a-f0-9-]+$/);
  await expect(page.getByTestId("doc-detail-filename")).toHaveText(XLSX_NAME, {
    timeout: 15_000,
  });
});

test("doc-detail header surfaces inferred_doc_type + authority + counts", async ({
  page,
}) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: XLSX_NAME })
    .click();
  await expect(page).toHaveURL(/.*\/files\/[a-f0-9-]+$/);

  // Doc-type pill from Gemini
  await expect(page.getByText(/price_sheet/)).toBeVisible();
  // Authority badge
  await expect(page.getByText(/auth 0\.5/)).toBeVisible();
  // Inline count pills in the slim sticky header
  await expect(page.getByText(/units/).first()).toBeVisible();
  await expect(page.getByText(/mentions/).first()).toBeVisible();
});

test("two-pane: native xlsx table on left, extraction accordions on right", async ({
  page,
}) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: XLSX_NAME })
    .click();

  // Left pane = native source viewer, kind="xlsx".
  const source = page.getByTestId("source-viewer");
  await expect(source).toHaveAttribute("data-kind", "xlsx");
  await expect(source).toContainText(/Tiers|NorthWind Commit/, { timeout: 10_000 });

  // Right pane = featured-clause hero + accordions.
  const extracted = page.getByTestId("extracted-pane");
  await expect(extracted).toContainText(
    /Featured because this unit has the highest rarity/,
    { timeout: 10_000 },
  );

  await page.screenshot({
    path: "tests/artifacts/doc-detail-xlsx-twopane.png",
    fullPage: true,
  });
});

test("native eml view renders From/To/Subject header", async ({ page }) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: "vertex-sales-thread.eml" })
    .click();
  const source = page.getByTestId("source-viewer");
  await expect(source).toHaveAttribute("data-kind", "email");
  await expect(source).toContainText(/From/, { timeout: 10_000 });
  await expect(source).toContainText(/Subject/);
  await page.screenshot({
    path: "tests/artifacts/doc-detail-eml-twopane.png",
    fullPage: true,
  });
});

test("native markdown view renders formatted .md", async ({ page }) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: "vertex-eval-notes.md" })
    .click();
  const source = page.getByTestId("source-viewer");
  await expect(source).toHaveAttribute("data-kind", "markdown");
  // Rendered markdown should produce real headings/paragraphs, not raw `#` text.
  await expect(source.locator("h1, h2, h3").first()).toBeVisible({ timeout: 10_000 });
});

test("native pdf view renders the first page via PDF.js", async ({ page }) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: PDF_NAME })
    .click();
  const source = page.getByTestId("source-viewer");
  await expect(source).toHaveAttribute("data-kind", "pdf");
  // PdfView mounts a header + pager once the blob + react-pdf bundle resolve.
  await expect(page.getByTestId("pdf-view")).toBeVisible({ timeout: 15_000 });
  await expect(source).toContainText(/page 1 \//);
  // Wait for PDF.js to finish rendering the page canvas (text layer DOM
  // appears after the page is laid out).
  await page.waitForSelector(".react-pdf__Page__textContent", {
    timeout: 15_000,
  });
  await page.screenshot({
    path: "tests/artifacts/doc-detail-pdf-twopane.png",
    fullPage: true,
  });
});

test("accordions lazy-load when clicked (source + fields + mentions)", async ({
  page,
}) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: XLSX_NAME })
    .click();

  // Source accordion: open, expect parsed page text to appear.
  const source = page.getByTestId("doc-detail-source");
  await source.locator("button").first().click();
  await expect(source).toContainText(/page \d+ of/);

  // Proposed fields accordion.
  const fields = page.getByTestId("doc-detail-fields");
  await fields.locator("button").first().click();
  // The pricing xlsx has fields like annual_commit_usd / committed_sites.
  await expect(fields).toContainText(/annual_commit_usd|committed_sites/, {
    timeout: 10_000,
  });

  // Mentions accordion.
  const mentions = page.getByTestId("doc-detail-mentions");
  await mentions.locator("button").first().click();
  await expect(mentions).toContainText(/NorthWind|Mumbai/, { timeout: 10_000 });
});

test("PDF doc surfaces the L2 mentions gap visibly", async ({ page }) => {
  // The audit at docs/upload_flow_audit.md §7 documents that the Gemini
  // mentions extractor returns 0 results on Docling-parsed PDFs. The
  // doc-detail page makes that gap explicit (warn badges).
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: PDF_NAME })
    .click();
  await expect(page).toHaveURL(/.*\/files\/[a-f0-9-]+$/);

  // Mentions accordion header should show "0" and warn icon.
  const mentions = page.getByTestId("doc-detail-mentions");
  await expect(mentions).toBeVisible({ timeout: 10_000 });
  await mentions.locator("button").first().click();
  await expect(mentions).toContainText(/Nothing here for this doc|0/);

  await page.screenshot({
    path: "tests/artifacts/doc-detail-pdf-gap.png",
    fullPage: true,
  });
});

test("click a mention → source pane highlights the cited cell (xlsx)", async ({
  page,
}) => {
  // Pick a mention we know is in the raw cells (not from a contextual
  // prefix). "Mumbai" appears in the Region column of the Tiers sheet.
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: XLSX_NAME })
    .click();
  const mentions = page.getByTestId("doc-detail-mentions");
  await mentions.locator("button").first().click();
  await mentions.getByRole("button", { name: /GPE\s+Mumbai/i }).first().click();

  // Source pane header shows the citing banner.
  await expect(page.getByText(/↳ citing:/)).toBeVisible();
  // The matching cell gets highlighted.
  await expect(page.locator(".kb-cited-cell").first()).toBeVisible({
    timeout: 5_000,
  });

  await page.screenshot({
    path: "tests/artifacts/doc-detail-cited.png",
    fullPage: true,
  });
});

test("click a mention not in cells → 'not in source body' banner", async ({
  page,
}) => {
  // First mention in the pricing xlsx is "NorthWind" which the
  // contextualizer adds as a prefix; the raw cells don't contain it.
  // The UI surfaces this gap rather than silently failing.
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: XLSX_NAME })
    .click();
  const mentions = page.getByTestId("doc-detail-mentions");
  await mentions.locator("button").first().click();
  await mentions.getByRole("button", { name: /ORG\s+NorthWind/i }).first().click();
  await expect(page.getByText(/not found in this file's cells/i)).toBeVisible({
    timeout: 5_000,
  });
});

test("processing log renders lifecycle events with relative timing", async ({
  page,
}) => {
  await page.goto("/upload");
  await page
    .locator('[data-testid="file-row-link"]', { hasText: XLSX_NAME })
    .click();
  const log = page.getByTestId("doc-detail-processing");
  await log.locator("button").first().click();
  // Events render as "+12.3s · event_name · from → to".
  await expect(log).toContainText(/\+\d+\.\d+s/);
});
