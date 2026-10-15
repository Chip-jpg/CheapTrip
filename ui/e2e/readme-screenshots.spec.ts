/**
 * The README's pictures of the app, on the demo data (scripts/demo_data.py). Skipped in the
 * normal run; to take them again after the screens change:
 *
 *   npm run build && README_SCREENSHOTS=1 npx playwright test readme-screenshots
 *
 * They're written to docs/images, at the window's default size (1440×900).
 */
import { fileURLToPath } from "node:url";
import { expect, test } from "./fixtures";

const OUT = fileURLToPath(new URL("../../docs/images/", import.meta.url));

test.skip(!process.env.README_SCREENSHOTS, "set README_SCREENSHOTS=1 to take the README's pictures");

async function settle(page: import("@playwright/test").Page): Promise<void> {
  await expect(page.locator("main [aria-busy=true], main .animate-pulse")).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);  // charts finish drawing
}

for (const theme of ["light", "dark"] as const) {
  test(`Deals (${theme})`, async ({ app }) => {
    await app.theme(theme);
    await app.go("#/deals");
    await expect(app.page.locator("main [data-testid=deal-card]").first()).toBeVisible();
    await settle(app.page);
    await app.page.screenshot({ path: `${OUT}deals-${theme}.png` });
  });
}

test("a deal's details", async ({ app }) => {
  await app.page.setViewportSize({ width: 1440, height: 1300 });  // tall enough for its charts
  await app.theme("dark");
  await app.go("#/deals");
  await app.page.locator("main [data-testid=deal-card]").first().getByRole("link", { name: /Details/ }).click();
  await expect(app.page.getByRole("dialog").getByText("Why it's here")).toBeVisible();
  await settle(app.page);
  await app.page.getByRole("dialog").screenshot({ path: `${OUT}deal-details.png` });
});

test("Destinations", async ({ app }) => {
  await app.theme("light");
  await app.go("#/destinations");
  await settle(app.page);
  await app.page.screenshot({ path: `${OUT}destinations.png` });
});
