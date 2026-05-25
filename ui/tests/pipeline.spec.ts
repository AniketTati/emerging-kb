import { test, expect, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";

/**
 * End-to-end pipeline test: drives the FULL backend through the UI.
 *
 *   1. /upload page: upload a real PDF via the dropzone's hidden file input.
 *   2. SSE-driven status row reaches lifecycle_state="ready".
 *   3. Navigate to /chat.
 *   4. Send a chat query via the composer.
 *   5. Assert an assistant turn renders — either a grounded answer card,
 *      or (more likely with no LLM key) a refusal envelope. Either way,
 *      the pipeline ran end-to-end.
 *
 * Gated on RUN_PIPELINE_TEST=1 — skipped unless the backend is up. The
 * verify_phase_10b.sh script sets this after `docker compose up -d`.
 */

const RUN = process.env.RUN_PIPELINE_TEST === "1";
const API = process.env.NEXT_PUBLIC_KB_API_URL || "http://localhost:8000";

test.describe("UI-driven pipeline (drop → ready → ask → answer)", () => {
  test.skip(!RUN, "Set RUN_PIPELINE_TEST=1 with backend running to enable");

  // Worker-scoped backend liveness probe — fail loud if compose isn't up.
  test.beforeAll(async ({}, testInfo) => {
    const resp = await fetch(`${API}/health`).catch(() => null);
    if (!resp || !resp.ok) {
      throw new Error(
        `pipeline test requires backend at ${API}; got ${resp?.status ?? "no response"}`,
      );
    }
  });

  test("drop tiny.pdf via /upload, wait for ready, chat, see assistant turn", async ({
    page,
  }) => {
    // -------- 1) Pick up the real fixture from the backend test suite --------
    const fixturePath = path.resolve(__dirname, "../../tests/fixtures/tiny.pdf");
    if (!fs.existsSync(fixturePath)) {
      throw new Error(`fixture missing: ${fixturePath}`);
    }

    await page.goto("/upload");

    // The dropzone uses a hidden <input type="file">. Playwright can set
    // it directly — equivalent to a user picking the file.
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(fixturePath);

    // The new row appears once POST /files resolves.
    const row = page.getByTestId("file-row").first();
    await expect(row).toBeVisible({ timeout: 15_000 });

    // -------- 2) Wait until SSE drives the row to ready --------
    // tiny.pdf goes through the full chain (parse → ... → ready) which can
    // take 60–120s on a cold worker. Generous timeout.
    await expect.poll(
      async () => await row.getAttribute("data-state"),
      { timeout: 240_000, intervals: [1_000, 2_000, 5_000] },
    ).toBe("ready");

    // -------- 3) Navigate to /chat --------
    await page.click('a[href="/chat"]');
    await expect(page).toHaveURL(/.*\/chat$/);
    await expect(page.getByTestId("chat-empty-state")).toBeVisible();

    // -------- 4) Send a query via the composer --------
    const input = page.getByTestId("chat-input");
    await input.fill("Summarize the documents I uploaded.");
    await page.getByTestId("chat-send").click();

    // Composer disables while the request is in flight.
    await expect(page.getByTestId("chat-send")).toBeDisabled();

    // -------- 5) Assistant turn renders — refusal OR grounded --------
    const answer = page.getByTestId("answer-card").first();
    await expect(answer).toBeVisible({ timeout: 60_000 });

    // Either a grounded answer or a refusal envelope — the point is that
    // the orchestrator ran and the UI rendered the result.
    const refused = await answer.getAttribute("data-refused");
    expect(["true", "false"]).toContain(refused);

    // Save a final screenshot of the chat with answer rendered.
    await page.screenshot({
      path: "tests/artifacts/pipeline-final.png",
      fullPage: true,
    });
  });
});
