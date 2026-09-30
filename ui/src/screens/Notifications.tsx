/**
 * Notifications & tray (the design's "Windows Toasts" page): the notifications
 * CheapTrip would show now, drawn like Windows 11 toasts from the same text the
 * engine sends (each can be sent for real), the tray icon's states and menu,
 * and the switches that decide what reaches you.
 */
import type { ReactNode } from "react";
import { api, type NotificationPreview, type PreviewKind } from "../api";
import { Icon } from "../components/Icon";
import { Logo } from "../components/Logo";
import { PageHeader, Pill } from "../components/Page";
import { useToast } from "../components/Toast";
import { useEngine } from "../engine";
import { useReloadOn } from "../events";
import { clock } from "../format";
import { useLoad } from "../load";
import { href } from "../router";

const KINDS: Record<PreviewKind, { label: string; icon: string; tone: string }> = {
  deal: { label: "Deal", icon: "local_offer", tone: "text-secondary" },
  error_fare: { label: "Possible error fare", icon: "warning", tone: "text-error" },
  digest: { label: "Daily digest", icon: "summarize", tone: "text-primary" },
  notice: { label: "Source problem", icon: "report", tone: "text-tertiary" },
  test: { label: "Test", icon: "notifications", tone: "text-on-surface-variant" },
};

const TRAY_STATES = [
  { state: "running", label: "Running", detail: "Watching prices between searches" },
  { state: "searching", label: "Searching…", detail: "Asking the sources for fares" },
  { state: "paused", label: "Alerts paused", detail: "Searching on, alerts held until you resume" },
  { state: "attention", label: "Needs attention", detail: "A source is failing or the last search failed" },
] as const;

function ToastPreview({ preview, onSend }: { preview: NotificationPreview; onSend: () => void }) {
  const kind = KINDS[preview.kind];
  return (
    <article className="card flex flex-col justify-between gap-space-md p-space-md" data-testid="toast-preview">
      <div className="flex flex-col gap-space-sm">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-space-xs text-caption text-on-surface-variant">
            <Logo size={16} />CheapTrip · just now
          </span>
          <span className={`flex items-center gap-1 text-caption font-semibold ${kind.tone}`}>
            <Icon name={kind.icon} size={14} />{kind.label}{preview.example ? " (example)" : ""}
          </span>
        </div>
        {preview.image && (
          <img src={preview.image} alt="" className="w-full rounded-lg border border-stroke" />
        )}
        <div className="flex flex-col gap-0.5">
          <span className="text-body font-semibold text-on-surface">{preview.title}</span>
          <span className="text-body text-on-surface-variant">{preview.message}</span>
        </div>
        {preview.buttons.length > 0 && (
          <div className="grid gap-space-xs" style={{ gridTemplateColumns: `repeat(${preview.buttons.length}, minmax(0, 1fr))` }}>
            {preview.buttons.map((label, i) => (
              <span key={label} className={`flex h-8 items-center justify-center truncate rounded px-space-sm text-body ${i === 0
                ? preview.kind === "error_fare" ? "bg-error text-on-error" : "bg-primary-container text-on-primary"
                : "bg-surface-container-highest text-on-surface"}`}>{label}</span>
            ))}
          </div>
        )}
      </div>
      <button className="btn-ghost self-end font-mono text-mono-sm text-primary" onClick={onSend}>
        <Icon name="send" size={14} />Show it on this PC
      </button>
    </article>
  );
}

function Card({ title, icon, right, children }: { title: string; icon: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card flex flex-col gap-space-md p-space-md">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-space-xs text-body-lg font-semibold text-on-surface"><Icon name={icon} size={18} />{title}</h3>
        {right && <span className="font-mono text-mono-sm text-outline">{right}</span>}
      </div>
      {children}
    </section>
  );
}

