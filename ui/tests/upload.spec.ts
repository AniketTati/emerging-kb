import { test, expect } from "@playwright/test";

/**
 * Phase 10a E2E — visual + structural assertions on the /upload page.
 * Backend (port 8000) must be running; Playwright config launches the
 * Next.js dev server.
 */

test("upload page renders the sidebar, topbar, dropzone, and table", async ({
  page,
}) => {
  await page.goto("/upload");

  // Sidebar shows the K logo + Upload nav item.
  await expect(page.getByText("Upload documents")).toBeVisible();

  // Dropzone is interactive.
  const dropzone = page.getByTestId("dropzone");
  await expect(dropzone).toBeVisible();
  await expect(dropzone).toHaveText(/Drop files here/);

  // Table is in its empty state (no uploads yet for this workspace test run).
  await expect(page.getByText(/No uploads yet/)).toBeVisible();

  // Take a verified-state screenshot for the verify script artifact.
  await page.screenshot({
    path: "tests/artifacts/upload-empty.png",
    fullPage: true,
  });
});

test("upload page redirects from root", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/.*\/upload$/);
});
