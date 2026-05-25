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
  await page.goto("/chat");
  const input = page.getByTestId("chat-input");
  const send = page.getByTestId("chat-send");

  await expect(send).toBeDisabled();
  await input.fill("hello");
  await expect(send).toBeEnabled();
  await input.fill("");
  await expect(send).toBeDisabled();
});
