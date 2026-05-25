import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 10a E2E config — boots the Next.js dev server, expects the backend
 * at `KB_API_URL` (default http://localhost:8000) to already be running.
 * Verify script handles backend lifecycle.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  // The pipeline test posts to /chat which (with KB_GEMINI_API_KEY set)
  // hits real Gemini and the API's per-request DB transaction. Concurrent
  // /chat workers occasionally collide mid-transaction and the second
  // request comes back as 500 → "Failed to fetch" in the browser. Single
  // worker keeps the suite deterministic without papering over a real
  // backend race we still want to surface if it gets worse.
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: "list",
  use: {
    baseURL: process.env.KB_UI_URL || "http://localhost:3000",
    headless: true,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: process.env.KB_UI_URL || "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
