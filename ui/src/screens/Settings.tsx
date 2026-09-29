import { useState, type ReactNode } from "react";
import { api, type NotificationSettings, type Settings as SettingsData, type SettingsUpdate, type TripLength } from "../api";
import { useLoad } from "../load";

const LENGTHS: TripLength[] = ["weekend", "short", "medium", "long"];
type KeyName = "telegram_bot_token" | "anthropic_api_key" | "travelpayouts_token" | "rapidapi_key";
const KEY_NAMES: Record<KeyName, string> = {
  telegram_bot_token: "Telegram bot token",
  anthropic_api_key: "Anthropic API key",
  travelpayouts_token: "Travelpayouts token",
  rapidapi_key: "RapidAPI key (Skyscanner)",
};
const NOTIFY_NAMES: Record<keyof NotificationSettings, string> = {
  desktop: "Desktop notifications", telegram: "Telegram", instant: "Deals as they're found",
  digest: "Daily digest", source_problems: "When a source stops working", sound: "Sound",
};

function Section({ title, children, onSave, busy }: {
  title: string; children: ReactNode; onSave: () => void; busy: boolean;
}) {
  return (
    <fieldset className="settings-section">
      <legend>{title}</legend>
      {children}
      <button className="primary" disabled={busy} onClick={onSave}>Save</button>
    </fieldset>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

export function Settings() {
  const loaded = useLoad(api.settings, []);
  const [draft, setDraft] = useState<SettingsData>();
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string>();
  const [busy, setBusy] = useState(false);
  const s = draft ?? loaded.data;

  if (loaded.error) return <section><h1>Settings</h1><p className="error">{loaded.error}</p></section>;
  if (!s) return <section><h1>Settings</h1>…</section>;

  const edit = (change: (d: SettingsData) => SettingsData) => setDraft(change(structuredClone(s)));
  const save = (update: SettingsUpdate) => {
    setBusy(true);
    setMessage(undefined);
    api.saveSettings(update)
      .then((result) => {
        setDraft(result.settings);
        setKeys({});
        const later = Object.values(result.applies).includes("next search");
        setMessage(`Saved. ${later ? "Applies from the next search." : "Applies now."}`);
      })
      .catch((err: Error) => setMessage(`Not saved: ${err.message}`))
      .finally(() => setBusy(false));
  };
  const p = s.preferences;

  return (
    <section>
      <h1>Settings</h1>
      {message && <p className="notice" role="status">{message}</p>}

      <Section title="Trips" busy={busy} onSave={() => save({ preferences: {
        home_airports: p.home_airports, preferred_trip_lengths: p.preferred_trip_lengths,
        search_window_days: p.search_window_days, adults: p.adults, max_trip_budget: p.max_trip_budget,
        allow_repositioning: p.allow_repositioning,
      } })}>
        <Field label="Home airports">
          <input value={p.home_airports.join(", ")} onChange={(e) => edit((d) => {
            d.preferences.home_airports = e.target.value.split(/[\s,;]+/).filter(Boolean).map((c) => c.toUpperCase());
            return d;
          })} />
        </Field>
        <Field label="Trip lengths">
          <span>
            {LENGTHS.map((l) => (
              <label key={l} className="chip">
                <input type="checkbox" checked={p.preferred_trip_lengths.includes(l)} onChange={() => edit((d) => {
                  const on = d.preferences.preferred_trip_lengths.includes(l);
                  d.preferences.preferred_trip_lengths = on
                    ? d.preferences.preferred_trip_lengths.filter((x) => x !== l) : [...d.preferences.preferred_trip_lengths, l];
                  return d;
                })} />{l}
              </label>
            ))}
          </span>
        </Field>
        <Field label="Search this many days ahead">
          <input type="number" min={7} max={365} value={p.search_window_days}
                 onChange={(e) => edit((d) => ({ ...d, preferences: { ...d.preferences, search_window_days: Number(e.target.value) } }))} />
        </Field>
        <Field label="Travellers">
          <input type="number" min={1} max={9} value={p.adults}
                 onChange={(e) => edit((d) => ({ ...d, preferences: { ...d.preferences, adults: Number(e.target.value) } }))} />
        </Field>
        <Field label="Trip budget (€, empty: no limit)">
          <input type="number" min={0} value={p.max_trip_budget ?? ""}
                 onChange={(e) => edit((d) => ({ ...d, preferences: { ...d.preferences,
                   max_trip_budget: e.target.value ? Number(e.target.value) : null } }))} />
        </Field>
        <Field label="Look for cheaper flights from nearby hubs">
          <input type="checkbox" checked={p.allow_repositioning}
                 onChange={(e) => edit((d) => ({ ...d, preferences: { ...d.preferences, allow_repositioning: e.target.checked } }))} />
        </Field>
      </Section>

      <Section title="Alerts" busy={busy} onSave={() => save({ alerts: s.alerts })}>
        {([
          ["europe_trip_max_eur", "Always alert for Europe trips under (€)"],
          ["longhaul_trip_max_eur", "Always alert for long-haul trips under (€)"],
          ["price_anomaly_min_drop_pct", "Unusually cheap: at least this % below the usual price"],
          ["instant_alerts_per_hour", "At most this many alerts an hour"],
          ["scrape_interval_minutes", "Search every (minutes)"],
        ] as const).map(([name, label]) => (
          <Field key={name} label={label}>
            <input type="number" value={s.alerts[name]}
                   onChange={(e) => edit((d) => ({ ...d, alerts: { ...d.alerts, [name]: Number(e.target.value) } }))} />
          </Field>
        ))}
        <Field label="Daily digest at">
          <input type="time" value={s.alerts.digest_time}
                 onChange={(e) => edit((d) => ({ ...d, alerts: { ...d.alerts, digest_time: e.target.value } }))} />
        </Field>
      </Section>

      <Section title="Notifications" busy={busy} onSave={() => {
        const { telegram_ready: _ready, ...switches } = s.notifications;
        save({ notifications: switches });
      }}>
        {(Object.keys(NOTIFY_NAMES) as (keyof NotificationSettings)[]).map((name) => (
          <Field key={name} label={NOTIFY_NAMES[name]}>
            <input type="checkbox" checked={s.notifications[name]}
                   onChange={(e) => edit((d) => ({ ...d, notifications: { ...d.notifications, [name]: e.target.checked } }))} />
          </Field>
        ))}
        {!s.notifications.telegram_ready && <p className="muted">Telegram needs a bot token and chat ID (below).</p>}
        <button disabled={busy} onClick={() => api.testNotification()
          .then((r) => setMessage(`Test sent: ${Object.entries(r.sent).map(([c, ok]) => `${c} ${ok ? "✓" : "✗"}`).join(", ")}`))
          .catch((err: Error) => setMessage(`Test failed: ${err.message}`))}>
          Send a test notification
        </button>
      </Section>

      <Section title="Telegram and keys" busy={busy} onSave={() => save({
        keys: { ...keys, telegram_chat_id: s.keys.telegram_chat_id },
      })}>
        {(Object.keys(KEY_NAMES) as KeyName[]).map((name) => {
          const current = s.keys[name];
          return (
            <Field key={name} label={KEY_NAMES[name]}>
              <input type="password" autoComplete="off" value={keys[name] ?? ""}
                     placeholder={current.set ? `set (${current.hint}): type to replace` : "not set"}
                     onChange={(e) => setKeys((k) => ({ ...k, [name]: e.target.value }))} />
            </Field>
          );
        })}
        <Field label="Telegram chat ID">
          <input value={s.keys.telegram_chat_id}
                 onChange={(e) => edit((d) => ({ ...d, keys: { ...d.keys, telegram_chat_id: e.target.value } }))} />
        </Field>
      </Section>

      <Section title="Sources" busy={busy} onSave={() => save({ sources: s.sources })}>
        {Object.entries(s.sources).map(([name, on]) => (
          <Field key={name} label={name.replace(/^enable_/, "").replace(/_/g, " ")}>
            <input type="checkbox" checked={on}
                   onChange={(e) => edit((d) => ({ ...d, sources: { ...d.sources, [name]: e.target.checked } }))} />
          </Field>
        ))}
      </Section>

      <Section title="App" busy={busy} onSave={() => save({ app: s.app })}>
        <Field label="Closing the window keeps CheapTrip running in the tray">
          <input type="checkbox" checked={s.app.close_to_tray}
                 onChange={(e) => edit((d) => ({ ...d, app: { close_to_tray: e.target.checked } }))} />
        </Field>
      </Section>

      <p className="muted">Stored in {s.files.settings} and {s.files.preferences}.</p>
    </section>
  );
}
