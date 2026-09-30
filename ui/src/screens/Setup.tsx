/**
 * The first-run setup wizard, as designed: five steps (Welcome, where you fly
 * from, how to tell you, optional extras, checks) with a stepper, a sticky
 * footer (Back · Step 3 of 5 · Skip optional steps · Save & continue), and the
 * finish that says what happens next. It opens at the first launch and stays
 * in the sidebar ("Setup wizard") to run again.
 */
import { useEffect, useState, type ReactNode } from "react";
import { api, type Settings as SettingsData } from "../api";
import { AirportChips, CheckList, CommitInput, SecretField, Stepper, Switch } from "../components/Form";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";
import { useToast } from "../components/Toast";
import { useEngine } from "../engine";
import { useLoad } from "../load";
import { navigate } from "../router";
import { LENGTHS, TelegramHelp, useSave } from "./Settings";

const STEPS = [
  { title: "Welcome", icon: "waving_hand", heading: "Cheap flights, found for you", label: "Welcome" },
  { title: "Origins", icon: "flight_takeoff", heading: "Where do you fly from?", label: "Home airports & trips" },
  { title: "Notifications", icon: "notifications_active", heading: "How should we tell you?", label: "Alerts & delivery" },
  { title: "Extras", icon: "key", heading: "Optional extras", label: "API keys (optional)" },
  { title: "Checks", icon: "fact_check", heading: "Everything ready?", label: "Checks" },
] as const;

