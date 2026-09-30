/**
 * The typed client for the engine's local API (api/server.py, /api/v1).
 *
 * The app opens the UI through /?token=…, which sets an HttpOnly cookie; every
 * request here is same-origin, so the cookie authorizes it. The designed
 * screens (Round 8) are built on it.
 */

// ── Shapes ────────────────────────────────────────────────────────────────────

export type EngineState = "stopped" | "searching" | "paused" | "attention" | "running";

export interface Cycle {
  id: number;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
  trigger: string | null;
  fares: number | null;
  trips: number | null;
  new_instant: number | null;
  new_digest: number | null;
  sent: number | null;
  with_usual_price: number | null;
  status: "running" | "ok" | "failed" | "stopped";
  error: string | null;
}

export interface Status {
  state: EngineState;
  searching: boolean;
  paused_until: string | null;
  next_search_at: string | null;
  last_cycle: Cycle | null;
  searches_today: number;
  attention: string[];
  learning: { trips: number | null; with_usual_price: number | null } | null;
  telegram_configured: boolean;
  timezone: string;
  version: string;
  /** The first-run setup wizard hasn't been finished */
  setup_needed: boolean;
  /** Where the screens run: the desktop app's window, or a browser (`main.py ui`) */
  app: "desktop" | "browser" | null;
}

export interface Place {
  code: string;
  city: string;
  country: string | null;
}

export type DealType = "flight" | "one_way" | "package" | "hotel" | "flight_hotel" | "via_hub";
export type TripLength = "weekend" | "short" | "medium" | "long";

export interface HotelInfo {
  name: string;
  location: string;
  price_per_night: number;
  nights: number;
  total: number;
  price_basis: string | null;
  rating: number | null;
  review_count: number | null;
  stars: number | null;
  check_in: string | null;
  check_out: string | null;
  travel_window: string | null;
  booking_url: string | null;
}

export interface Leg {
  from: Place | null;
  to: Place | null;
  price: number;
  depart_date: string | null;
  return_date: string | null;
  booking_url: string | null;
  source: string;
}

export interface Deal {
  id: string;
  type: DealType;
  tier: "instant" | "digest" | "archive";
  route: string;
  origin: Place | null;
  destination: Place | null;
  depart_date: string | null;
  return_date: string | null;
  nights: number | null;
  length: TripLength | null;
  travel_window: string | null;
  price: number;
  usual_price: number | null;
  discount_pct: number | null;
  previous_price: number | null;
  reasons: string[];
  sources: string[];
  confidence: string;
  is_error_fare: boolean;
  is_priority: boolean;
  is_muted: boolean;
  flight: {
    airline: string | null;
    departure_time: string | null;
    arrival_time: string | null;
    stops: number | null;
    duration_minutes: number | null;
    round_trip: boolean;
  } | null;
  hotel: HotelInfo | null;
  package_hotel: string | null;
  legs: Leg[];
  links: { book: string | null; hotels: string | null };
  notified: { at: string; channels: string[] } | null;
  waiting: boolean;
  found_at: string;
  /** The first unusually cheap deals (the full cards): similar fares within ±30 days, for the card's fare range */
  comparison?: FareComparison | null;
}

export interface FareComparison {
  window_days: number;
  points: { depart: string; price: number; route: string; is_this: boolean }[];
  median: number | null;
  history: { at: string; price: number }[];
}

export interface DealDetail extends Deal {
  comparison: FareComparison | null;
  verdict: string;
  feasibility_notes: string[];
}

export interface DealsResponse {
  summary: {
    unusually_cheap: number;
    /** The most unusually cheap deal, else the cheapest of all */
    best: Deal | null;
    last_search: { at: string; fares: number } | null;
  };
  instant: Deal[];
  digest: Deal[];
}

export interface DealFilters {
  types?: (DealType | "error_fare")[];
  lengths?: TripLength[];
  max_price?: number;
  from?: string;
  to?: string;
  q?: string;
  priority_only?: boolean;
  sort?: "price" | "discount" | "date";
}

export interface Destination extends Place {
  best_price: number | null;
  best_depart: string | null;
  best_return: string | null;
  best_route: string | null; // "BGY-KRK"
  usual_price: number | null;
  trend: { day: string; price: number }[]; // the cheapest fare seen each day
  fares: number;
  last_seen: string | null;
  is_priority: boolean;
  is_muted: boolean;
}

export type DestinationTab = "priority" | "muted" | "seen";

export interface Airport {
  code: string;
  name: string;
  city: string;
  country: string;
}

export interface Source {
  id: string;
  enabled: boolean;
  reason: string | null;
  status: string;
  last_count: number | null;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  switch: string | null;
}

