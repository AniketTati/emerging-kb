/**
 * Screenshot harness verifying the two chat-UX fixes:
 *   1. Inline [N] citation chip click → scrolls + flashes right-rail card
 *   2. Summarize-style queries no longer refuse on the CRAG gate
 */

import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 1600, height: 1100 } });


test("chip click scrolls + flashes the matching citation card", async ({
  page,
}) => {
  await page.goto("/chat");
  await page.getByTestId("chat-input").fill(
    "Tell me about the MSA between NorthWind and Vertex including payment terms.",
  );
  await page.getByTestId("chat-send").click();

  // Wait for answer + at least 2 citation cards.
  await expect(page.getByTestId("answer-text")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[data-testid="citation-card"]').nth(1)).toBeVisible({
    timeout: 10_000,
  });

  // Click the second inline [N] chip in the answer body.
  const chips = page.locator('[data-citation-index]');
  const count = await chips.count();
  expect(count).toBeGreaterThanOrEqual(2);
  await chips.nth(1).click();

  // The matching card should briefly carry data-citation-flash.
  // Use polling because the flash auto-clears in 1500ms.
  await expect.poll(
    async () => await page.locator("[data-citation-flash]").count(),
    { timeout: 1000 },
  ).toBeGreaterThan(0);

  await page.screenshot({
    path: "tests/artifacts/chat-chip-flash.png",
    fullPage: true,
  });
});


test("summarize query does not refuse on CRAG gate", async ({ page }) => {
  await page.goto("/chat");
  await page.getByTestId("chat-input").fill("Summarize the documents");
  await page.getByTestId("chat-send").click();

  // Wait for the answer card to render (refused or not).
  const card = page.getByTestId("answer-card");
  await expect(card).toBeVisible({ timeout: 30_000 });

  // Should NOT be marked refused.
  await expect(card).toHaveAttribute("data-refused", "false");

  await expect(page.getByTestId("answer-text")).toBeVisible();
  const txt = await page.getByTestId("answer-text").innerText();
  expect(txt.length).toBeGreaterThan(50);

  await page.screenshot({
    path: "tests/artifacts/chat-summarize-works.png",
    fullPage: true,
  });
});
