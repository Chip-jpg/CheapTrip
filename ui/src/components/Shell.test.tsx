import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { DEALS, fakeFetch, SilentEventSource, STATUS } from "../test/fixtures";
import { statusText } from "./TopBar";

let fetch: ReturnType<typeof fakeFetch>;

function start(status = STATUS) {
  fetch = fakeFetch({
    "/deals": DEALS, "/status": status, "/engine/search-now": { started: true }, "/engine/pause": {},
    "/engine/resume": {}, "/settings": { app: { theme: "system", close_to_tray: true } },
    "/airports": [{ code: "KRK", name: "John Paul II", city: "Kraków", country: "PL" }],
  });
  vi.stubGlobal("fetch", fetch.impl);
  vi.stubGlobal("EventSource", SilentEventSource);
  window.location.hash = "#/deals";
  render(<App />);
}

const posted = () => fetch.calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.url} ${c.body ?? ""}`.trim());

beforeEach(() => vi.useRealTimers());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the top bar", () => {
  it("says what the engine is doing, in words", () => {
    const now = new Date("2026-09-29T10:00:00Z");
    expect(statusText({ ...STATUS, next_search_at: "2026-09-29T10:42:00" }, undefined, now))
      .toBe("Running · next search in 42 min");
    expect(statusText({ ...STATUS, state: "searching", searching: true },
                      { planned: ["ryanair", "google_flights", "travelpayouts", "going"], done: {
                        ryanair: { status: "ok", count: 765 }, going: { status: "failed", count: 0 } } }, now))
      .toBe("Searching… 2 of 4 sources");
    expect(statusText({ ...STATUS, state: "paused", paused_until: "2036-01-01T08:00:00" }, undefined, now))
      .toBe("Alerts paused until you resume");
    expect(statusText({ ...STATUS, state: "attention" }, undefined, now)).toBe("Needs attention");
    expect(statusText({ ...STATUS, state: "stopped" }, undefined, now)).toBe("Stopped");
  });

  it("starts a search and pauses alerts", async () => {
    start();
    fireEvent.click(await screen.findByRole("button", { name: /Search now/ }));
    const pause = screen.getByRole("button", { name: "Pause alerts" }) as HTMLButtonElement;
    await waitFor(() => expect(pause.disabled).toBe(false));
    fireEvent.click(pause);
    fireEvent.click(await screen.findByRole("menuitem", { name: "4 hours" }));
    await waitFor(() => expect(posted()).toEqual([
      "POST /api/v1/engine/search-now", 'POST /api/v1/engine/pause {"hours":4}',
    ]));
  });

  it("offers Resume while alerts are paused", async () => {
    start({ ...STATUS, state: "paused", paused_until: "2036-01-01T08:00:00" });
    fireEvent.click(await screen.findByRole("button", { name: /Resume/ }));
    await waitFor(() => expect(posted()).toEqual(["POST /api/v1/engine/resume"]));
  });

  it("jumps to a destination", async () => {
    start();
    fireEvent.change(screen.getByRole("combobox", { name: "Jump to a destination" }), { target: { value: "krak" } });
    fireEvent.click(await screen.findByRole("option", { name: /KRK/ }));
    expect(window.location.hash).toBe("#/destinations/KRK");
  });

  it("switches the theme and saves the choice", async () => {
    start();
    const toggle = await screen.findByRole("button", { name: /Switch to the (light|dark) theme/ });
    const before = document.documentElement.dataset.theme;
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).not.toBe(before);
    await waitFor(() => expect(posted()[0]).toMatch(/^PUT \/api\/v1\/settings \{"app":\{"theme":"(light|dark)"\}\}$/));
  });
});

describe("the sidebar", () => {
  it("counts the unusually cheap deals and shows the engine", async () => {
    start();
    expect(await screen.findByLabelText("1 unusually cheap")).toBeTruthy();
    expect(screen.getByRole("link", { name: /^Deals/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("v0.6.0")).toBeTruthy();
  });
});
