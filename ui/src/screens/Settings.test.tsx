import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Settings } from "../api";
import { App } from "../App";
import { DEALS, fakeFetch, SilentEventSource, STATUS } from "../test/fixtures";

const SETTINGS: Settings = {
  preferences: {
    home_airports: ["MXP", "BGY"], allow_repositioning: true, max_trip_budget: null, excluded_destinations: [],
    preferred_trip_lengths: ["weekend", "short"], minimum_hotel_rating: 7, preferred_hotel_rating: 8, search_window_days: 90,
    min_hotel_review_count: null, priority_destinations: [], adults: 1, repositioning_hubs: ["LHR"], market: null,
  },
  alerts: { europe_trip_max_eur: 25, longhaul_trip_max_eur: 250, price_anomaly_min_drop_pct: 35, instant_alerts_per_hour: 5,
            digest_time: "08:00", scrape_interval_minutes: 90 },
  keys: { telegram_bot_token: { set: true, hint: "…OKEN" }, anthropic_api_key: { set: false, hint: "" },
          travelpayouts_token: { set: false, hint: "" }, rapidapi_key: { set: false, hint: "" }, telegram_chat_id: "4242" },
  sources: { enable_ryanair: true, enable_going: false },
  notifications: { desktop: true, telegram: true, instant: true, digest: true, source_problems: true, sound: true, telegram_ready: true },
  app: { close_to_tray: true, setup_done: false, theme: "system", text_size: "standard", devtools: false, start_at_login: null },
  files: { settings: "C:/Users/me/AppData/Roaming/CheapTrip/.env", preferences: "C:/Users/me/AppData/Roaming/CheapTrip/config/user_preferences.yaml" },
};

let fetch: ReturnType<typeof fakeFetch>;

function start(hash: string, status = STATUS) {
  fetch = fakeFetch({
    "/deals": DEALS, "/status": status, "/settings": SETTINGS,
    "/airports": [{ code: "MXP", name: "Malpensa", city: "Milan", country: "IT" }],
    "/setup/checks": { checks: [{ level: "ok", name: "Database", detail: "ready" }, { level: "warn", name: "Anthropic", detail: "no key" }] },
    "/setup/check": { checks: [{ level: "ok", name: "Telegram", detail: "bot @CheapTripBot, chat 4242" }] },
    "/app/update": { current: "0.6.0", latest: "0.8.0", newer: true, url: "https://github.com/x", download: "https://example.com/setup.exe" },
  });
  // saving answers with the settings, as the engine does
  const answer = fetch.impl;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    if (init.method === "PUT") {
      fetch.calls.push({ url, method: "PUT", body: init.body as string });
      return new Response(JSON.stringify({ saved: [], applies: { "x": "next search" }, settings: SETTINGS }), { status: 200 });
    }
    return answer(url, init);
  });
  vi.stubGlobal("EventSource", SilentEventSource);
  window.location.hash = hash;
  render(<App />);
}