function Switch({ label, detail, on, onChange }: { label: string; detail: string; on: boolean; onChange: (on: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-space-md rounded-lg bg-surface-container-low p-space-sm">
      <div className="flex flex-col">
        <span className="text-body text-on-surface">{label}</span>
        <span className="text-caption text-on-surface-variant">{detail}</span>
      </div>
      <button role="switch" aria-checked={on} aria-label={label} className="switch" onClick={() => onChange(!on)} />
    </div>
  );
}

export function Notifications() {
  const previews = useLoad(api.notificationPreviews, []);
  const { status } = useEngine();
  const toast = useToast();
  useReloadOn(["cycle_finished", "settings_changed", "alert_sent"], previews.reload);
  const data = previews.data;

  const send = (kind?: PreviewKind) => api.testNotification("desktop", kind)
    .then((r) => toast(r.sent.desktop ? "Sent: look at the bottom right of your screen" : "Windows didn't show it: check its notification settings",
      { tone: r.sent.desktop ? "ok" : "error" }))
    .catch((err: Error) => toast(err.message, { tone: "error" }));
  const setSwitch = (name: "desktop" | "sound", on: boolean) => api.saveSettings({ notifications: { [name]: on } })
    .then(() => { previews.reload(); toast(`${name === "sound" ? "Sound" : "Desktop notifications"} ${on ? "on" : "off"}`); })
    .catch((err: Error) => toast(err.message, { tone: "error" }));
  const openWindowsSettings = () => api.open("notification-settings").catch((err: Error) => toast(err.message, { tone: "error" }));
  const state = status?.state === "stopped" ? "running" : status?.state;
  const desktopApp = status?.app === "desktop";

  return (
    <div className="flex flex-col gap-space-xl">
      <PageHeader label="Windows · Notifications & tray" title="Notifications & tray" actions={
        <>
          <button className="btn-primary" onClick={() => send()} disabled={!data?.desktop.available}><Icon name="send" size={16} />Send a test notification</button>
          <a className="btn-secondary" href={href({ screen: "settings", param: "notifications" })}><Icon name="tune" size={16} />Notification settings</a>
        </>
      }>
        How CheapTrip's notifications look on Windows, with today's deals, and what the tray icon tells you.
      </PageHeader>

      <section className="flex flex-col gap-space-md" aria-label="Notification previews">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-space-sm text-headline-sm text-on-surface">
            <Icon name="notifications_active" size={22} />What you'll see
            <span className="rounded bg-surface-container-highest px-2 py-0.5 font-mono text-mono-sm text-on-surface-variant">From today's deals</span>
          </h2>
          {data && (data.desktop.available
            ? <Pill tone={data.desktop.on ? "ok" : "off"}>{data.desktop.on ? "Desktop notifications on" : "Desktop notifications off"}</Pill>
            : <Pill tone="off">Only in the desktop app</Pill>)}
        </div>
        {previews.error && <p className="text-body text-error">{previews.error}</p>}
        <div className="grid grid-cols-1 gap-gutter @3xl:grid-cols-2 @5xl:grid-cols-3">
          {data ? data.previews.map((p) => <ToastPreview key={p.kind} preview={p} onSend={() => send(p.kind)} />)
            : [0, 1, 2].map((i) => <div key={i} className="skeleton h-56" />)}
          {data && (
            <Card title="How CheapTrip appears in Windows" icon="desktop_windows">
              <dl className="grid grid-cols-2 gap-y-1 rounded-lg bg-surface-container-lowest p-space-sm font-mono text-mono-sm">
                <dt className="text-on-surface-variant">App ID</dt><dd className="text-right text-primary">{data.app_id}</dd>
                <dt className="text-on-surface-variant">Sound</dt><dd className="text-right text-primary">{data.desktop.sound ? "Windows default" : "Silent"}</dd>
              </dl>
              <Switch label="Desktop notifications" detail="Deals, the digest and source problems on this PC" on={data.desktop.on}
                      onChange={(on) => setSwitch("desktop", on)} />
              <Switch label="Sound" detail="Play the Windows notification sound" on={data.desktop.sound}
                      onChange={(on) => setSwitch("sound", on)} />
            </Card>
          )}
        </div>
      </section>

      <section className="flex flex-col gap-space-md" aria-label="System tray">
        <h2 className="flex items-center gap-space-sm text-headline-sm text-on-surface"><Icon name="system_update_alt" size={22} />The tray icon</h2>
        <div className="grid grid-cols-1 gap-gutter @5xl:grid-cols-3">
          <Card title="What the icon says" icon="smartphone" right="16 / 32 px">
            <p className="text-body text-on-surface-variant">CheapTrip keeps running in the tray (by the clock) when you close its window. The dot shows what it's doing:</p>
            <div className="flex flex-col gap-space-xs">
              {TRAY_STATES.map((s) => (
                <div key={s.state} className={`flex items-center gap-space-md rounded-lg p-space-sm ${state === s.state ? "bg-primary-container/15" : "bg-surface-container-low"}`}>
                  <img src={`./tray/tray-${s.state}.png`} alt="" className="h-8 w-8" />
                  <div className="flex flex-1 flex-col">
                    <span className="text-body text-on-surface">{s.label}</span>
                    <span className="text-caption text-on-surface-variant">{s.detail}</span>
                  </div>
                  {state === s.state && <Pill tone="info">Now</Pill>}
                </div>
              ))}
            </div>
          </Card>
          <Card title="Its menu" icon="menu_open" right="Right-click the icon">
            <div className="flyout flex flex-col gap-0.5 p-space-xs text-body">
              <span className="flex items-center gap-space-xs rounded px-space-md py-1.5 font-mono text-mono-sm text-on-surface-variant">
                <span className="h-1.5 w-1.5 rounded-full bg-secondary" />
                CheapTrip {status?.version} · {status?.state === "paused" ? "Alerts paused" : `Running${status?.next_search_at ? ` (next ${clock(status.next_search_at)})` : ""}`}
              </span>
              <span className="my-0.5 h-px bg-stroke" />
              {[["open_in_new", "Open CheapTrip"], ["radar", "Search now"], ["pause_circle", status?.state === "paused" ? "Resume alerts" : "Pause alerts ›"],
                ["settings", "Settings"]].map(([icon, label]) => (
                <span key={label} className="flex items-center gap-space-sm rounded px-space-md py-1.5 text-on-surface"><Icon name={icon} size={18} />{label}</span>
              ))}
              <span className="my-0.5 h-px bg-stroke" />
              <span className="flex items-center gap-space-sm rounded px-space-md py-1.5 text-error"><Icon name="power_settings_new" size={18} />Quit CheapTrip</span>
            </div>
            <p className="text-caption text-outline">Pause alerts: for 1 hour, 4 hours, until tomorrow morning or until you resume. Searches go on.</p>
          </Card>
          <Card title="Focus and Do not disturb" icon="do_not_disturb_on" right="Set in Windows">
            <p className="text-body text-on-surface-variant">
              Windows decides whether notifications show while you're busy: Do not disturb and Focus hold them in the
              notification centre. To let deals through, or to show them on the lock screen, change CheapTrip's
              settings in Windows.
            </p>
            {desktopApp ? (
              <button className="btn-secondary self-start" onClick={openWindowsSettings}>
                <Icon name="open_in_new" size={16} />Open Windows notification settings
              </button>
            ) : <span className="text-caption text-outline">Settings → System → Notifications → CheapTrip</span>}
          </Card>
        </div>
      </section>
    </div>
  );
}