export interface AlertLogEntry {
  sent_at: string;
  alert_tier: string;
  channel: string;
  hash: string;
  route: string | null;
  total_cost: number | null;
}

export interface Activity {
  sources: Source[];
  alerts: { log: AlertLogEntry[]; per_hour_limit: number; sent_last_hour: number; waiting: number };
  searches: Cycle[];
}

export interface Preferences {
  home_airports: string[];
  allow_repositioning: boolean;
  max_trip_budget: number | null;
  excluded_destinations: string[];
  preferred_trip_lengths: TripLength[];
  minimum_hotel_rating: number;
  preferred_hotel_rating: number;
  search_window_days: number;
  min_hotel_review_count: number | null;
  priority_destinations: string[];
  adults: number;
  repositioning_hubs: string[];
  market: string | null;
  [other: string]: unknown;
}

export interface AlertRules {
  europe_trip_max_eur: number;
  longhaul_trip_max_eur: number;
  price_anomaly_min_drop_pct: number;
  instant_alerts_per_hour: number;
  digest_time: string; // "HH:MM", local time
  scrape_interval_minutes: number;
}

export interface SecretKey {
  set: boolean;
  hint: string;
}

export interface NotificationSettings {
  desktop: boolean;
  telegram: boolean;
  instant: boolean;
  digest: boolean;
  source_problems: boolean;
  sound: boolean;
}

export interface Settings {
  preferences: Preferences;
  alerts: AlertRules;
  keys: {
    telegram_bot_token: SecretKey;
    anthropic_api_key: SecretKey;
    travelpayouts_token: SecretKey;
    rapidapi_key: SecretKey;
    telegram_chat_id: string;
  };
  sources: Record<string, boolean>;
  notifications: NotificationSettings & { telegram_ready: boolean };
  app: {
    close_to_tray: boolean; setup_done: boolean; theme: ThemeChoice; text_size: TextSize; devtools: boolean;
    start_at_login: boolean | null;
  };
  files: { settings: string; preferences: string };
}

export interface SettingsUpdate {
  preferences?: Partial<Preferences>;
  alerts?: Partial<AlertRules>;
  keys?: Record<string, string | null>; // "" clears a key; leave a key out to keep it
  sources?: Record<string, boolean>;
  notifications?: Partial<NotificationSettings>;
  app?: {
    close_to_tray?: boolean; setup_done?: boolean; theme?: ThemeChoice; text_size?: TextSize; devtools?: boolean;
    start_at_login?: boolean;
  };
}

export interface UiError {
  kind: "screen" | "app" | "uncaught" | "rejection";
  message: string;
  stack?: string;
  component?: string;
  route: string;
}

export type ThemeChoice = "system" | "light" | "dark";
export type TextSize = "standard" | "large" | "largest";

export interface SaveResult {
  saved: string[];
  applies: Record<string, "now" | "next search" | "next start">;
  settings: Settings;
}

export interface Check {
  level: "ok" | "warn" | "fail";
  name: string;
  detail: string;
}

export interface UpdateInfo {
  current: string;
  latest: string;
  newer: boolean;
  url: string | null;
  download: string | null;
}

export type PreviewKind = "deal" | "error_fare" | "digest" | "notice" | "test";

export interface NotificationPreview {
  kind: PreviewKind;
  example: boolean; // made up, to show what it would look like (e.g. no source is failing)
  title: string;
  message: string;
  buttons: string[];
  image: string | null;
}

export interface NotificationPreviews {
  previews: NotificationPreview[];
  desktop: { available: boolean; on: boolean; sound: boolean };
  app_id: string;
}

export type PauseChoice = { hours: number } | { until: "tomorrow" | "resume" };

/** What the engine announces on /events (utils/events.py): every event has a type. */
export interface EngineEvent {
  type: string;
  [field: string]: unknown;
}

// ── Requests ──────────────────────────────────────────────────────────────────

export const API = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(API + path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new ApiError(resp.status, (data as { error?: string }).error ?? `HTTP ${resp.status}`);
  }
  return data as T;
}

export function dealsQuery(filters: DealFilters): string {
  const params = new URLSearchParams();
  if (filters.types?.length) params.set("types", filters.types.join(","));
  if (filters.lengths?.length) params.set("lengths", filters.lengths.join(","));
  if (filters.max_price !== undefined) params.set("max_price", String(filters.max_price));
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.q) params.set("q", filters.q);
  if (filters.priority_only) params.set("priority_only", "1");
  if (filters.sort) params.set("sort", filters.sort);
  const query = params.toString();
  return query ? `?${query}` : "";
}

