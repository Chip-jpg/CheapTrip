/**
 * Settings (built in the design's style): a section menu (Trips, Deals &
 * alerts, Notifications, Accounts & keys, Sources, App) and controls that save
 * as you change them, with a confirmation that says when the change applies.
 */
import { useState, type ReactNode } from "react";
import { api, type Check, type Settings as SettingsData, type SettingsUpdate, type TripLength, type UpdateInfo } from "../api";
import { AirportChips, CheckList, CommitInput, Row, Section, Segmented, SecretField, Stepper, Switch } from "../components/Form";
import { Icon } from "../components/Icon";
import { PageHeader, Pill } from "../components/Page";
import { useToast } from "../components/Toast";
import { useEngine } from "../engine";
import { useReloadOn } from "../events";
import { useLoad } from "../load";
import { href } from "../router";
import { sourceName } from "../sources";
import { useTheme } from "../theme";

export const SECTIONS = [
  { id: "trips", label: "Trips", icon: "flight" },
  { id: "alerts", label: "Deals & alerts", icon: "tune" },
  { id: "notifications", label: "Notifications", icon: "notifications" },
  { id: "keys", label: "Accounts & keys", icon: "key" },
  { id: "sources", label: "Sources", icon: "hub" },
  { id: "app", label: "App", icon: "desktop_windows" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

export const LENGTHS: { value: TripLength; label: string; nights: string }[] = [
  { value: "weekend", label: "Weekend", nights: "2–4 nights" },
  { value: "short", label: "Short", nights: "4–7 nights" },
  { value: "medium", label: "Medium", nights: "7–14 nights" },
  { value: "long", label: "Long", nights: "14–30 nights" },
];

const SOURCE_HELP: Record<string, string> = {
  enable_ryanair: "Ryanair's own fares: the backbone, about 700 a search.",
  enable_piratinviaggio: "Italian deal-site posts (flights, packages, hotels), read by Claude: needs the Anthropic key.",
  enable_skyscanner: "Skyscanner via RapidAPI: needs a RapidAPI key; its free quota is small.",
  enable_google_hotels: "Google Hotels: can't be given dates yet, so its prices are rough.",
  enable_booking_html: "Booking.com pages: usually blocked for automated searches.",
  enable_secret_flying: "Secret Flying's error fares: behind Cloudflare, usually blocked.",
  enable_going: "Going's deals: a JavaScript-only site, usually empty.",
  enable_holiday_pirates: "HolidayPirates (UK edition): packages from UK airports in pounds.",
};

/** Save a change and say so ("Saved · applies from the next search"). */
export function useSave(onSaved: (settings: SettingsData) => void) {
  const toast = useToast();
  return (update: SettingsUpdate, what = "Saved") => api.saveSettings(update)
    .then((result) => {
      onSaved(result.settings);
      const applies = Object.values(result.applies);
      toast(`${what} · ${applies.includes("next start") ? "applies when CheapTrip starts again"
        : applies.includes("next search") ? "applies from the next search" : "applies now"}`);
      return result;
    })
    .catch((err: Error) => { toast(`Not saved: ${err.message}`, { tone: "error" }); return null; });  // the toast says it
}

function Trips({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const p = s.preferences;
  return (
    <>
      <Section title="Where you fly from" icon="flight_takeoff" description="Airports of the same city are searched together, so MXP also covers Linate and Bergamo.">
        <Row label="Home airports" help="At least one.">
          <AirportChips label="Home airports" codes={p.home_airports}
                        onChange={(codes) => codes.length && save({ preferences: { home_airports: codes } }, "Home airports saved")} />
        </Row>
        <Row label="Travellers" help="Searches and booking links are for this many; prices are shown per person.">
          <Stepper label="travellers" value={p.adults} min={1} max={9} onChange={(adults) => save({ preferences: { adults } })} />
        </Row>
      </Section>
      <Section title="Trips you like" icon="date_range">
        <Row label="Trip lengths" help="Fares for other lengths are still shown, but these are searched.">
          <div className="grid w-[21rem] max-w-full grid-cols-2 gap-space-xs">
            {LENGTHS.map((l) => {
              const on = p.preferred_trip_lengths.includes(l.value);
              return (
                <button key={l.value} role="checkbox" aria-checked={on} onClick={() => {
                  const next = on ? p.preferred_trip_lengths.filter((x) => x !== l.value) : [...p.preferred_trip_lengths, l.value];
                  if (next.length) void save({ preferences: { preferred_trip_lengths: next } });
                }} className={`flex min-w-0 items-center gap-space-sm rounded-lg border px-space-md py-2 text-left ${on
                  ? "border-primary-container bg-primary-container/10" : "border-stroke hover:bg-surface-container-highest"}`}>
                  <Icon name={on ? "check_box" : "check_box_outline_blank"} size={18} className={on ? "text-primary" : "text-outline"} />
                  <span className="flex flex-col"><span className="text-body text-on-surface">{l.label}</span>
                    <span className="font-mono text-mono-sm text-outline">{l.nights}</span></span>
                </button>
              );
            })}
          </div>
        </Row>
        <Row label="How far ahead" help="Departures up to this many days from today.">
          <CommitInput label="How far ahead" value={p.search_window_days} min={14} max={365} suffix="days"
                       onCommit={(v) => save({ preferences: { search_window_days: Number(v) } })} />
        </Row>
        <Row label="Trip budget" help="The most a whole trip may cost per person. Empty: no limit.">
          <CommitInput label="Trip budget" value={p.max_trip_budget} min={1} prefix="€" placeholder="No limit" width="w-32" optional
                       onCommit={(v) => save({ preferences: { max_trip_budget: v ? Number(v) : null } })} />
        </Row>
      </Section>
      <Section title="Cheaper via a hub" icon="alt_route"
               description="Fly to a nearby hub on one ticket and on from there on another, when that's much cheaper than flying direct.">
        <Row label="Search via hub airports">
          <Switch label="Search via hub airports" on={p.allow_repositioning}
                  onChange={(on) => save({ preferences: { allow_repositioning: on } })} />
        </Row>
        {p.allow_repositioning && (
          <Row label="Hubs" help="London, Amsterdam, Paris…">
            <AirportChips label="Hubs" codes={p.repositioning_hubs} onChange={(codes) => save({ preferences: { repositioning_hubs: codes } })} />
          </Row>
        )}
      </Section>
    </>
  );
}

function Alerts({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const a = s.alerts, p = s.preferences;
  return (
    <>
      <Section title="When a fare is a deal" icon="local_offer"
               description="A fare alerts when it's well below its usual price, when it's for a priority destination, or when it's under your price floor.">
        <Row label="Unusually cheap" help="At least this far below the usual price for its route and trip length.">
          <CommitInput label="Percent below usual" value={a.price_anomaly_min_drop_pct} min={5} max={95} suffix="% below usual"
                       onCommit={(v) => save({ alerts: { price_anomaly_min_drop_pct: Number(v) } })} />
        </Row>
        <Row label="Price floor, Europe" help="A European trip under this always alerts.">
          <CommitInput label="Europe price floor" value={a.europe_trip_max_eur} min={0} max={1000} prefix="€"
                       onCommit={(v) => save({ alerts: { europe_trip_max_eur: Number(v) } })} />
        </Row>
        <Row label="Price floor, long-haul" help="A long-haul trip under this always alerts.">
          <CommitInput label="Long-haul price floor" value={a.longhaul_trip_max_eur} min={0} max={5000} prefix="€"
                       onCommit={(v) => save({ alerts: { longhaul_trip_max_eur: Number(v) } })} />
        </Row>
      </Section>
      <Section title="How often" icon="schedule">
        <Row label="Instant alerts" help="At most this many an hour; the rest wait for the next free slot.">
          <Stepper label="alerts an hour" value={a.instant_alerts_per_hour} min={1} max={60} unit="/h"
                   onChange={(n) => save({ alerts: { instant_alerts_per_hour: n } })} />
        </Row>
        <Row label="Daily digest" help="The day's best deals, in one message.">
          <CommitInput label="Daily digest time" type="time" value={a.digest_time} width="w-28"
                       onCommit={(v) => save({ alerts: { digest_time: v } })} />
        </Row>
        <Row label="Search every" help="Shorter means fresher fares and more requests to the sources.">
          <select aria-label="Search every" className="field w-36" value={a.scrape_interval_minutes}
                  onChange={(e) => save({ alerts: { scrape_interval_minutes: Number(e.target.value) } })}>
            {[...new Set([60, 90, 120, 180, 240, 360, a.scrape_interval_minutes])].sort((x, y) => x - y).map((m) => (
              <option key={m} value={m}>{m < 120 ? `${m} minutes` : `${m / 60} hours`}</option>
            ))}
          </select>
        </Row>
      </Section>
      <Section title="Hotels" icon="hotel">
        <Row label="Minimum rating" help="Hotels rated lower are left out (out of 10).">
          <CommitInput label="Minimum hotel rating" value={p.minimum_hotel_rating} min={0} max={10} step={0.5} suffix="/ 10"
                       onCommit={(v) => save({ preferences: { minimum_hotel_rating: Number(v) } })} />
        </Row>
        <Row label="Minimum reviews" help="Empty: any number.">
          <CommitInput label="Minimum reviews" value={p.min_hotel_review_count} min={0} placeholder="Any" optional
                       onCommit={(v) => save({ preferences: { min_hotel_review_count: v ? Number(v) : null } })} />
        </Row>
      </Section>
    </>
  );
}

function Notifications({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const n = s.notifications;
  const toast = useToast();
  const test = (channel: "desktop" | "telegram") => api.testNotification(channel)
    .then((r) => toast(r.sent[channel] ? "Test sent" : "It didn't arrive: see Activity", { tone: r.sent[channel] ? "ok" : "error" }))
    .catch((err: Error) => toast(err.message, { tone: "error" }));
  return (
    <>
      <Section title="Where alerts go" icon="notifications_active">
        <Row label="Desktop notifications" help="Windows notifications from the app.">
          <button className="btn-ghost" onClick={() => test("desktop")}>Send a test</button>
          <Switch label="Desktop notifications" on={n.desktop} onChange={(on) => save({ notifications: { desktop: on } })} />
        </Row>
        <Row label="Sound" help="Desktop notifications play the Windows sound.">
          <Switch label="Sound" on={n.sound} onChange={(on) => save({ notifications: { sound: on } })} />
        </Row>
        <Row label="Telegram" help={n.telegram_ready ? "Messages from your bot, on your phone too."
          : <>Needs a bot token and chat ID: <a className="text-primary hover:underline" href={href({ screen: "settings", param: "keys" })}>Accounts & keys</a>.</>}>
          {n.telegram_ready && <button className="btn-ghost" onClick={() => test("telegram")}>Send a test</button>}
          <Switch label="Telegram" on={n.telegram} onChange={(on) => save({ notifications: { telegram: on } })} />
        </Row>
      </Section>
      <Section title="What to send" icon="checklist">
        <Row label="Deals as they're found" help="Off: they wait for the daily digest.">
          <Switch label="Deals as they're found" on={n.instant} onChange={(on) => save({ notifications: { instant: on } })} />
        </Row>
        <Row label="Daily digest">
          <Switch label="Daily digest" on={n.digest} onChange={(on) => save({ notifications: { digest: on } })} />
        </Row>
        <Row label="Source problems" help="When a source stops working, and when it works again.">
          <Switch label="Source problems" on={n.source_problems} onChange={(on) => save({ notifications: { source_problems: on } })} />
        </Row>
      </Section>
    </>
  );
}

export function TelegramHelp() {
  return (
    <ol className="flex flex-col gap-1 rounded-lg bg-surface-container-lowest p-space-md text-body text-on-surface-variant">
      {[<>In Telegram, message <code className="font-mono text-primary">@BotFather</code>, send <code className="font-mono text-primary">/newbot</code> and pick a name.</>,
        <>Paste the token it gives you in <strong>Bot token</strong>.</>,
        <>Open your new bot and press <strong>Start</strong> (in a group: add the bot to it).</>,
        <>Your chat ID: message <code className="font-mono text-primary">@userinfobot</code>; for a group, use its ID (it starts with -100).</>,
      ].map((step, i) => (
        <li key={i} className="flex items-start gap-space-sm">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-container-highest font-mono text-mono-sm text-on-surface">{i + 1}</span>
          <span>{step}</span>
        </li>
      ))}
    </ol>
  );
}

function KeyCheck({ what }: { what: "telegram" | "anthropic" }) {
  const [checks, setChecks] = useState<Check[]>();
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col items-end gap-space-xs">
      <button className="btn-secondary" disabled={busy} onClick={() => {
        setBusy(true);
        api.checkKey(what).then((r) => setChecks(r.checks)).catch((err: Error) =>
          setChecks([{ level: "fail", name: "Check", detail: err.message }])).finally(() => setBusy(false));
      }}>
        <Icon name={busy ? "progress_activity" : "fact_check"} size={16} className={busy ? "animate-spin" : ""} />Check
      </button>
      {checks && <div className="w-full max-w-[32rem]"><CheckList checks={checks} /></div>}
    </div>
  );
}

function Keys({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const k = s.keys;
  const saveKey = (name: string, value: string) => save({ keys: { [name]: value } }, value ? "Key saved" : "Key removed");
  return (
    <>
      <Section title="Telegram" icon="send" description="Alerts on your phone, and commands like /deals and /mute."
               right={s.notifications.telegram_ready ? <Pill tone="ok">Set up</Pill> : <Pill tone="off">Not set up</Pill>}>
        <Row label="Bot token" help="From @BotFather.">
          <SecretField label="Telegram bot token" secret={k.telegram_bot_token} onSave={(v) => saveKey("telegram_bot_token", v)} />
        </Row>
        <Row label="Chat ID" help="Where the bot writes: your chat, or a group.">
          <CommitInput label="Telegram chat ID" type="text" value={k.telegram_chat_id} width="w-44" placeholder="e.g. 98231019" optional
                       onCommit={(v) => save({ keys: { telegram_chat_id: v.trim() } }, "Chat ID saved")} />
        </Row>
        <div className="flex items-start justify-between gap-space-xl py-space-md">
          <TelegramHelp />
          <KeyCheck what="telegram" />
        </div>
      </Section>
      <Section title="Anthropic (Claude)" icon="auto_awesome" description="Reads Italian deal-site posts (PiratinViaggio) and polishes alerts. Optional.">
        <Row label="API key" help="From console.anthropic.com.">
          <SecretField label="Anthropic API key" secret={k.anthropic_api_key} onSave={(v) => saveKey("anthropic_api_key", v)} />
        </Row>
        <div className="flex justify-end py-space-md"><KeyCheck what="anthropic" /></div>
      </Section>
      <Section title="More fare sources" icon="travel_explore" description="Optional: more fares, especially long-haul.">
        <Row label="Travelpayouts token" help="Free from travelpayouts.com: 'search everywhere' fares.">
          <SecretField label="Travelpayouts token" secret={k.travelpayouts_token} onSave={(v) => saveKey("travelpayouts_token", v)} />
        </Row>
        <Row label="RapidAPI key" help="For Skyscanner (switch it on in Sources).">
          <SecretField label="RapidAPI key" secret={k.rapidapi_key} onSave={(v) => saveKey("rapidapi_key", v)} />
        </Row>
      </Section>
    </>
  );
}

function Sources({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  return (
    <Section title="Sources" icon="hub" description="Where fares come from. Changes apply from the next search; Activity shows how each one is doing.">
      {Object.entries(s.sources).map(([name, on]) => {
        const id = name.replace(/^enable_/, "").replace("booking_html", "booking_com_api").replace("skyscanner", "skyscanner_api");
        return (
          <Row key={name} label={sourceName(id)} help={SOURCE_HELP[name]}>
            <Switch label={sourceName(id)} on={on} onChange={(next) => save({ sources: { [name]: next } })} />
          </Row>
        );
      })}
      <Row label="Google Flights" help="Always on; it pauses by itself for a few hours after being blocked."><Pill tone="ok">Always on</Pill></Row>
      <Row label="Travelpayouts" help="On when its token is set (Accounts & keys)."><Pill tone={s.keys.travelpayouts_token.set ? "ok" : "off"}>{s.keys.travelpayouts_token.set ? "On" : "Needs a token"}</Pill></Row>
    </Section>
  );
}

function AppSection({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const { choice, choose, textSize, chooseTextSize } = useTheme();
  const { status } = useEngine();
  const toast = useToast();
  const [update, setUpdate] = useState<UpdateInfo | string>();
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <Section title="The app" icon="desktop_windows">
        <Row label="Start when I sign in to Windows" help={s.app.start_at_login === null ? "Only in the installed Windows app." : "In the tray, quietly."}>
          <Switch label="Start when I sign in" on={!!s.app.start_at_login} disabled={s.app.start_at_login === null}
                  onChange={(on) => save({ app: { start_at_login: on } })} />
        </Row>
        <Row label="The close button" help="Closing the window can keep CheapTrip searching in the tray, or quit it.">
          <Segmented label="The close button" value={s.app.close_to_tray ? "tray" : "quit"}
                     options={[{ value: "tray", label: "Keep running in the tray" }, { value: "quit", label: "Quit" }]}
                     onChange={(v) => save({ app: { close_to_tray: v === "tray" } })} />
        </Row>
        <Row label="Theme">
          <Segmented label="Theme" value={choice} onChange={choose} options={[
            { value: "system", label: "Like Windows", icon: "contrast" }, { value: "light", label: "Light", icon: "light_mode" },
            { value: "dark", label: "Dark", icon: "dark_mode" }]} />
        </Row>
        <Row label="Text size" help="Everything grows with the text. Ctrl + and Ctrl − change it anywhere; Ctrl 0 goes back to standard.">
          <Segmented label="Text size" value={textSize} onChange={chooseTextSize} options={[
            { value: "standard", label: "Standard", icon: "text_fields" }, { value: "large", label: "Large", icon: "format_size" },
            { value: "largest", label: "Largest", icon: "text_increase" }]} />
        </Row>
        <Row label="Data folder" help={<span className="font-mono text-mono-sm">{s.files.settings.replace(/[\\/]\.env$/, "")}</span>}>
          <button className="btn-secondary" onClick={() => api.open("data").catch((err: Error) => toast(err.message, { tone: "error" }))}>
            <Icon name="folder_open" size={16} />Open
          </button>
        </Row>
        <Row label="Setup wizard" help="Go through the first-run steps again.">
          <a className="btn-secondary" href={href({ screen: "setup" })}><Icon name="magic_button" size={16} />Open</a>
        </Row>
        <Row label={`Version ${status?.version ?? ""}`} help={typeof update === "string" ? update : update
          ? update.newer ? <>Version {update.latest} is available.</> : <>You have the latest version.</> : "Checks GitHub for a newer release."}>
          {update && typeof update !== "string" && update.newer && (update.download || update.url) && (
            <a className="btn-primary" href={update.download ?? update.url!} target="_blank" rel="noreferrer"><Icon name="download" size={16} />Download {update.latest}</a>
          )}
          <button className="btn-secondary" onClick={() => api.update().then(setUpdate).catch((err: Error) => setUpdate(err.message))}>
            <Icon name="update" size={16} />Check for updates
          </button>
        </Row>
      </Section>
      <Section title="Advanced" icon="build" description="For reporting a problem: the log folder, and the window's developer tools.">
        <Row label="Log folder" help="What CheapTrip did and any errors, one file a day.">
          <button className="btn-secondary" onClick={() => api.open("logs").catch((err: Error) => toast(err.message, { tone: "error" }))}>
            <Icon name="folder_open" size={16} />Open
          </button>
        </Row>
        <Row label="Developer tools" help="F12 (or right-click → Inspect) opens WebView2's developer tools in the window. Applies when CheapTrip starts again.">
          <Switch label="Developer tools" on={s.app.devtools} onChange={(on) => save({ app: { devtools: on } }, on ? "Developer tools on" : "Developer tools off")} />
        </Row>
      </Section>
      <section className="flex flex-col gap-space-md rounded-xl border border-error/40 bg-error-container/10 p-space-lg" aria-label="Danger zone">
        <h2 className="flex items-center gap-space-xs text-headline-sm text-error"><Icon name="warning" size={20} />Danger zone</h2>
        <div className="flex items-center justify-between gap-space-xl">
          <div className="flex flex-col">
            <span className="text-body font-semibold text-on-surface">Clear the price history</span>
            <span className="text-caption text-on-surface-variant">Forget every price seen. CheapTrip learns usual prices again from scratch, which takes about a day.</span>
          </div>
          {confirming ? (
            <span className="flex items-center gap-space-xs">
              <button className="btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
              <button className="btn-danger" onClick={() => {
                setConfirming(false);
                api.clearPriceHistory().then((r) => toast(`Price history cleared (${r.deleted} prices)`))
                  .catch((err: Error) => toast(err.message, { tone: "error" }));
              }}>Yes, clear it</button>
            </span>
          ) : <button className="btn-secondary text-error" onClick={() => setConfirming(true)}>Clear…</button>}
        </div>
      </section>
    </>
  );
}

function Body({ section, s, save }: { section: SectionId; s: SettingsData; save: ReturnType<typeof useSave> }): ReactNode {
  switch (section) {
    case "alerts": return <Alerts s={s} save={save} />;
    case "notifications": return <Notifications s={s} save={save} />;
    case "keys": return <Keys s={s} save={save} />;
    case "sources": return <Sources s={s} save={save} />;
    case "app": return <AppSection s={s} save={save} />;
    default: return <Trips s={s} save={save} />;
  }
}

export function Settings({ section }: { section?: string }) {
  const loaded = useLoad(api.settings, []);
  const [fresh, setFresh] = useState<SettingsData>();
  useReloadOn(["settings_changed", "preferences_changed"], () => { setFresh(undefined); loaded.reload(); });
  const save = useSave(setFresh);
  const s = fresh ?? loaded.data;
  const current = (SECTIONS.find((x) => x.id === section)?.id ?? "trips") as SectionId;

  return (
    <div className="flex flex-col gap-space-lg">
      <PageHeader label="Configuration · Settings" title="Settings">
        Changes save as you make them; each says whether it applies now or from the next search.
      </PageHeader>
      {/* The section menu beside the settings, or above them as tabs where the screen is narrow */}
      <div className="flex flex-col gap-space-lg @4xl:flex-row @4xl:items-start">
        <nav aria-label="Settings sections"
             className="flex flex-wrap gap-space-xs @4xl:sticky @4xl:top-[5.5rem] @4xl:w-56 @4xl:shrink-0 @4xl:flex-col">
          {SECTIONS.map((x) => (
            <a key={x.id} href={href({ screen: "settings", param: x.id })} aria-current={current === x.id ? "page" : undefined}
               className={`flex items-center gap-space-md rounded-lg px-space-md py-2.5 text-body ${current === x.id
                 ? "border border-stroke bg-surface-container-high font-semibold text-primary"
                 : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"}`}>
              <Icon name={x.icon} size={18} filled={current === x.id} />{x.label}
            </a>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col gap-space-lg">
          {!s ? (loaded.error ? <p className="text-body text-error">{loaded.error}</p> : <div className="skeleton h-96" />)
            : <Body section={current} s={s} save={save} />}
          {s && current !== "app" && (
            <p className="text-caption text-outline">Stored in {s.files.settings} and {s.files.preferences}.</p>
          )}
        </div>
      </div>
    </div>
  );
}
