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


/**
 * R1 verification — drive a question that should retrieve both MSA and
 * Amendment chunks, confirm the orchestrator surfaces the chain-rule
 * conflict resolution in the response, and capture the rendered banner.
 *
 * Backend (port 8000) must be running with the demo corpus already
 * extracted (atomic_units present on vertex-msa.pdf + vertex-amendment.txt
 * AND the two files chained via the E4 fix from PR6). The Playwright
 * config launches the Next.js dev server.
 */
test("conflict-resolution banner renders for MSA ↔ Amendment payment terms", async ({
  page,
}) => {
  await page.goto("/chat", { waitUntil: "networkidle" });

  await page
    .getByTestId("chat-input")
    .fill("What were the original payment terms in the MSA before any amendments?");
  await page.getByTestId("chat-send").click();

  // Wait for the assistant turn to land. The answer-card datatestid is
  // emitted only after the response arrives.
  await expect(page.getByTestId("answer-card")).toBeVisible({ timeout: 30_000 });

  // Banner shows up — exactly one conflict resolved (payment_terms.payment_due_days).
  const banner = page.getByTestId("conflict-resolutions");
  await expect(banner).toBeVisible({ timeout: 10_000 });
  await expect(banner).toContainText(/Resolved 1 conflict/i);
  await expect(banner).toContainText("payment_terms.payment_due_days");
  await expect(banner).toContainText("via chain");

  await page.screenshot({
    path: "tests/artifacts/chat-conflict-resolution.png",
    fullPage: true,
  });
});
