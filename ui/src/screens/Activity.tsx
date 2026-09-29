/**
 * Activity (built in the design's style): the engine and its learning
 * progress, every source with its status and switch, the notifications sent
 * (and the hourly limit), and the search history.
 */
import { api, type AlertLogEntry, type Cycle, type Source } from "../api";
import { Icon } from "../components/Icon";
import { PageHeader, Pill } from "../components/Page";
import { useToast } from "../components/Toast";
import { StateDot, statusText } from "../components/TopBar";
import { progressLabel, useEngine } from "../engine";
import { useReloadOn } from "../events";
import { ago, clock, euros, fromNow, when } from "../format";
import { useLoad } from "../load";
import { href } from "../router";
import { sourceName } from "../sources";

const SOURCE_STATUS: Record<string, { tone: "ok" | "warn" | "bad" | "off"; icon: string; label: string }> = {
  OK: { tone: "ok", icon: "check_circle", label: "OK" },
  DEGRADED: { tone: "warn", icon: "warning", label: "Degraded" },
  FAILING: { tone: "bad", icon: "error", label: "Failing" },
  STALE: { tone: "warn", icon: "schedule", label: "Stale" },
  DISABLED: { tone: "off", icon: "block", label: "Off" },
  UNKNOWN: { tone: "off", icon: "hourglass_empty", label: "Not run yet" },
};

function sourceNote(s: Source): string {
  if (!s.enabled) return s.reason ?? "Switched off";
  if (s.last_error) return s.last_error;
  if (s.status === "OK" && s.last_count === 0) return "Nothing new last time";
  if (s.status === "UNKNOWN") return "Runs at the next search";
  return "";
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="label-caps">{label}</span>
      <span className="font-mono text-mono-lg text-on-surface">{value}</span>
      {detail && <span className="font-mono text-mono-sm text-outline">{detail}</span>}
    </div>
  );
}

function EngineCard() {
  const { status, progress, searchNow, busy } = useEngine();
  if (!status) return <div className="skeleton h-44 xl:col-span-2" />;
  const last = status.last_cycle;
  const learning = status.learning;
  const known = learning?.trips ? (learning.with_usual_price ?? 0) / learning.trips : 0;
  return (
    <section className="card flex flex-col gap-space-lg p-space-lg xl:col-span-2" aria-label="Engine">
      <div className="flex items-center justify-between gap-space-md">
        <div className="flex items-center gap-space-sm">
          <StateDot state={status.state} />
          <h2 className="text-headline-sm text-on-surface">{statusText(status, progress)}</h2>
        </div>
        <button className="btn-secondary" onClick={searchNow} disabled={busy || status.searching || status.state === "stopped"}>
          <Icon name="radar" size={16} />Search now
        </button>
      </div>
      <div className="grid grid-cols-4 gap-space-md">
        <Metric label="Last search" value={last ? clock(last.finished_at ?? last.started_at) : "–"}
                detail={last?.duration_s ? `took ${Math.round(last.duration_s)} s` : undefined} />
        <Metric label="Fares checked" value={last?.fares !== null && last ? String(last.fares) : "–"}
                detail={last ? `${last.trips ?? 0} trips built` : undefined} />
        <Metric label="Next search" value={status.next_search_at ? clock(status.next_search_at) : "–"}
                detail={status.next_search_at ? fromNow(status.next_search_at) : undefined} />
        <Metric label="Searches today" value={String(status.searches_today)}
                detail={status.searching ? progressLabel(progress) || "searching…" : undefined} />
      </div>
      <div className="flex flex-col gap-space-xs">
        <div className="flex items-center justify-between text-body">
          <span className="flex items-center gap-space-xs font-semibold text-on-surface"><Icon name="model_training" size={18} className="text-tertiary" />Learning prices</span>
          <span className="font-mono text-mono-sm text-on-surface-variant">
            {learning?.trips ? `${learning.with_usual_price ?? 0} of ${learning.trips} trips have a usual price` : "after the first search"}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-container-lowest">
          <div className="h-full rounded-full bg-tertiary-container" style={{ width: `${Math.round(known * 100)}%` }} />
        </div>
        <span className="text-caption text-outline">
          A fare counts as unusually cheap once similar fares have been seen for a while. That takes about a day on a new
          install; priority destinations and your price floors alert from the start.
        </span>
      </div>
      {status.attention.length > 0 && (
        <div className="flex flex-col gap-1 rounded-lg bg-error-container/20 p-space-md" role="alert">
          {status.attention.map((item) => (
            <span key={item} className="flex items-center gap-space-xs text-body text-error"><Icon name="warning" size={16} />{item}</span>
          ))}
        </div>
      )}
    </section>
  );
}

