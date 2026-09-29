import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { DEALS, fakeFetch, SilentEventSource, STATUS } from "../test/fixtures";

let fetch: ReturnType<typeof fakeFetch>;

beforeEach(() => {
  fetch = fakeFetch({ "/deals": DEALS, "/status": STATUS });
  vi.stubGlobal("fetch", fetch.impl);
  vi.stubGlobal("EventSource", SilentEventSource);
  window.location.hash = "#/deals";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the Deals screen", () => {
  it("shows the unusually cheap deals, then the rest", async () => {
    render(<App />);
    const cards = await screen.findAllByTestId("deal-card");
    expect(cards.map((c) => c.querySelector("strong")?.textContent)).toEqual(["Milan → Berlin", "Milan → Prague"]);
    expect(cards[0].textContent).toContain("46% below the usual €56");
    expect(cards[0].textContent).toContain("desktop + telegram");
    expect(cards[0].getAttribute("href")).toBe("#/deals/deal-1");
    expect(screen.getByTestId("summary").textContent).toContain("783 fares");
    expect((await screen.findByTestId("state")).textContent).toBe("Watching prices");
  });

  it("asks the engine again when a filter changes", async () => {
    render(<App />);
    await screen.findAllByTestId("deal-card");
    fireEvent.click(screen.getByLabelText("Priority only"));
    await waitFor(() => expect(fetch.calls.some((c) => c.url.includes("priority_only=1"))).toBe(true));
  });
});
