import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { DEALS, DETAIL, fakeFetch, SilentEventSource, STATUS } from "../test/fixtures";

let fetch: ReturnType<typeof fakeFetch>;

function start(hash = "#/deals") {
  fetch = fakeFetch({
    "/deals": DEALS, "/status": STATUS, "/deals/deal-1": DETAIL, "/destinations/BER/mute": {}, "/deals/deal-1/hide": {},
    "/settings": { app: { theme: "system", close_to_tray: true }, preferences: { home_airports: ["MXP", "BGY"] } },
  });
  vi.stubGlobal("fetch", fetch.impl);
  vi.stubGlobal("EventSource", SilentEventSource);
  window.location.hash = hash;
  render(<App />);
}

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the Deals screen", () => {
  it("shows the unusually cheap deals as cards, then the rest as a table", async () => {
    start();
    const [card] = await screen.findAllByTestId("deal-card");
    expect(card.querySelector("h3")?.textContent).toBe("Milan → Berlin");
    expect(card.textContent).toContain("€38");  // the price before it dropped
    expect(card.textContent).toContain("-46%");
    expect(card.textContent).toContain("Usual €56");  // the fare range
    expect(card.textContent).toContain("Why it's here: 46% below the usual price");
    expect(card.textContent).toMatch(/Sent \d\d:\d\d/);
    expect(card.textContent).toContain("alert sent to Desktop + Telegram");
    const [row] = screen.getAllByTestId("deal-row");
    expect(row.textContent).toContain("Prague (PRG)");
    expect(row.textContent).toContain("learning");  // no usual price yet
    expect(screen.getByTestId("summary").textContent).toContain("1unusually cheap today");
    expect(screen.getByText("783")).toBeTruthy();
    expect(screen.getByText("Searching from MXP / BGY")).toBeTruthy();
  });

  it("asks the engine again when a filter changes", async () => {
    start();
    await screen.findAllByTestId("deal-card");
    fireEvent.click(screen.getByRole("switch", { name: "Priority only" }));
    fireEvent.click(screen.getByRole("button", { name: "Possible error fares" }));
    fireEvent.click(screen.getByRole("button", { name: "Weekend (2–4n)" }));
    await waitFor(() => expect(fetch.calls.some((c) => c.url.includes("priority_only=1") && c.url.includes("types=error_fare")
      && c.url.includes("lengths=weekend"))).toBe(true));
  });

  it("mutes a destination from a card's menu, with Undo", async () => {
    start();
    const [card] = await screen.findAllByTestId("deal-card");
    fireEvent.click(within(card).getByRole("button", { name: "More for Milan → Berlin" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /Mute Berlin/ }));
    expect(await screen.findByText("Berlin muted: no more alerts for it")).toBeTruthy();
    expect(fetch.calls.some((c) => c.method === "POST" && c.url === "/api/v1/destinations/BER/mute")).toBe(true);
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
  });

  it("opens a deal's details over the list", async () => {
    start("#/deals/deal-1");
    const panel = await screen.findByRole("dialog", { name: "Milan → Berlin details" });
    expect(await within(panel).findByText("How this fare compares")).toBeTruthy();
    expect(panel.textContent).toContain("Compared with 3 similar fares");
    expect(panel.textContent).toContain("First seen €38");
    expect(panel.textContent).toContain("06:40 MXP → 08:25 BER");
    expect(panel.textContent).toContain("Booking confidence: High");
    expect(await screen.findAllByTestId("deal-card")).toHaveLength(1);  // the list stays behind it
    fireEvent.keyDown(window, { key: "Escape" });
    expect(window.location.hash).toBe("#/deals");
  });
});
