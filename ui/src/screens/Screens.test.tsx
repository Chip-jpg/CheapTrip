import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Activity, Destination, NotificationPreviews } from "../api";
import { App } from "../App";
import { countryName } from "../format";
import { DEALS, fakeFetch, SilentEventSource, STATUS } from "../test/fixtures";

function destination(code: string, city: string, extra: Partial<Destination> = {}): Destination {
  return { code, city, country: "PL", best_price: 30, best_depart: "2026-10-27", best_return: "2026-10-29",
    best_route: `MXP-${code}`, usual_price: 40, trend: [{ day: "2026-09-28", price: 34 }, { day: "2026-09-29", price: 30 }],
    fares: 24, last_seen: "2026-09-29T21:00:00", is_priority: false, is_muted: false, ...extra };
}

const ACTIVITY: Activity = {
  sources: [
    { id: "ryanair", enabled: true, reason: null, status: "OK", last_count: 731, last_run_at: "2026-09-29T20:09:00",
      last_success_at: "2026-09-29T20:09:00", last_error: null, consecutive_failures: 0, switch: "enable_ryanair" },
    { id: "going", enabled: false, reason: "JavaScript-only site", status: "DISABLED", last_count: null, last_run_at: null,
      last_success_at: null, last_error: null, consecutive_failures: 0, switch: "enable_going" },
  ],
  alerts: { log: [{ sent_at: "2026-09-29T20:10:00", alert_tier: "instant", channel: "desktop,telegram", hash: "deal-1",
                    route: "Milan → Berlin", total_cost: 30 }], per_hour_limit: 5, sent_last_hour: 1, waiting: 2 },
  searches: [{ id: 1, started_at: "2026-09-29T20:08:00", finished_at: "2026-09-29T20:09:00", duration_s: 39, trigger: "startup",
               fares: 749, trips: 562, new_instant: 3, new_digest: 361, sent: 1, with_usual_price: 0, status: "ok", error: null }],
};

const PREVIEWS: NotificationPreviews = {
  previews: [
    { kind: "deal", example: false, title: "Milan → Berlin · €30", message: "46% below the usual €56", buttons: ["Book €30", "Details", "Mute Berlin"], image: null },
    { kind: "notice", example: true, title: "Google Flights isn't working", message: "3 searches failed", buttons: ["Open Activity"], image: null },
  ],
  desktop: { available: true, on: true, sound: true }, app_id: "CheapTrip",
};

let fetch: ReturnType<typeof fakeFetch>;

function start(hash: string) {
  fetch = fakeFetch({
    "/deals": DEALS, "/status": STATUS, "/activity": ACTIVITY, "/notifications/previews": PREVIEWS,
    "/settings": { app: { theme: "system", close_to_tray: true }, preferences: { home_airports: ["MXP"] },
                   sources: { enable_ryanair: true, enable_going: false } },
    "/destinations": { tab: "seen", destinations: [destination("KRK", "Kraków", { is_priority: true }), destination("WAW", "Warsaw")] },
    "/destinations/WAW/priority": {}, "/destinations/KRK/priority": {}, "/destinations/KRK/mute": {},
    "/setup/test-notification": { sent: { desktop: true } }, "/app/open": { opened: "/logs" },
  });
  vi.stubGlobal("fetch", fetch.impl);
  vi.stubGlobal("EventSource", SilentEventSource);
  window.location.hash = hash;
  render(<App />);
}

const writes = () => fetch.calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.url} ${c.body ?? ""}`.trim());

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Destinations", () => {
  it("lists every destination seen, with its rule, and changes rules from the menu", async () => {
    start("#/destinations/WAW");
    const rows = await screen.findAllByTestId("destination-row");  // the quick jump's destination only
    expect(rows.map((r) => r.querySelector("span")?.textContent)).toEqual(["WAW"]);
    expect(rows[0].textContent).toContain("Poland");
    expect((screen.getByRole("textbox", { name: "Filter destinations" }) as HTMLInputElement).value).toBe("WAW");
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Rules for Warsaw" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /Always notify/ }));
    await waitFor(() => expect(writes()).toContain("POST /api/v1/destinations/WAW/priority"));
    expect(await screen.findByText("Warsaw always alerts now")).toBeTruthy();
  });

  it("adds a destination from the dialog", async () => {
    start("#/destinations");
    fireEvent.click(await screen.findByRole("button", { name: /Add destination/ }));
    const dialog = screen.getByRole("dialog", { name: "Add a destination" });
    fetch.calls.length = 0;
    vi.stubGlobal("fetch", fakeFetch({ "/airports": [{ code: "KEF", name: "Keflavik", city: "Reykjavik", country: "IS" }],
                                       "/destinations/KEF/priority": {}, "/destinations": { destinations: [] } }).impl);
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "reyk" } });
    fireEvent.click(await within(dialog).findByRole("option", { name: /KEF/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Always notify Reykjavik" }));
    expect(await screen.findByText("Reykjavik always alerts now")).toBeTruthy();
  });
});

describe("Activity", () => {
  it("shows the sources, the alerts sent and the searches, and switches a source", async () => {
    start("#/activity");
    const sources = await screen.findAllByTestId("source-row");
    expect(sources[0].textContent).toContain("731 found");
    expect(sources[1].textContent).toContain("JavaScript-only site");
    expect(screen.getByText("2 deals are waiting for the next free slot.")).toBeTruthy();
    expect(screen.getByText("Milan → Berlin · €30")).toBeTruthy();
    fireEvent.click(await screen.findByRole("switch", { name: "Going on" }));
    await waitFor(() => expect(writes()).toContain('PUT /api/v1/settings {"sources":{"enable_going":true}}'));
    fireEvent.click(screen.getByRole("button", { name: /Open log folder/ }));
    await waitFor(() => expect(writes()).toContain('POST /api/v1/app/open {"what":"logs"}'));
  });
});

describe("Notifications", () => {
  it("previews what Windows will show and sends one on request", async () => {
    start("#/notifications");
    const [deal, notice] = await screen.findAllByTestId("toast-preview");
    expect(deal.textContent).toContain("Book €30");
    expect(notice.textContent).toContain("Source problem (example)");
    fireEvent.click(within(deal).getByRole("button", { name: /Show it on this PC/ }));
    await waitFor(() => expect(writes()).toContain('POST /api/v1/setup/test-notification {"channel":"desktop","kind":"deal"}'));
  });
});

describe("formatting", () => {
  it("names countries", () => {
    expect(countryName("PL")).toBe("Poland");
    expect(countryName(null)).toBe("");
  });
});