function Stepper5({ step, onGo }: { step: number; onGo: (step: number) => void }) {
  return (
    <nav aria-label="Setup steps" className="pane grid grid-cols-5 gap-space-xs p-space-xs">
      {STEPS.map((s, i) => {
        const done = i < step, active = i === step;
        return (
          <button key={s.title} onClick={() => onGo(i)} aria-current={active ? "step" : undefined}
                  className={`flex items-center gap-space-sm rounded-lg p-space-sm text-left ${active ? "bg-primary-container/15" : done ? "bg-surface-container-high" : ""}`}>
            <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${done ? "bg-secondary-container text-on-secondary-container"
              : active ? "bg-primary-container text-on-primary" : "border border-outline-variant font-mono text-mono-sm text-outline"}`}>
              {done ? <Icon name="check" size={18} /> : active ? <Icon name={s.icon} size={18} /> : i + 1}
            </span>
            <span className="flex min-w-0 flex-col">
              <span className={`font-mono text-mono-sm tracking-wider uppercase ${active || done ? "text-secondary" : "text-outline"}`}>
                Step {i + 1}{active ? " · now" : ""}
              </span>
              <span className={`truncate text-body ${active ? "font-semibold text-primary" : done ? "text-on-surface" : "text-outline"}`}>{s.title}</span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}

function Point({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <div className="card flex flex-col gap-space-sm p-space-md">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container/20 text-primary"><Icon name={icon} size={22} /></div>
      <span className="text-body-lg font-semibold text-on-surface">{title}</span>
      <span className="text-body text-on-surface-variant">{children}</span>
    </div>
  );
}

function Welcome({ interval }: { interval: number }) {
  return (
    <div className="flex flex-col gap-space-lg">
      <div className="flex items-center gap-space-md">
        <Logo size={56} />
        <p className="max-w-2xl text-body-lg text-on-surface-variant">
          CheapTrip is your own travel-deal radar. It runs quietly in the tray and tells you when a flight is
          unusually cheap. Setting it up takes about two minutes.
        </p>
      </div>
      <div className="grid grid-cols-3 gap-gutter">
        <Point icon="radar" title="It searches for you">Every {interval} minutes it checks Ryanair, Google Flights and deal sites, from your home airports.</Point>
        <Point icon="insights" title="It knows a real bargain">It learns what each route usually costs, then alerts when a fare is well below that, or is a likely error fare.</Point>
        <Point icon="notifications_active" title="It tells you at once">A Windows notification with Book, Details and Mute, and on Telegram if you like. Then a daily digest.</Point>
      </div>
    </div>
  );
}

function Origins({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const p = s.preferences;
  return (
    <div className="grid grid-cols-2 gap-gutter">
      <div className="card flex flex-col gap-space-md p-space-lg">
        <span className="text-body-lg font-semibold text-on-surface">Home airports</span>
        <span className="text-body text-on-surface-variant">Airports of the same city are searched together: MXP covers Linate and Bergamo too.</span>
        <AirportChips label="Home airports" codes={p.home_airports}
                      onChange={(codes) => codes.length && save({ preferences: { home_airports: codes } }, "Home airports saved")} />
        <div className="flex items-center justify-between pt-space-sm">
          <span className="flex flex-col"><span className="text-body font-semibold text-on-surface">Travellers</span>
            <span className="text-caption text-on-surface-variant">Prices are shown per person.</span></span>
          <Stepper label="travellers" value={p.adults} min={1} max={9} onChange={(adults) => save({ preferences: { adults } })} />
        </div>
      </div>
      <div className="card flex flex-col gap-space-md p-space-lg">
        <span className="text-body-lg font-semibold text-on-surface">Trips you like</span>
        <span className="text-body text-on-surface-variant">CheapTrip searches these lengths; you can change them any time in Settings.</span>
        <div className="grid grid-cols-2 gap-space-xs">
          {LENGTHS.map((l) => {
            const on = p.preferred_trip_lengths.includes(l.value);
            return (
              <button key={l.value} role="checkbox" aria-checked={on} onClick={() => {
                const next = on ? p.preferred_trip_lengths.filter((x) => x !== l.value) : [...p.preferred_trip_lengths, l.value];
                if (next.length) void save({ preferences: { preferred_trip_lengths: next } });
              }} className={`flex items-center gap-space-sm rounded-lg border p-space-md text-left ${on
                ? "border-primary-container bg-primary-container/10" : "border-stroke hover:bg-surface-container-highest"}`}>
                <Icon name={on ? "check_box" : "check_box_outline_blank"} size={20} className={on ? "text-primary" : "text-outline"} />
                <span className="flex flex-col"><span className="text-body font-semibold text-on-surface">{l.label}</span>
                  <span className="font-mono text-mono-sm text-outline">{l.nights}</span></span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Delivery({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const toast = useToast();
  const n = s.notifications;
  const [telegramCheck, setTelegramCheck] = useState<Awaited<ReturnType<typeof api.checkKey>>>();
  const test = (channel: "desktop" | "telegram") => api.testNotification(channel)
    .then((r) => toast(r.sent[channel] ? "Test sent" : "It didn't arrive: see Activity", { tone: r.sent[channel] ? "ok" : "error" }))
    .catch((err: Error) => toast(err.message, { tone: "error" }));
  return (
    <div className="grid grid-cols-2 gap-gutter">
      <div className="card flex flex-col justify-between gap-space-md p-space-lg">
        <div className="flex flex-col gap-space-md">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-space-md">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-container-highest text-primary"><Icon name="desktop_windows" size={22} /></div>
              <div className="flex flex-col"><span className="text-body-lg font-semibold text-on-surface">Windows notifications</span>
                <span className="font-mono text-mono-sm text-primary">In the notification centre</span></div>
            </div>
            <Switch label="Windows notifications" on={n.desktop} onChange={(on) => save({ notifications: { desktop: on } })} />
          </div>
          <span className="text-body text-on-surface-variant">A notification for each deal, with buttons to book it, see the details, or mute the destination.</span>
          <div className="flex flex-col gap-1 rounded-lg bg-surface-container-lowest p-space-md">
            <span className="flex justify-between font-mono text-mono-sm text-on-surface-variant"><span className="flex items-center gap-1"><Logo size={14} />CheapTrip</span><span>just now</span></span>
            <span className="text-body font-semibold text-on-surface">Milan → Berlin · €30</span>
            <span className="font-mono text-mono-sm text-secondary">↓ 46% below the usual €56 · Wed 21–28 Oct</span>
            <span className="mt-1 grid grid-cols-3 gap-1 text-center text-caption">
              <span className="rounded bg-primary-container py-1 text-on-primary">Book €30</span>
              <span className="rounded bg-surface-container-highest py-1 text-on-surface">Details</span>
              <span className="rounded bg-surface-container-highest py-1 text-on-surface">Mute Berlin</span>
            </span>
          </div>
          <label className="flex items-center gap-space-sm text-body text-on-surface">
            <input type="checkbox" className="h-4 w-4 accent-[var(--c-primary-container)]" checked={n.sound}
                   onChange={(e) => save({ notifications: { sound: e.target.checked } })} />
            Play the notification sound
          </label>
        </div>
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-space-xs font-mono text-mono-sm text-outline"><Icon name="speed" size={16} className="text-tertiary" />
            At most {s.alerts.instant_alerts_per_hour} instant alerts an hour</span>
          <button className="btn-secondary" onClick={() => test("desktop")} disabled={!n.desktop}><Icon name="notifications" size={16} />Send a test</button>
        </div>
      </div>
      <div className="card flex flex-col justify-between gap-space-md p-space-lg">
        <div className="flex flex-col gap-space-md">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-space-md">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-container-highest text-primary"><Icon name="send" size={22} /></div>
              <div className="flex flex-col"><span className="text-body-lg font-semibold text-on-surface">Telegram <span className="font-normal text-outline">(optional)</span></span>
                <span className="font-mono text-mono-sm text-secondary">On your phone too</span></div>
            </div>
            <Switch label="Telegram" on={n.telegram} onChange={(on) => save({ notifications: { telegram: on } })} />
          </div>
          <label className="flex flex-col gap-space-xs"><span className="label-caps">Bot token</span>
            <SecretField label="Telegram bot token" secret={s.keys.telegram_bot_token}
                         onSave={(v) => save({ keys: { telegram_bot_token: v } }, "Bot token saved")} /></label>
          <label className="flex flex-col gap-space-xs"><span className="label-caps">Chat ID</span>
            <CommitInput label="Telegram chat ID" type="text" value={s.keys.telegram_chat_id} width="w-56" placeholder="e.g. 98231019" optional
                         onCommit={(v) => save({ keys: { telegram_chat_id: v.trim() } }, "Chat ID saved")} /></label>
          {telegramCheck && <CheckList checks={telegramCheck.checks} />}
          <TelegramHelp />
        </div>
        <div className="flex items-center justify-end gap-space-sm">
          <button className="btn-secondary" disabled={!n.telegram_ready}
                  onClick={() => api.checkKey("telegram").then(setTelegramCheck).catch((err: Error) => toast(err.message, { tone: "error" }))}>
            <Icon name="fact_check" size={16} />Check</button>
          <button className="btn-secondary" disabled={!n.telegram_ready} onClick={() => test("telegram")}><Icon name="send" size={16} />Send a test message</button>
        </div>
      </div>
    </div>
  );
}

function Extras({ s, save }: { s: SettingsData; save: ReturnType<typeof useSave> }) {
  const [check, setCheck] = useState<Awaited<ReturnType<typeof api.checkKey>>>();
  return (
    <div className="grid grid-cols-2 gap-gutter">
      <div className="card flex flex-col gap-space-md p-space-lg">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-space-sm text-body-lg font-semibold text-on-surface"><Icon name="auto_awesome" size={20} className="text-primary" />Anthropic API key</span>
          <span className="rounded bg-surface-container-highest px-2 py-0.5 text-caption text-on-surface-variant">Optional</span>
        </div>
        <span className="text-body text-on-surface-variant">Claude reads Italian deal-site posts (PiratinViaggio: flights, packages and hotels) and polishes alerts. From console.anthropic.com.</span>
        <SecretField label="Anthropic API key" secret={s.keys.anthropic_api_key} onSave={(v) => save({ keys: { anthropic_api_key: v } }, "Key saved")} />
        {check && <CheckList checks={check.checks} />}
        <button className="btn-secondary self-end" disabled={!s.keys.anthropic_api_key.set}
                onClick={() => api.checkKey("anthropic").then(setCheck)}><Icon name="fact_check" size={16} />Check</button>
      </div>
      <div className="card flex flex-col gap-space-md p-space-lg">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-space-sm text-body-lg font-semibold text-on-surface"><Icon name="travel_explore" size={20} className="text-primary" />Travelpayouts token</span>
          <span className="rounded bg-surface-container-highest px-2 py-0.5 text-caption text-on-surface-variant">Optional</span>
        </div>
        <span className="text-body text-on-surface-variant">One more fare source that searches everywhere, good for long-haul. Free at travelpayouts.com.</span>
        <SecretField label="Travelpayouts token" secret={s.keys.travelpayouts_token} onSave={(v) => save({ keys: { travelpayouts_token: v } }, "Token saved")} />
      </div>
    </div>
  );
}

function Checks() {
  const checks = useLoad(api.checks, []);
  const toast = useToast();
  const list = checks.data?.checks ?? [];
  const passed = list.filter((c) => c.level === "ok").length;
  return (
    <div className="flex flex-col gap-space-md">
      <div className="flex items-center justify-between">
        <span className="text-body text-on-surface-variant">
          {checks.loading ? "Checking your setup…" : `${passed} of ${list.length} checks passed. Warnings are fine: they're optional extras.`}
        </span>
        <span className="flex gap-space-sm">
          <button className="btn-secondary" onClick={checks.reload} disabled={checks.loading}>
            <Icon name={checks.loading ? "progress_activity" : "refresh"} size={16} className={checks.loading ? "animate-spin" : ""} />Check again</button>
          <button className="btn-secondary" onClick={() => api.testNotification().then(() => toast("Test sent"))
            .catch((err: Error) => toast(err.message, { tone: "error" }))}><Icon name="notifications" size={16} />Send a test notification</button>
        </span>
      </div>
      {checks.loading && !checks.data ? <div className="skeleton h-48" /> : <div className="grid grid-cols-2 gap-space-xs"><CheckList checks={list} /></div>}
      <div className="flex items-start gap-space-sm rounded-xl bg-surface-container-high p-space-md text-body text-on-surface">
        <Icon name="info" size={20} className="text-tertiary" />
        <p><strong className="text-tertiary">First search running…</strong> Unusually-cheap alerts start after about a day, while
          CheapTrip learns normal prices. Priority destinations and your price floors alert right away.</p>
      </div>
    </div>
  );
}

export function Setup() {
  const loaded = useLoad(api.settings, []);
  const [fresh, setFresh] = useState<SettingsData>();
  const [step, setStep] = useState(0);
  const { status, reload } = useEngine();
  const toast = useToast();
  const save = useSave(setFresh);
  const s = fresh ?? loaded.data;
  useEffect(() => window.scrollTo?.(0, 0), [step]);

  const finish = () => api.saveSettings({ app: { setup_done: true } })
    .then(() => { reload(); toast("All set: CheapTrip is watching prices"); navigate({ screen: "deals" }); })
    .catch((err: Error) => toast(err.message, { tone: "error" }));
  const last = step === STEPS.length - 1;

  return (
    <div className="flex flex-col gap-space-lg pb-24">
      <header className="flex flex-wrap items-end justify-between gap-space-md">
        <div className="flex flex-col gap-space-xs">
          <span className="flex items-center gap-space-sm">
            <span className="flex items-center gap-space-xs rounded bg-surface-container-high px-2 py-0.5 font-mono text-mono-sm tracking-[0.15em] text-primary uppercase">
              <span className="h-1.5 w-1.5 rounded-full bg-primary" />First-run setup</span>
            {status && <span className="font-mono text-mono-sm text-outline">v{status.version}</span>}
          </span>
          <h1 className="text-headline-lg text-on-surface">Setup wizard</h1>
          <p className="text-body text-on-surface-variant">Where you fly from and how CheapTrip tells you about deals, in five steps.</p>
        </div>
        {s && <span className="flex items-center gap-space-xs text-caption text-on-surface-variant"><Icon name="verified_user" size={16} className="text-secondary" />
          Saved on this PC as you go, in <code className="font-mono text-on-surface">{s.files.settings}</code></span>}
      </header>

      <Stepper5 step={step} onGo={setStep} />

      <section className="pane flex flex-col gap-space-lg p-space-lg" aria-label={STEPS[step].heading}>
        <div className="flex flex-col gap-space-xs">
          <div className="flex items-center justify-between">
            <span className="font-mono text-mono-sm tracking-[0.15em] text-primary uppercase">Step {step + 1} of 5: {STEPS[step].label}</span>
            {status && status.state !== "stopped" && (
              <span className="flex items-center gap-1 rounded bg-secondary-container/20 px-2 py-0.5 font-mono text-mono-sm text-secondary">
                <span className="h-1.5 w-1.5 rounded-full bg-secondary" />{status.searching ? "Searching now" : "Engine running"}</span>
            )}
          </div>
          <h2 className="text-headline-lg text-on-surface">{STEPS[step].heading}</h2>
        </div>
        {!s ? (loaded.error ? <p className="text-body text-error">{loaded.error}</p> : <div className="skeleton h-64" />)
          : step === 0 ? <Welcome interval={s.alerts.scrape_interval_minutes} />
            : step === 1 ? <Origins s={s} save={save} />
              : step === 2 ? <Delivery s={s} save={save} />
                : step === 3 ? <Extras s={s} save={save} />
                  : <Checks />}
        {step < 3 && (
          <div className="flex flex-col gap-space-xs">
            <span className="label-caps">Coming next</span>
            {STEPS.slice(step + 1).map((next, i) => (
              <div key={next.title} className="flex items-center justify-between rounded-lg bg-surface-container-low px-space-md py-space-sm">
                <span className="flex items-center gap-space-md">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full border border-outline-variant font-mono text-mono-sm text-outline">{step + i + 2}</span>
                  <span className="text-body text-on-surface">{next.heading}</span>
                </span>
                {next.title === "Extras" && <span className="rounded bg-surface-container-highest px-2 py-0.5 text-caption text-on-surface-variant">Optional</span>}
              </div>
            ))}
          </div>
        )}
      </section>

      <footer className="sticky bottom-space-md z-20 flex items-center justify-between rounded-xl border border-stroke bg-surface-container-high/95 p-space-md shadow-flyout backdrop-blur-xl">
        <div className="flex items-center gap-space-md">
          <button className="btn-secondary" onClick={() => setStep((x) => Math.max(0, x - 1))} disabled={step === 0}>
            <Icon name="arrow_back" size={16} />Back</button>
          <span className="font-mono text-mono-sm text-primary">Step {step + 1} of 5 <span className="font-sans text-caption text-on-surface-variant">· {STEPS[step].title}</span></span>
        </div>
        <div className="flex items-center gap-space-md">
          {step < 3 && <button className="btn-ghost" onClick={() => setStep(4)}>Skip optional steps</button>}
          {last ? <button className="btn-primary" onClick={finish}>Finish<Icon name="check" size={16} /></button>
            : <button className="btn-primary" onClick={() => setStep((x) => x + 1)}>
              {step === 0 ? "Let's start" : `Continue to ${STEPS[step + 1].title}`}<Icon name="arrow_forward" size={16} /></button>}
        </div>
      </footer>
    </div>
  );
}
