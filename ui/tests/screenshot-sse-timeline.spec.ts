/**
 * Screenshot harness for the SSE pipeline timeline.
 * Captures both the LIVE timeline (while pending) and the post-done
 * timeline inside the "How I answered" inspector.
 */

import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 1600, height: 1100 } });


test("live pipeline timeline shows each backend stage as it happens", async ({
  page,
}) => {
  await page.goto("/chat");
  await page.getByTestId("chat-input").fill(
    "What is the payment due period in the MSA?",
  );
  await page.getByTestId("chat-send").click();

  // Wait for the live timeline to appear with at least a few events.
  await expect(page.getByTestId("pipeline-timeline")).toBeVisible({
    timeout: 10_000,
  });
  await expect.poll(
    async () => page.locator('[data-event]').count(),
    { timeout: 8_000 },
  ).toBeGreaterThanOrEqual(4);

  // Snapshot mid-flight while still pending.
  await page.screenshot({
    path: "tests/artifacts/sse-timeline-live.png",
    fullPage: true,
  });

  // Wait for the answer to finalise.
  await expect(page.getByTestId("answer-text")).toBeVisible({ timeout: 30_000 });

  // Open the "How I answered" inspector to see the persisted trace.
  await page.locator("summary", { hasText: "How I answered" }).first().click();
  await page.waitForTimeout(300);
  await page.screenshot({
    path: "tests/artifacts/sse-timeline-inspector.png",
    fullPage: true,
  });
});
