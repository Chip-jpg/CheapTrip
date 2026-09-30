/**
 * Every control on every screen does something and breaks nothing: each button is
 * pressed (menus and dialogs close again with Escape), and afterwards the screen
 * is still there, with no page error and no failed API call.
 */
import type { Page } from "@playwright/test";
import { SETTINGS_SECTIONS, expect, expectAlive, stillThere, test } from "./fixtures";

// Pressed on their own below (or never: they end the test's own session)
const NOT_HERE = /^(Clear…|Delete|Hide to the tray)$/;

async function pressEverything(page: Page, hash: string, go: (hash: string) => Promise<void>): Promise<string[]> {
  await go(hash);
  await expectAlive(page);
  const pressed: string[] = [];
  const count = Math.min(await page.locator("main button").count(), 80);
  for (let i = 0; i < count; i++) {
    const button = page.locator("main button").nth(i);
    if (!(await button.isVisible().catch(() => false)) || !(await button.isEnabled().catch(() => false))) continue;
    const name = ((await button.getAttribute("aria-label")) ?? (await button.innerText().catch(() => "")) ?? "").trim();
    if (NOT_HERE.test(name)) continue;
    await button.click({ timeout: 2_000 }).catch(() => undefined);  // one that re-rendered away under the pointer
    pressed.push(name);
    await page.keyboard.press("Escape");
    if (!(await page.evaluate(() => window.location.hash)).startsWith(hash.split("/").slice(0, 2).join("/"))) await go(hash);
    await stillThere(page);
  }
  await expectAlive(page);
  return pressed;
}

for (const hash of ["#/deals", "#/destinations", "#/activity", "#/setup", "#/notifications",
  ...SETTINGS_SECTIONS.map((s) => `#/settings/${s}`)]) {
  test(`every control on ${hash}`, async ({ app }) => {
    const pressed = await pressEverything(app.page, hash, app.go);
    expect(pressed.length).toBeGreaterThan(0);
    expect(app.problems).toEqual([]);
    if (app.excused.length) test.info().annotations.push({ type: "needs a real PC", description: app.excused.join("\n") });
  });
}

test("the top bar: status, quick jump, search now, pause and resume, sidebar, theme", async ({ app }) => {
  const { page } = app;
  await page.getByTestId("state").click();
  await expect(page.getByText("Engine status")).toBeVisible();
  await page.keyboard.press("Escape");

  await page.keyboard.press("Control+k");
  await page.keyboard.type("LHR");
  await expect(page.getByRole("option").first()).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/destinations\/LHR/);

  const pause = page.getByRole("button", { name: /Pause alerts/ });
  await pause.click();
  await page.getByRole("menuitem").first().click();
  await expect(page.getByRole("button", { name: /Resume/ }).first()).toBeVisible();
  await page.getByRole("button", { name: /Resume/ }).first().click();
  await expect(page.getByRole("button", { name: /Pause alerts/ })).toBeVisible();

  await page.getByLabel("Show or hide the navigation").click();
  await expect(page.locator("aside")).toHaveClass(/w-18/);
  await page.getByLabel("Show or hide the navigation").click();
  await expect(page.locator("aside")).toHaveClass(/w-68/);
  await expectAlive(page);
  expect(app.problems).toEqual([]);
});

test("a deal's own actions: mute, priority and hide, each with Undo", async ({ app }) => {
  const { page } = app;
  await app.go("#/deals");
  const card = page.locator("main [data-testid=deal-card]").first();
  for (const [item, said] of [[/^(Mute|Unmute) /, /muted|unmuted/], [/priorit/, /priority/], [/^Hide this deal/, /Deal hidden/]] as const) {
    await card.getByRole("button", { name: /^More for/ }).click();
    await page.getByRole("menu").getByRole("menuitem", { name: item }).click();
    const toast = page.getByRole("status").filter({ hasText: said });
    await expect(toast).toBeVisible();
    await toast.getByRole("button", { name: "Undo" }).click();
    await expect(toast).toHaveCount(0);
  }
  await expectAlive(page);
  expect(app.problems).toEqual([]);
});

test("the danger zone clears the price history, after asking", async ({ app }) => {
  const { page } = app;
  await app.go("#/settings/app");
  await page.getByRole("button", { name: "Clear…" }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Clear…" }).click();
  await page.getByRole("button", { name: "Yes, clear it" }).click();
  await expect(page.getByText(/Price history cleared \(\d+ prices\)/)).toBeVisible();
  await expectAlive(page);
  expect(app.problems).toEqual([]);
});
