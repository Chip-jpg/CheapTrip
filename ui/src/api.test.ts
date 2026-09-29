import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, dealsQuery, subscribe, type EngineEvent } from "./api";
import { DEALS, fakeFetch } from "./test/fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("the API client", () => {
  it("turns the Deals filters into the query the engine reads", () => {
    expect(dealsQuery({})).toBe("");
    expect(dealsQuery({ types: ["flight", "error_fare"], lengths: ["weekend"], max_price: 80, q: "ber",
                        priority_only: true, sort: "discount", from: "2026-10-01" }))
      .toBe("?types=flight%2Cerror_fare&lengths=weekend&max_price=80&from=2026-10-01&q=ber&priority_only=1&sort=discount");
  });

  it("calls the engine's routes", async () => {
    const fetch = fakeFetch({ "/deals": DEALS, "/destinations/KRK/mute": { code: "KRK" }, "/engine/pause": {} });
    vi.stubGlobal("fetch", fetch.impl);

    expect((await api.deals({ sort: "price" })).instant[0].route).toBe("Milan → Berlin");
    await api.setMuted("KRK", false);
    await api.pause({ until: "tomorrow" });

    expect(fetch.calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "GET /api/v1/deals?sort=price", "DELETE /api/v1/destinations/KRK/mute", "POST /api/v1/engine/pause",
    ]);
    expect(fetch.calls[2].body).toBe('{"until":"tomorrow"}');
  });

  it("reports the engine's error message", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "unknown airport code" }), { status: 400 }));
    await expect(api.setPriority("XXX", true)).rejects.toEqual(new ApiError(400, "unknown airport code"));
  });
});

describe("live events", () => {
  it("share one connection however many screens listen", () => {
    const opened: { url: string; closed: boolean; listeners: Record<string, (m: MessageEvent) => void> }[] = [];
    vi.stubGlobal("EventSource", class {
      state = { url: "", closed: false, listeners: {} as Record<string, (m: MessageEvent) => void> };
      constructor(url: string) { this.state.url = url; opened.push(this.state); }
      addEventListener(type: string, fn: (m: MessageEvent) => void) { this.state.listeners[type] = fn; }
      close() { this.state.closed = true; }
    });
    const a: EngineEvent[] = [], b: EngineEvent[] = [];
    const stopA = subscribe((e) => a.push(e));
    const stopB = subscribe((e) => b.push(e));

    opened[0].listeners["source_done"]({ data: '{"type":"source_done","source":"ryanair"}' } as MessageEvent);
    expect(opened.map((o) => o.url)).toEqual(["/api/v1/events"]);
    expect(a).toEqual([{ type: "source_done", source: "ryanair" }]);
    expect(b).toEqual(a);

    stopA();
    expect(opened[0].closed).toBe(false);
    stopB();
    expect(opened[0].closed).toBe(true);
  });
});
