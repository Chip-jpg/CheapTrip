import type { Deal, DealDetail, DealsResponse, Status } from "../api";

export function deal(overrides: Partial<Deal> = {}): Deal {
  return {
    id: "deal-1", type: "flight", tier: "instant", route: "Milan → Berlin",
    origin: { code: "MXP", city: "Milan", country: "IT" }, destination: { code: "BER", city: "Berlin", country: "DE" },
    depart_date: "2026-10-21", return_date: "2026-10-28", nights: 7, length: "medium", travel_window: null,
    price: 30, usual_price: 56, discount_pct: 46.4, previous_price: null, reasons: ["46% below the usual price"],
    sources: ["ryanair"], confidence: "high", is_error_fare: false, is_priority: false, is_muted: false,
    flight: null, hotel: null, package_hotel: null, legs: [], links: { book: "https://example.com/book", hotels: null },
    notified: { at: "2026-09-29T10:00:00", channels: ["desktop", "telegram"] }, waiting: false,
    found_at: "2026-09-29T09:58:00", ...overrides,
  };
}

const COMPARISON = {
  window_days: 30, median: 56,
  points: [
    { depart: "2026-10-02", price: 68, route: "BGY-BER", is_this: false },
    { depart: "2026-10-11", price: 58, route: "BGY-BER", is_this: false },
    { depart: "2026-10-21", price: 30, route: "BGY-BER", is_this: true },
    { depart: "2026-11-01", price: 55, route: "MXP-BER", is_this: false },
  ],
  history: [{ at: "2026-09-28T04:12:00", price: 38 }, { at: "2026-09-29T09:58:00", price: 30 }],
};

export const DEALS: DealsResponse = {
  summary: { unusually_cheap: 1, best: deal(), last_search: { at: "2026-09-29T09:58:00", fares: 783 } },
  instant: [deal({ comparison: COMPARISON, previous_price: 38 })],
  digest: [deal({ id: "deal-2", tier: "digest", route: "Milan → Prague", price: 35, usual_price: null,
                  discount_pct: null, notified: null, destination: { code: "PRG", city: "Prague", country: "CZ" } })],
};

export const DETAIL: DealDetail = {
  ...deal({ previous_price: 38 }), comparison: COMPARISON, verdict: "", feasibility_notes: [],
  flight: { airline: "Ryanair", departure_time: "2026-10-21T06:40:00", arrival_time: "2026-10-21T08:25:00", stops: 0,
            duration_minutes: 105, round_trip: true },
};

export const STATUS: Status = {
  state: "running", searching: false, paused_until: null, next_search_at: "2026-09-29T12:30:00",
  last_cycle: null, searches_today: 3, attention: [], learning: { trips: 80, with_usual_price: 30 },
  telegram_configured: true, timezone: "Europe/Rome", version: "0.6.0", app: "desktop", setup_needed: false,
};

/** A fetch that answers from `routes` (path → JSON) and records every call. */
export function fakeFetch(routes: Record<string, unknown>) {
  const calls: { url: string; method: string; body?: string }[] = [];
  const impl = async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body as string | undefined });
    const path = url.replace(/^\/api\/v1/, "").split("?")[0];
    if (!(path in routes)) return new Response(JSON.stringify({ error: "not found" }), { status: 404 });
    return new Response(JSON.stringify(routes[path]), { status: 200 });
  };
  return { calls, impl };
}

/** jsdom has no EventSource: a silent one. */
export class SilentEventSource {
  onmessage: unknown = null;
  constructor(public url: string) {}
  addEventListener() {}
  close() {}
}
