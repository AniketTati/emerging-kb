/**
 * One-shot screenshot harness for R5 layout strip + mini-map in doc-detail.
 */

import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 1600, height: 1100 } });

test("R5 — layout strip with element breakdown + minimap renders", async ({
  page,
}) => {
  await page.goto("/files/db682fe4-b688-418b-b200-975591380e41");

  // Wait for the doc-detail page to render.
  await expect(page.getByTestId("doc-detail-filename")).toBeVisible({
    timeout: 15_000,
  });

  // Click the "Parsed text" accordion to open it.
  const accordion = page.getByTestId("doc-detail-source");
  await accordion.click();

  // The layout strip should now appear inside the open accordion.
  await expect(page.getByTestId("layout-strip")).toBeVisible({
    timeout: 10_000,
  });

  // Give the mini-map SVG a beat to render.
  await page.waitForTimeout(400);

  await page.screenshot({
    path: "tests/artifacts/r5-layout-strip.png",
    fullPage: true,
  });

  // Also clip the layout strip alone for a legible artifact.
  await page.getByTestId("layout-strip").screenshot({
    path: "tests/artifacts/r5-layout-strip-zoom.png",
  });
});