const enc = encodeURIComponent;

export const api = {
  status: () => request<Status>("GET", "/status"),

  deals: (filters: DealFilters = {}) => request<DealsResponse>("GET", "/deals" + dealsQuery(filters)),
  /** Only the summary (the sidebar's count): no cards, so none of their price comparisons */
  dealsSummary: () => request<DealsResponse>("GET", "/deals?summary=1"),
  deal: (id: string) => request<DealDetail>("GET", `/deals/${enc(id)}`),
  hideDeal: (id: string) => request<{ id: string; hidden: boolean }>("POST", `/deals/${enc(id)}/hide`),

  destinations: (tab: DestinationTab) =>
    request<{ tab: DestinationTab; destinations: Destination[] }>("GET", `/destinations?tab=${tab}`),
  setPriority: (code: string, on: boolean) =>
    request<Place & { is_priority: boolean; is_muted: boolean }>(on ? "POST" : "DELETE",
      `/destinations/${enc(code)}/priority`),
  setMuted: (code: string, on: boolean) =>
    request<Place & { is_priority: boolean; is_muted: boolean }>(on ? "POST" : "DELETE",
      `/destinations/${enc(code)}/mute`),
  airports: (q: string, limit = 8) => request<Airport[]>("GET", `/airports?q=${enc(q)}&limit=${limit}`),

  activity: () => request<Activity>("GET", "/activity"),

  settings: () => request<Settings>("GET", "/settings"),
  saveSettings: (update: SettingsUpdate) => request<SaveResult>("PUT", "/settings", update),
  /** The window's own choices (the sidebar), which the engine keeps: the window forgets them when it closes */
  saveUi: (ui: { sidebar: "collapsed" | "open" }) => request<{ sidebar: string }>("PUT", "/ui", ui),
  /** A screen failed: its error goes to the engine's log (the window has no console to read) */
  reportError: (error: UiError) => request<{ logged: boolean }>("POST", "/ui/error", error),

  checks: () => request<{ checks: Check[] }>("GET", "/setup/checks"),
  checkKey: (what: "telegram" | "anthropic") => request<{ checks: Check[] }>("POST", "/setup/check", { what }),
  testTelegram: () => request<{ sent: boolean; detail: string }>("POST", "/setup/test-telegram"),
  testNotification: (channel?: "desktop" | "telegram", kind?: PreviewKind) =>
    request<{ sent: Record<string, boolean> }>("POST", "/setup/test-notification",
      { ...(channel ? { channel } : {}), ...(kind ? { kind } : {}) }),
  notificationPreviews: () => request<NotificationPreviews>("GET", "/notifications/previews"),

  searchNow: () => request<{ started: boolean }>("POST", "/engine/search-now"),
  pause: (choice: PauseChoice) => request<{ paused_until: string }>("POST", "/engine/pause", choice),
  resume: () => request<{ paused_until: null }>("POST", "/engine/resume"),

  quitApp: () => request<{ quitting: boolean }>("POST", "/app/quit"),
  hideWindow: () => request<{ hidden: boolean }>("POST", "/app/hide"),
  open: (what: "logs" | "data" | "notification-settings") => request<{ opened: string }>("POST", "/app/open", { what }),
  update: () => request<UpdateInfo>("GET", "/app/update"),
  clearPriceHistory: () => request<{ deleted: number }>("POST", "/data/clear-price-history"),
};

const EVENT_TYPES = [
  "cycle_started", "cycle_planned", "source_done", "cycle_finished", "alert_sent", "digest_sent",
  "paused", "resumed", "preferences_changed", "settings_changed", "deal_hidden", "engine_started",
  "engine_stopped",
];
const listeners = new Set<(event: EngineEvent) => void>();
let source: EventSource | null = null;

/**
 * Live events from the engine; returns a function that stops listening. Every
 * screen shares one connection (a browser allows only a few per server).
 */
export function subscribe(onEvent: (event: EngineEvent) => void): () => void {
  listeners.add(onEvent);
  if (source === null) {
    source = new EventSource(API + "/events");
    const dispatch = (message: MessageEvent) => {
      let event: EngineEvent;
      try {
        event = JSON.parse(message.data) as EngineEvent;
      } catch {
        return; // a malformed event is skipped
      }
      listeners.forEach((listener) => listener(event));
    };
    EVENT_TYPES.forEach((type) => source!.addEventListener(type, dispatch));
  }
  return () => {
    listeners.delete(onEvent);
    if (listeners.size === 0 && source !== null) {
      source.close();
      source = null;
    }
  };
}