const puts = () => fetch.calls.filter((c) => c.method === "PUT").map((c) => c.body);

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Settings", () => {
  it("saves as you change things, and says when it applies", async () => {
    start("#/settings/trips");
    fireEvent.click(await screen.findByRole("button", { name: "Remove BGY" }));
    await waitFor(() => expect(puts()).toContain('{"preferences":{"home_airports":["MXP"]}}'));
    expect(await screen.findByText("Home airports saved · applies from the next search")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Long/ }));
    await waitFor(() => expect(puts()).toContain('{"preferences":{"preferred_trip_lengths":["weekend","short","long"]}}'));
  });

  it("won't save an empty or impossible value, and puts the saved one back", async () => {
    start("#/settings/trips");
    const days = await screen.findByLabelText("How far ahead") as HTMLInputElement;

    fireEvent.change(days, { target: { value: "" } });
    fireEvent.blur(days);
    expect(days.value).toBe("90");
    expect(await screen.findByText("How far ahead: can't be empty")).toBeTruthy();

    fireEvent.change(days, { target: { value: "0" } });
    fireEvent.blur(days);
    expect(days.value).toBe("90");
    expect(await screen.findByText("How far ahead: enter a number from 14 to 365")).toBeTruthy();
    expect(puts()).toEqual([]);

    const budget = screen.getByLabelText("Trip budget") as HTMLInputElement;  // optional: empty is "no limit"
    fireEvent.change(budget, { target: { value: "300" } });
    fireEvent.blur(budget);
    await waitFor(() => expect(puts()).toEqual(['{"preferences":{"max_trip_budget":300}}']));
  });

  it("puts the saved value back when the engine refuses it", async () => {
    start("#/settings/trips");
    const days = await screen.findByLabelText("How far ahead") as HTMLInputElement;
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "How far ahead: must be between 14 and 365" }), { status: 400 }));

    fireEvent.change(days, { target: { value: "120" } });
    fireEvent.blur(days);

    expect(await screen.findByText("Not saved: How far ahead: must be between 14 and 365")).toBeTruthy();
    await waitFor(() => expect(days.value).toBe("90"));  // and no unhandled rejection, which fails the run
  });

  it("shows each section from its menu", async () => {
    start("#/settings/notifications");
    fireEvent.click(await screen.findByRole("switch", { name: "Daily digest" }));
    await waitFor(() => expect(puts()).toContain('{"notifications":{"digest":false}}'));
    expect(screen.getByRole("link", { name: /Notifications/, current: "page" })).toBeTruthy();
  });

  it("checks Telegram and never shows a key back", async () => {
    start("#/settings/keys");
    const token = await screen.findByLabelText("Telegram bot token") as HTMLInputElement;
    expect(token.value).toBe("");
    expect(token.placeholder).toContain("…OKEN");
    fireEvent.click(screen.getAllByRole("button", { name: /Check/ })[0]);
    expect(await screen.findByText(/bot @CheapTripBot/)).toBeTruthy();
    fireEvent.change(token, { target: { value: "123:NEW" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(puts()).toContain('{"keys":{"telegram_bot_token":"123:NEW"}}'));
  });

  it("offers an update, and starting at sign-in only where it works", async () => {
    start("#/settings/app");
    expect((await screen.findByRole("switch", { name: "Start when I sign in" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Check for updates/ }));
    expect(await screen.findByRole("link", { name: /Download 0.8.0/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Quit" }));
    await waitFor(() => expect(puts()).toContain('{"app":{"close_to_tray":false}}'));
  });
});

describe("Settings → App", () => {
  it("makes the text bigger at once, and keeps it", async () => {
    start("#/settings/app");
    const sizes = await screen.findByRole("radiogroup", { name: "Text size" });
    fireEvent.click(within(sizes).getByRole("radio", { name: /Largest/ }));
    expect(document.documentElement.dataset.textSize).toBe("largest");
    await waitFor(() => expect(puts()).toContain('{"app":{"text_size":"largest"}}'));
    fireEvent.keyDown(window, { key: "0", ctrlKey: true });  // Ctrl 0: back to standard
    expect(document.documentElement.dataset.textSize).toBeUndefined();
    await waitFor(() => expect(puts()).toContain('{"app":{"text_size":"standard"}}'));
  });

  it("turns the developer tools on for the next start", async () => {
    start("#/settings/app");
    fireEvent.click(await screen.findByRole("switch", { name: "Developer tools" }));
    await waitFor(() => expect(puts()).toContain('{"app":{"devtools":true}}'));
  });
});

describe("the setup wizard", () => {
  it("opens by itself on a new install and finishes on the deals", async () => {
    start("", { ...STATUS, setup_needed: true });
    await waitFor(() => expect(window.location.hash).toBe("#/setup"));
    expect(await screen.findByText("Cheap flights, found for you")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Let's start/ }));
    expect(await screen.findByText("Where do you fly from?", { selector: "h2" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Skip optional steps" }));
    const checks = await screen.findByRole("region", { name: "Everything ready?" });
    expect(await within(checks).findByText(/1 of 2 checks passed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Finish/ }));
    await waitFor(() => expect(puts()).toContain('{"app":{"setup_done":true}}'));
    await waitFor(() => expect(window.location.hash).toBe("#/deals"));
  });

  it("can be left for another screen where scrollTo returns a promise, as in WebView2 (v0.8's black window)", async () => {
    vi.stubGlobal("scrollTo", () => Promise.resolve());
    start("#/setup", { ...STATUS, setup_needed: true });
    fireEvent.click(await screen.findByRole("button", { name: /Let's start/ }));  // a new step scrolls to the top
    expect(await screen.findByText("Where do you fly from?", { selector: "h2" })).toBeTruthy();
    window.location.hash = "#/deals";  // what a sidebar button does
    expect(await screen.findByRole("heading", { name: /Unusually cheap today/ })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Monitor" })).toBeTruthy();
    expect(screen.queryByText(/hit a problem/)).toBeNull();
  });
});
