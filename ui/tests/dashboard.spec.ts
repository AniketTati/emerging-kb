import { test, expect } from "@playwright/test";

/**
 * Visual + data-driven smoke for /dashboard.
 *
 * Two layers:
 *   1. Renders even on an empty workspace (default header copy + "no
 *      documents yet" hint).
 *   2. When the backend has data (the demo corpus has been ingested),
 *      stat cards reflect real counts and the doc-type breakdown shows
 *      the Gemini-classified labels.
 *
 * No external state needed — the page fetches from /dashboard/summary
 * and /dashboard/needs-attention on mount.
 */

const API = process.env.NEXT_PUBLIC_KB_API_URL || "http://localhost:8000";
const WORKSPACE = "00000000-0000-0000-0000-000000000001";


async function backendHasFiles(): Promise<number> {
  try {
    const resp = await fetch(`${API}/dashboard/summary`, {
      headers: { "X-Test-Workspace": WORKSPACE },
    });
    if (!resp.ok) return -1;
    const body = await resp.json();
    return typeof body.files_total === "number" ? body.files_total : -1;
  } catch {
    return -1;
  }
}


test("dashboard renders header + stat cards + breakdown cards", async ({
  page,
}) => {
  await page.goto("/dashboard", { waitUntil: "networkidle" });

  // Page chrome.
  await expect(page.getByText("Dashboard").first()).toBeVisible();

  // Body must render once the summary fetch resolves (no spinner stuck).
  const body = page.getByTestId("dash-body");
  await expect(body).toBeVisible({ timeout: 10_000 });

  // All four stat cards.
  await expect(page.getByTestId("stat-files")).toBeVisible();
  await expect(page.getByTestId("stat-queries")).toBeVisible();
  await expect(page.getByTestId("stat-conflicts")).toBeVisible();
  await expect(page.getByTestId("stat-corrections")).toBeVisible();

  // Four breakdown cards.
  await expect(page.getByTestId("breakdown-doctype")).toBeVisible();
  await expect(page.getByTestId("breakdown-status")).toBeVisible();
  await expect(page.getByTestId("breakdown-mode")).toBeVisible();
  await expect(page.getByTestId("breakdown-verdict")).toBeVisible();

  // Save a verified-state screenshot.
  await page.screenshot({
    path: "tests/artifacts/dashboard.png",
    fullPage: true,
  });
});


test("dashboard surfaces real demo-corpus counts when present", async ({
  page,
}) => {
  const filesTotal = await backendHasFiles();
  test.skip(
    filesTotal <= 0,
    "Requires backend with ingested data (run demo-corpus/ingest.py first)",
  );

  await page.goto("/dashboard", { waitUntil: "networkidle" });
  await expect(page.getByTestId("dash-body")).toBeVisible({ timeout: 10_000 });

  // Files stat card shows the real total.
  const filesCard = page.getByTestId("stat-files");
  await expect(filesCard).toContainText(String(filesTotal));

  // The doc-type breakdown shows at least one row from the Gemini
  // classifier (when the demo corpus is loaded, we see 5 distinct types
  // like master_services_agreement, email_thread, etc.).
  const docTypeRows = page.locator('[data-testid="breakdown-doctype-row"]');
  await expect(docTypeRows.first()).toBeVisible();
  expect(await docTypeRows.count()).toBeGreaterThan(0);
});


test("dashboard handles api error gracefully", async ({ page }) => {
  // Intercept the dashboard summary call and force a 500. The error
  // banner should appear instead of an infinite loading spinner.
  await page.route("**/dashboard/summary*", (route) =>
    route.fulfill({ status: 500, body: '{"detail":"injected failure"}' }),
  );
  await page.goto("/dashboard", { waitUntil: "networkidle" });
  await expect(page.getByTestId("dash-error")).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId("dash-error")).toContainText("Couldn't load");
});
