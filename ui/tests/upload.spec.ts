import { test, expect } from "@playwright/test";

/**
 * E2E — /upload page. Asserts the rich columns + row-expand that the
 * Wave-B audit reintroduced (Type · Status · Detected · 5-stage timeline)
 * against the demo-corpus fixture (5 vertex-* files plus a `tiny.pdf`
 * with source_authority=0.2 that triggers the Needs-attention filter).
 *
 * Backend (port 8000) must be running; Playwright config launches the
 * Next.js dev server.
 */

test("upload page renders sidebar, topbar, dropzone, and table", async ({
  page,
}) => {
  await page.goto("/upload");

  await expect(page.getByText("Upload documents")).toBeVisible();

  const dropzone = page.getByTestId("dropzone");
  await expect(dropzone).toBeVisible();
  await expect(dropzone).toHaveText(/Drop files here/);

  // Wait for the corpus to hydrate so the artifact captures the rich state.
  await expect(
    page.locator('[data-testid="file-row"]').first(),
  ).toBeVisible({ timeout: 10_000 });

  await page.screenshot({
    path: "tests/artifacts/upload-page.png",
    fullPage: true,
  });
});

test("upload page redirects from root", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/.*\/upload$/);
});

test("table renders the demo corpus with Type + Status columns", async ({
  page,
}) => {
  await page.goto("/upload");

  // The MSA pdf should appear in the table once /files lands.
  const msaRow = page.locator('[data-testid="file-row"]', {
    hasText: "vertex-msa.pdf",
  });
  await expect(msaRow).toBeVisible({ timeout: 10_000 });

  // Type column shows Gemini's inferred_doc_type (master_services_agreement).
  await expect(msaRow).toContainText(/master_services_agreement/);

  // The pricing xlsx renders with the price_sheet doc-type.
  const priceRow = page.locator('[data-testid="file-row"]', {
    hasText: "vertex-pricing-tiers.xlsx",
  });
  await expect(priceRow).toContainText(/price_sheet/);
});

test("Needs-attention chip surfaces the low-authority file", async ({
  page,
}) => {
  await page.goto("/upload");

  // Wait for rows to materialize before clicking the chip — otherwise the
  // filter applies to an empty list and the assertion races the fetch.
  await expect(
    page.locator('[data-testid="file-row"]').first(),
  ).toBeVisible({ timeout: 10_000 });

  // Chip is rendered with the label "Needs attention".
  const chip = page.getByRole("button", { name: /Needs attention/i });
  await expect(chip).toBeVisible();
  await chip.click();

  // After filter, tiny.pdf (authority 0.2) must be visible …
  await expect(
    page.locator('[data-testid="file-row"]', { hasText: "tiny.pdf" }),
  ).toBeVisible();

  // … and the live-corpus xlsx (authority 0.5, doc_status=live) must NOT.
  await expect(
    page.locator('[data-testid="file-row"]', {
      hasText: "vertex-pricing-tiers.xlsx",
    }),
  ).toHaveCount(0);
});

test("row expand fetches /files/:id/details and renders the timeline", async ({
  page,
}) => {
  await page.goto("/upload");

  const xlsxRow = page.locator('[data-testid="file-row"]', {
    hasText: "vertex-pricing-tiers.xlsx",
  });
  await expect(xlsxRow).toBeVisible({ timeout: 10_000 });

  // Click the row toggle (chevron button on the left edge).
  await xlsxRow.getByTestId("file-row-toggle").click();

  // The expanded panel is identified by data-testid. Wait for the
  // /files/:id/details fetch to land (Loading… → DetailBody).
  const detail = page.locator('[data-testid="file-row-detail"]').first();
  await expect(detail).toBeVisible();
  await expect(detail).toContainText(/Doc-type/, { timeout: 10_000 });

  // 5-stage timeline labels are rendered.
  await expect(detail).toContainText(/Parse/);
  await expect(detail).toContainText(/Contextualize/);
  await expect(detail).toContainText(/Extract/);
  await expect(detail).toContainText(/Resolve/);
  await expect(detail).toContainText(/Index/);

  // Rollup row exposes the extracted counts.
  await expect(detail).toContainText(/mentions/);
  await expect(detail).toContainText(/atomic units/);
  await expect(detail).toContainText(/entities/);
});