function AlertsCard({ limit, sentLastHour, waiting }: { limit: number; sentLastHour: number; waiting: number }) {
  return (
    <section className="card flex flex-col gap-space-md p-space-lg" aria-label="Alert limit">
      <h2 className="flex items-center gap-space-xs text-headline-sm text-on-surface"><Icon name="speed" size={20} className="text-primary" />Hourly limit</h2>
      <div className="flex items-baseline gap-space-xs">
        <span className="font-mono text-display text-on-surface">{sentLastHour}</span>
        <span className="text-body text-on-surface-variant">of {limit} instant alerts sent in the last hour</span>
      </div>
      <div className="flex gap-1" aria-hidden="true">
        {Array.from({ length: limit }, (_, i) => (
          <span key={i} className={`h-2 flex-1 rounded-full ${i < sentLastHour ? "bg-primary-container" : "bg-surface-container-lowest"}`} />
        ))}
      </div>
      <span className={`text-body ${waiting ? "text-tertiary" : "text-on-surface-variant"}`}>
        {waiting ? `${waiting} ${waiting === 1 ? "deal is" : "deals are"} waiting for the next free slot.` : "Nothing waiting."}
      </span>
      <a className="text-body text-primary hover:underline" href={href({ screen: "settings", param: "alerts" })}>Change the limit</a>
    </section>
  );
}

