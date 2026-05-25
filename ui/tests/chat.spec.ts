import { test, expect } from "@playwright/test";

/**
 * Visual smoke for /chat — no backend needed (POST /chat only fires on
 * Send, which we don't trigger here).
 */

test("chat page renders the composer, thread, and citations panel", async ({
  page,
}) => {
  await page.goto("/chat");

  // Empty state copy.
  await expect(
    page.getByText("Ask anything about your knowledge base"),
  ).toBeVisible();
  await expect(page.getByTestId("chat-empty-state")).toBeVisible();

  // Composer with textarea + Send.
  await expect(page.getByTestId("chat-input")).toBeVisible();
  await expect(page.getByTestId("chat-send")).toBeVisible();
  await expect(page.getByTestId("chat-send")).toBeDisabled();

  // Right rail.
  await expect(page.getByText(/Citations will appear here/)).toBeVisible();

  await page.screenshot({
    path: "tests/artifacts/chat-empty.png",
    fullPage: true,
  });
});

test("composer enables Send when text is entered, disables when empty", async ({
  page,
}) => {
  // `waitUntil: "networkidle"` lets Next.js hydration complete before we
  // start typing. Without it, Playwright's `fill()` races ahead of
  // hydration: the DOM gets the value but React doesn't see the change
  // event, so the controlled `value` state stays empty and the Send
  // button (whose `disabled` is derived from `!value.trim()`) stays
  // disabled. Real users type slowly enough that this race never bites.
  await page.goto("/chat", { waitUntil: "networkidle" });
  const input = page.getByTestId("chat-input");
  const send = page.getByTestId("chat-send");

  await expect(send).toBeDisabled();
  await input.fill("hello");
  await expect(send).toBeEnabled();
  await input.fill("");
  await expect(send).toBeDisabled();
});
