/**
 * End-to-end: the built screens on the real engine and API (B47), with a few days
 * of seeded history in a throwaway folder. Every screen from the sidebar, every
 * control on it, in both themes and at every text size.
 *
 *   npm run build && npx playwright test          (Python and requirements-dev.txt installed)
 *
 * The app's real window (Edge WebView2) is driven separately, on Windows: e2e/window-smoke.mjs.
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  testMatch: "*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,  // one engine and one database, shared: the tests take turns
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
