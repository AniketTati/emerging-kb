/**
 * Screenshot the new chat empty state + the doc-filter popover open +
 * a real markdown-rendered answer.
 */

import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 1600, height: 1100 } });


test("empty state + suggestion chips clickable", async ({ page }) => {
  await page.goto("/chat");

  // Suggestion chips should be buttons.
  await expect(page.getByTestId("chat-suggestion").first()).toBeVisible();

  await page.screenshot({
    path: "tests/artifacts/chat-empty-state-v2.png",
    fullPage: true,
  });
});


test("doc-filter popover opens + lists files", async ({ page }) => {
  await page.goto("/chat");

  await page.getByTestId("chat-doc-filter").click();
  await expect(page.getByTestId("chat-doc-filter-popover")).toBeVisible();

  // At least one file should render.
  await expect(
    page.getByTestId("chat-doc-filter-item").first(),
  ).toBeVisible({ timeout: 5_000 });

  await page.screenshot({
    path: "tests/artifacts/chat-doc-filter-open.png",
    fullPage: true,
  });
});


test("markdown answer renders structure + clickable chips", async ({
  page,
}) => {
  await page.goto("/chat");
  await page.getByTestId("chat-input").fill(
    "Tell me about the MSA between NorthWind and Vertex including payment terms.",
  );
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("answer-text")).toBeVisible({ timeout: 30_000 });
  // The answer body should be inside a .prose-styled div now.
  await expect(page.locator(".prose")).toBeVisible();

  await page.screenshot({
    path: "tests/artifacts/chat-markdown-answer.png",
    fullPage: true,
  });
});
