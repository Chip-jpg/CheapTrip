/**
 * The app open in a page, with everything that goes wrong on it collected: page errors,
 * console errors and failed API calls. A test ends by expecting that list to be empty.
 */
import { test as base, expect, type Page, type Response } from "@playwright/test";

export const SIDEBAR = ["Deals", "Destinations", "Activity", "Settings", "Setup wizard", "Notifications"] as const;
export const SETTINGS_SECTIONS = ["trips", "alerts", "notifications", "keys", "sources", "app"] as const;

/**
 * What only works on a real PC (a folder window, a notification, a Telegram bot, GitHub): on a
 * test machine these may fail, but must say why in words the screens can show.
 */
const DEPENDS_ON_THE_PC = [
  "POST /api/v1/app/open", "POST /api/v1/setup/test-notification", "POST /api/v1/setup/test-telegram",
  "POST /api/v1/setup/check", "GET /api/v1/app/update",
];

export interface App {
  page: Page;
  problems: string[];
  /** Failures of what depends on the PC, each with the message the screens showed */
  excused: string[];
  go: (hash: string) => Promise<void>;
  theme: (theme: "light" | "dark") => Promise<void>;
}

export const test = base.extend<{ app: App }>({
  app: async ({ page }, use) => {
    const { port, token } = JSON.parse(process.env.CHEAPTRIP_E2E ?? "{}");
    const problems: string[] = [];
    const excused: string[] = [];
    page.on("pageerror", (err) => problems.push(`page error: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error" && !msg.text().startsWith("Failed to load resource")) problems.push(`console: ${msg.text()}`);
    });
    page.on("response", (resp: Response) => {
      const url = new URL(resp.url());
      if (!url.pathname.startsWith("/api/") || resp.status() < 400) return;
      const call = `${resp.request().method()} ${url.pathname}`;
      if (!DEPENDS_ON_THE_PC.includes(call)) {
        problems.push(`HTTP ${resp.status()} ${call}`);
        return;
      }
      resp.json().then((body: { error?: string }) => {
        if (body.error) excused.push(`${call}: ${body.error}`);
        else problems.push(`HTTP ${resp.status()} ${call} without a message`);
      }).catch(() => problems.push(`HTTP ${resp.status()} ${call} without a message`));
    });
    page.on("popup", (popup) => { void popup.close(); });  // Book, Hotels: the booking sites

    await page.goto(`http://127.0.0.1:${port}/?token=${token}#/deals`);
    await expect(page.getByRole("navigation", { name: "Monitor" })).toBeVisible();
    await page.keyboard.press("Control+0");  // every test at the standard text size, whatever the last one left
    await expect(page.locator("html")).not.toHaveAttribute("data-text-size");
    const go = async (hash: string) => {
      await page.evaluate((h) => { window.location.hash = h; }, hash);
      await expect(page.locator("main")).not.toBeEmpty();
    };
    const theme = async (name: "light" | "dark") => {
      const toggle = page.getByLabel(/Switch to the (light|dark) theme/);
      if ((await page.evaluate(() => document.documentElement.dataset.theme)) !== name) await toggle.click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", name);
    };
    await use({ page, problems, excused, go, theme });
  },
});

/** A sidebar button (its name may go on with a badge: "Deals 429 unusually cheap"). */
export function sidebarLink(page: Page, label: string) {
  return page.locator("aside").getByRole("link", { name: new RegExp(`^${label}`) });
}

/** Quick: the app is still drawn, and no screen shows an error (after each of many clicks). */
export async function stillThere(page: Page): Promise<void> {
  const state = await page.evaluate(() => ({
    root: document.getElementById("root")?.childElementCount ?? 0,
    failed: document.body.innerText.includes("hit a problem"),
  }));
  expect(state.root, "the app is gone: a blank window").toBeGreaterThan(0);
  expect(state.failed, "a screen shows an error").toBe(false);
}

/** The app is still there: the frame, and a screen with something on it (a blank window was v0.8's bug). */
export async function expectAlive(page: Page): Promise<void> {
  await expect(page.getByRole("navigation", { name: "Monitor" })).toBeVisible();
  await expect(page.locator("main h1, main h2").first()).toBeVisible();
  expect(await page.locator("#root").evaluate((root) => root.childElementCount)).toBeGreaterThan(0);
  await expect(page.getByText(/hit a problem/)).toHaveCount(0);
}

export { expect };
