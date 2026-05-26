/**
 * One-shot screenshot harness for R1 superseded-citation UX.
 * Run via: npx playwright test tests/screenshot-r1.spec.ts --headed
 * (or headless — captures the same artifact).
 *
 * Not part of the regular suite — intentionally tied to live demo data.
 */

import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 1600, height: 1100 } });

test("R1 — superseded citation ribbon renders in chat", async ({ page }) => {
  page.on("console", (msg) => console.log("BROWSER:", msg.type(), msg.text()));
  page.on("pageerror", (err) => console.log("PAGE ERROR:", err.message));

  await page.goto("/chat");

  const input = page.getByTestId("chat-input");
  await input.fill(
    "Tell me about the MSA between NorthWind and Vertex including payment terms.",
  );
  await page.getByTestId("chat-send").click();

  // Wait for the answer card to render with a citation.
  await expect(
    page.locator('[data-testid="answer-card"]').first(),
  ).toBeVisible({ timeout: 30_000 });

  // Wait for at least one citation card on the right rail.
  await expect(
    page.locator('[data-testid="citation-card"]').first(),
  ).toBeVisible({ timeout: 15_000 });

  // Give the conflict-resolution chip a moment to render.
  await page.waitForTimeout(500);

  await page.screenshot({
    path: "tests/artifacts/r1-superseded-chat.png",
    fullPage: true,
  });

  // Also clip into the right-rail to make the superseded chip legible
  // in the artifact.
  const rail = page.locator('[data-testid="citation-card"]').first().locator("xpath=ancestor::*[3]");
  await rail.screenshot({ path: "tests/artifacts/r1-citations-panel.png" });
});