function SourcesTable({ sources, switches, onToggle }: {
  sources: Source[]; switches: Record<string, boolean> | undefined; onToggle: (s: Source, on: boolean) => void;
}) {
  return (
    <section className="pane flex flex-col gap-space-md p-space-md" aria-label="Sources">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-space-sm text-body-lg font-semibold text-on-surface"><Icon name="hub" size={20} className="text-outline" />Sources</h2>
        <span className="font-mono text-mono-sm text-outline">Switches apply from the next search</span>
      </div>
      <table className="w-full text-left text-body">
        <thead>
          <tr className="bg-surface-container-lowest/50 text-caption tracking-wider text-outline uppercase">
            <th className="rounded-l px-space-md py-2.5 font-normal">Source</th>
            <th className="px-space-md py-2.5 font-normal">Status</th>
            <th className="px-space-md py-2.5 font-normal">Last result</th>
            <th className="px-space-md py-2.5 font-normal">Note</th>
            <th className="rounded-r px-space-md py-2.5 text-right font-normal">On</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => {
            const status = SOURCE_STATUS[s.enabled ? s.status : "DISABLED"] ?? SOURCE_STATUS.UNKNOWN;
            return (
              <tr key={s.id} data-testid="source-row" className="hover:bg-surface-container-high">
                <td className="px-space-md py-2.5 font-semibold text-on-surface">{sourceName(s.id)}</td>
                <td className="px-space-md py-2.5"><Pill tone={status.tone} icon={status.icon}>{status.label}</Pill></td>
                <td className="px-space-md py-2.5 font-mono text-mono-sm text-on-surface-variant">
                  {s.last_run_at ? `${s.last_count ?? 0} found · ${clock(s.last_run_at)}` : "–"}
                </td>
                <td className="max-w-[340px] truncate px-space-md py-2.5 text-caption text-outline" title={sourceNote(s)}>{sourceNote(s)}</td>
                <td className="px-space-md py-2.5 text-right">
                  {s.switch && switches ? (
                    <button role="switch" aria-checked={switches[s.switch] ?? s.enabled} className="switch"
                            aria-label={`${sourceName(s.id)} on`} onClick={() => onToggle(s, !(switches[s.switch!] ?? s.enabled))} />
                  ) : <span className="text-caption text-outline" title="On when its key is set">{s.enabled ? "Always" : "Needs a key"}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function AlertsLog({ log }: { log: AlertLogEntry[] }) {
  return (
    <section className="pane flex flex-col gap-space-md p-space-md" aria-label="Notifications sent">
      <h2 className="flex items-center gap-space-sm text-body-lg font-semibold text-on-surface"><Icon name="notifications" size={20} className="text-outline" />Notifications sent</h2>
      {log.length === 0 ? <p className="text-body text-on-surface-variant">None yet.</p> : (
        <ol className="flex flex-col">
          {log.map((entry, i) => (
            <li key={`${entry.hash}-${entry.sent_at}-${i}`} className="flex items-center gap-space-md border-l-2 border-outline-variant py-1.5 pl-space-md">
              <span className="w-24 shrink-0 font-mono text-mono-sm text-outline">{when(entry.sent_at)}</span>
              <a className="min-w-0 flex-1 truncate text-body text-on-surface hover:underline" href={href({ screen: "deals", dealId: entry.hash })}>
                {entry.route ?? "Deal"}{entry.total_cost !== null ? ` · ${euros(entry.total_cost)}` : ""}
              </a>
              {entry.alert_tier === "instant" ? <Pill tone="ok">Instant</Pill> : <Pill tone="info">Digest</Pill>}
              <span className="flex gap-1">
                {entry.channel.split(",").filter(Boolean).map((c) => (
                  <span key={c} className="flex items-center gap-1 rounded bg-surface-container-highest px-1.5 py-0.5 text-caption text-on-surface-variant">
                    <Icon name={c === "telegram" ? "send" : "desktop_windows"} size={12} />{c === "telegram" ? "Telegram" : "Desktop"}
                  </span>
                ))}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

const CYCLE_STATUS: Record<Cycle["status"], [("ok" | "bad" | "info" | "off"), string]> = {
  ok: ["ok", "Done"], failed: ["bad", "Failed"], running: ["info", "Running"], stopped: ["off", "Stopped"],
};
const TRIGGERS: Record<string, string> = { scheduled: "Scheduled", manual: "Search now", startup: "At start", telegram: "Telegram" };

function SearchHistory({ searches }: { searches: Cycle[] }) {
  return (
    <section className="pane flex flex-col gap-space-md p-space-md" aria-label="Search history">
      <h2 className="flex items-center gap-space-sm text-body-lg font-semibold text-on-surface"><Icon name="history" size={20} className="text-outline" />Search history</h2>
      <table className="w-full text-left text-body">
        <thead>
          <tr className="bg-surface-container-lowest/50 text-caption tracking-wider text-outline uppercase">
            <th className="rounded-l px-space-md py-2.5 font-normal">Started</th>
            <th className="px-space-md py-2.5 font-normal">Why</th>
            <th className="px-space-md py-2.5 text-right font-normal">Took</th>
            <th className="px-space-md py-2.5 text-right font-normal">Fares</th>
            <th className="px-space-md py-2.5 text-right font-normal">Unusually cheap</th>
            <th className="px-space-md py-2.5 text-right font-normal">Other deals</th>
            <th className="px-space-md py-2.5 text-right font-normal">Sent</th>
            <th className="rounded-r px-space-md py-2.5 font-normal">Result</th>
          </tr>
        </thead>
        <tbody>
          {searches.map((c) => {
            const [tone, label] = CYCLE_STATUS[c.status] ?? CYCLE_STATUS.ok;
            return (
              <tr key={c.id} className="hover:bg-surface-container-high">
                <td className="px-space-md py-2 font-mono text-mono-sm text-on-surface">{when(c.started_at)}</td>
                <td className="px-space-md py-2 text-caption text-on-surface-variant">{TRIGGERS[c.trigger ?? ""] ?? c.trigger ?? "–"}</td>
                <td className="px-space-md py-2 text-right font-mono text-mono-sm text-outline">{c.duration_s !== null ? `${Math.round(c.duration_s)} s` : "–"}</td>
                <td className="px-space-md py-2 text-right font-mono text-mono-sm text-on-surface">{c.fares ?? "–"}</td>
                <td className="px-space-md py-2 text-right font-mono text-mono-sm font-semibold text-secondary">{c.new_instant ?? "–"}</td>
                <td className="px-space-md py-2 text-right font-mono text-mono-sm text-on-surface-variant">{c.new_digest ?? "–"}</td>
                <td className="px-space-md py-2 text-right font-mono text-mono-sm text-on-surface-variant">{c.sent ?? "–"}</td>
                <td className="px-space-md py-2" title={c.error ?? undefined}><Pill tone={tone}>{label}</Pill></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {searches.length === 0 && <p className="text-body text-on-surface-variant">No searches yet.</p>}
    </section>
  );
}

export function Activity() {
  const activity = useLoad(api.activity, []);
  const settings = useLoad(api.settings, []);
  const toast = useToast();
  useReloadOn(["cycle_finished", "source_done", "alert_sent", "digest_sent", "settings_changed"], activity.reload);
  const a = activity.data;

  const toggle = (s: Source, on: boolean) => {
    if (!s.switch) return;
    api.saveSettings({ sources: { [s.switch]: on } })
      .then(() => { activity.reload(); settings.reload(); toast(`${sourceName(s.id)} ${on ? "on" : "off"} from the next search`); })
      .catch((err: Error) => toast(err.message, { tone: "error" }));
  };
  const openLogs = () => api.open("logs").then((r) => toast(`Opened ${r.opened}`)).catch((err: Error) => toast(err.message, { tone: "error" }));

  return (
    <div className="flex flex-col gap-space-lg">
      <PageHeader label="Engine · Activity" title="Activity" actions={
        <button className="btn-secondary" onClick={openLogs}><Icon name="folder_open" size={16} />Open log folder</button>
      }>
        What CheapTrip searched, what each source found, and which notifications went out.
      </PageHeader>
      <div className="grid grid-cols-1 gap-gutter xl:grid-cols-3">
        <EngineCard />
        {a ? <AlertsCard limit={a.alerts.per_hour_limit} sentLastHour={a.alerts.sent_last_hour} waiting={a.alerts.waiting} />
          : <div className="skeleton h-44" />}
      </div>
      {activity.error && <p className="text-body text-error">{activity.error}</p>}
      {a && (
        <>
          <SourcesTable sources={a.sources} switches={settings.data?.sources} onToggle={toggle} />
          <div className="grid grid-cols-1 gap-gutter xl:grid-cols-5">
            <div className="xl:col-span-2"><AlertsLog log={a.alerts.log} /></div>
            <div className="xl:col-span-3"><SearchHistory searches={a.searches} /></div>
          </div>
          <p className="text-caption text-outline">
            Last updated {ago(a.searches[0]?.finished_at ?? a.searches[0]?.started_at ?? null)} · Sources' problems also
            appear in the top bar and, if you want, as notifications.
          </p>
        </>
      )}
    </div>
  );
}
