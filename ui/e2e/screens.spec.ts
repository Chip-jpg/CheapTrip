/** Every screen, reached the way people reach them: the sidebar, a deal card, the setup wizard. */
import { SETTINGS_SECTIONS, SIDEBAR, expect, expectAlive, sidebarLink, test } from "./fixtures";

for (const theme of ["dark", "light"] as const) {
  test(`every sidebar button opens its screen (${theme})`, async ({ app }, info) => {
    await app.theme(theme);
    for (const label of SIDEBAR) {
      await sidebarLink(app.page, label).click();
      await expectAlive(app.page);
      await info.attach(`${theme} · ${label}`, { body: await app.page.screenshot(), contentType: "image/png" });
    }
    expect(app.problems).toEqual([]);
  });
}

test("leaving the setup wizard by any sidebar button keeps the app (v0.8's black window)", async ({ app }) => {
  for (const label of SIDEBAR.filter((l) => l !== "Setup wizard")) {
    await app.go("#/setup");
    await expect(app.page.getByRole("heading", { name: "Setup wizard" })).toBeVisible();
    await app.page.getByRole("button", { name: /Let's start|Next/ }).first().click();  // a new step scrolls to the top
    await sidebarLink(app.page, label).click();
    await expectAlive(app.page);
  }
  expect(app.problems).toEqual([]);
});

test("a deal's details slide over the list and close again", async ({ app }) => {
  await app.go("#/deals");
  await app.page.locator("main [data-testid=deal-card]").first().getByRole("link", { name: /Details/ }).click();
  const panel = app.page.getByRole("dialog");
  await expect(panel.getByText("Why it's here")).toBeVisible();
  await panel.getByRole("button", { name: "Close" }).last().click();
  await expect(panel).toHaveCount(0);
  await app.page.locator("main [data-testid=deal-row]").first().click();
  await expect(app.page.getByRole("dialog")).toBeVisible();
  await app.page.keyboard.press("Escape");
  await expect(app.page.getByRole("dialog")).toHaveCount(0);
  await expectAlive(app.page);
  expect(app.problems).toEqual([]);
});

test("every settings section opens from its menu", async ({ app }) => {
  for (const section of SETTINGS_SECTIONS) {
    await app.go(`#/settings/${section}`);
    await expect(app.page.locator(`nav[aria-label="Settings sections"] a[aria-current="page"]`)).toBeVisible();
    await expectAlive(app.page);
  }
  expect(app.problems).toEqual([]);
});

test("no screen scrolls sideways, on a small window at every text size", async ({ app }, info) => {
  await app.page.setViewportSize({ width: 1024, height: 700 });
  const screens = ["#/deals", "#/destinations", "#/activity", "#/setup", "#/notifications",
    ...SETTINGS_SECTIONS.map((s) => `#/settings/${s}`)];
  for (const size of ["Largest", "Large", "Standard"]) {
    await app.go("#/settings/app");
    await app.page.getByRole("radiogroup", { name: "Text size" }).getByRole("radio", { name: size, exact: true }).click();
    for (const hash of screens) {
      await app.go(hash);
      await expectAlive(app.page);
      const sideways = await app.page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth);
      expect(sideways, `${hash} at ${size} scrolls sideways`).toBeLessThanOrEqual(1);
    }
    await info.attach(`1024 px · ${size}`, { body: await app.page.screenshot(), contentType: "image/png" });
  }
  await expect(app.page.locator("html")).not.toHaveAttribute("data-text-size");
  expect(app.problems).toEqual([]);
});
