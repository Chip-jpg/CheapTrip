/**
 * Destinations, as designed: tiles, the Priority / Muted / All seen tabs, the
 * priority destinations as cards with their 30-day trend, every destination
 * seen in a table, and "Add a destination" (autocomplete, priority or mute).
 * #/destinations/<airport> (the top bar's quick jump) opens one destination.
 */
import { useEffect, useMemo, useState } from "react";
import { api, type Airport, type Destination, type DestinationTab } from "../api";
import { Icon } from "../components/Icon";
import { MenuItem, Popover } from "../components/Popover";
import { PageHeader, Pill, StatTile, Tabs, TrendLine } from "../components/Page";
import { useToast } from "../components/Toast";
import { useEngine } from "../engine";
import { useReloadOn } from "../events";
import { ago, countryName, euros, pct, shortDay } from "../format";
import { useLoad } from "../load";
import { navigate } from "../router";

type Rule = "priority" | "muted" | "standard";

function ruleOf(d: Destination): Rule {
  return d.is_priority ? "priority" : d.is_muted ? "muted" : "standard";
}

function discount(d: Destination): number | null {
  return d.best_price !== null && d.usual_price ? ((d.usual_price - d.best_price) / d.usual_price) * 100 : null;
}

function matchesQuery(d: Destination, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || d.code.toLowerCase().includes(q) || d.city.toLowerCase().includes(q) || countryName(d.country).toLowerCase().includes(q);
}

/** Priority, mute and "no rule" for one destination, with a confirmation and Undo. */
function useRules(reload: () => void) {
  const toast = useToast();
  const apply = async (d: Pick<Destination, "code" | "city" | "is_priority" | "is_muted">, rule: Rule) => {
    const before: Rule = d.is_priority ? "priority" : d.is_muted ? "muted" : "standard";
    const set = async (target: Rule) => {
      if (target !== "priority" && d.is_priority) await api.setPriority(d.code, false);
      if (target !== "muted" && d.is_muted) await api.setMuted(d.code, false);
      if (target === "priority") await api.setPriority(d.code, true);
      if (target === "muted") await api.setMuted(d.code, true);
    };
    try {
      await set(rule);
      reload();
      const text = rule === "priority" ? `${d.city} always alerts now` : rule === "muted" ? `${d.city} muted: no more alerts for it`
        : `${d.city} follows the usual rules again`;
      toast(text, { action: { label: "Undo", run: () => { void api.setPriority(d.code, before === "priority").then(() =>
        api.setMuted(d.code, before === "muted")).then(reload); } } });
    } catch (err) {
      toast((err as Error).message, { tone: "error" });
    }
  };
  return apply;
}

function RuleMenu({ d, onRule }: { d: Destination; onRule: (d: Destination, rule: Rule) => void }) {
  const rule = ruleOf(d);
  return (
    <Popover align="right" className="w-56" button={(open, toggle) => (
      <button className="btn-icon" onClick={toggle} aria-expanded={open} aria-haspopup="menu" aria-label={`Rules for ${d.city}`}>
        <Icon name="more_vert" size={18} />
      </button>
    )}>
      {(close) => (
        <>
          {rule !== "priority" && <MenuItem onSelect={() => { onRule(d, "priority"); close(); }}><Icon name="star" size={16} />Always notify (priority)</MenuItem>}
          {rule !== "muted" && <MenuItem onSelect={() => { onRule(d, "muted"); close(); }}><Icon name="notifications_off" size={16} />Never notify (mute)</MenuItem>}
          {rule !== "standard" && <MenuItem onSelect={() => { onRule(d, "standard"); close(); }}><Icon name="restart_alt" size={16} />Back to the usual rules</MenuItem>}
        </>
      )}
    </Popover>
  );
}

function Monogram({ code }: { code: string }) {
  return (
    <div className="flex h-10 w-12 shrink-0 items-center justify-center rounded bg-surface-container-highest font-mono text-mono font-bold text-primary">
      {code}
    </div>
  );
}

function DestinationCard({ d, onRule }: { d: Destination; onRule: (d: Destination, rule: Rule) => void }) {
  const off = discount(d);
  const [origin] = (d.best_route ?? "").split("-");
  return (
    <article className="card flex flex-col gap-space-md p-space-md" data-testid="destination-card">
      <div className="flex items-start justify-between gap-space-sm">
        <div className="flex min-w-0 items-center gap-space-md">
          <Monogram code={d.code} />
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-headline-sm text-on-surface">{d.city}</span>
            <span className="truncate text-caption text-on-surface-variant">{countryName(d.country) || "\u00a0"}</span>
          </div>
        </div>
        <RuleMenu d={d} onRule={onRule} />
      </div>
      <div className="well flex items-end justify-between gap-space-md p-space-sm">
        {d.best_price !== null ? (
          <div className="flex flex-col">
            <span className="flex items-baseline gap-space-xs">
              <span className="font-mono text-mono-lg text-secondary">{euros(d.best_price)}</span>
              {off !== null && off > 0 && <span className="delta bg-secondary-container/20 text-secondary">-{pct(off)}</span>}
            </span>
            <span className="text-caption text-outline">{d.usual_price ? `Usually ${euros(d.usual_price)}` : "Best fare in 30 days"}</span>
          </div>
        ) : (
          <span className="text-body text-on-surface-variant">No fares seen in the last 30 days</span>
        )}
        <div className="flex flex-col items-end gap-0.5">
          <TrendLine points={d.trend} />
          <span className="font-mono text-mono-sm text-outline">30-day trend</span>
        </div>
      </div>
      {d.best_depart && (
        <div className="flex items-center justify-between font-mono text-mono-sm text-on-surface-variant">
          <span className="flex items-center gap-1.5"><Icon name="flight_takeoff" size={16} />
            {shortDay(d.best_depart)}{d.best_return ? ` → ${shortDay(d.best_return)}` : ""}{origin ? ` · from ${origin}` : ""}</span>
          <span className="text-outline">{d.fares} {d.fares === 1 ? "fare" : "fares"}</span>
        </div>
      )}
      <div className="flex items-center justify-between">
        {d.is_priority ? <Pill tone="ok" icon="notifications_active">Always notify</Pill>
          : d.is_muted ? <Pill tone="warn" icon="notifications_off">Never notify</Pill> : <Pill tone="off">Usual rules</Pill>}
        <span className="text-caption text-outline">{d.last_seen ? `Seen ${ago(d.last_seen)}` : ""}</span>
      </div>
    </article>
  );
}

/** "Add a destination": find an airport or city, then always notify or never notify. */
function AddDialog({ onClose, onAdd, initial }: {
  onClose: () => void; onAdd: (airport: Airport, rule: "priority" | "muted") => void; initial: "priority" | "muted";
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Airport[]>([]);
  const [chosen, setChosen] = useState<Airport>();
  const [rule, setRule] = useState(initial);
  useEffect(() => {
    if (chosen || query.trim().length < 2) { setResults([]); return; }
    const timer = window.setTimeout(() => api.airports(query.trim(), 6).then(setResults).catch(() => setResults([])), 150);
    return () => window.clearTimeout(timer);
  }, [query, chosen]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim backdrop-blur-sm" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Add a destination" onMouseDown={(e) => e.stopPropagation()}
           className="flyout flex w-[30rem] max-w-[calc(100vw-2rem)] flex-col gap-space-lg p-space-lg">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-space-md">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary"><Icon name="add_location" size={22} /></div>
            <div>
              <h2 className="text-headline-sm text-on-surface">Add a destination</h2>
              <p className="text-caption text-on-surface-variant">An airport or a city (its airports are searched together).</p>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close"><Icon name="close" size={20} /></button>
        </div>
        <label className="flex flex-col gap-space-xs">
          <span className="label-caps">Airport or city</span>
          <span className="relative">
            <Icon name="flight" size={18} className="pointer-events-none absolute top-[7px] left-space-md text-outline" />
            <input autoFocus className="field pl-10" placeholder="e.g. Reykjavik or KEF" role="combobox" aria-expanded={results.length > 0}
                   value={chosen ? `${chosen.city} (${chosen.code})` : query}
                   onChange={(e) => { setChosen(undefined); setQuery(e.target.value); }} />
          </span>
        </label>
        {results.length > 0 && (
          <div role="listbox" className="-mt-space-md flex flex-col rounded-lg border border-stroke bg-surface-container p-space-xs">
            {results.map((a) => (
              <button key={a.code} role="option" aria-selected={false} onClick={() => setChosen(a)}
                      className="flex items-center gap-space-md rounded px-space-md py-1.5 text-left hover:bg-surface-container-highest">
                <span className="w-9 font-mono text-mono font-semibold text-primary">{a.code}</span>
                <span className="flex-1 text-body text-on-surface">{a.city}</span>
                <span className="truncate text-caption text-outline">{a.name}</span>
              </button>
            ))}
          </div>
        )}
        <div className="flex flex-col gap-space-sm" role="radiogroup" aria-label="Rule">
          {([["priority", "Always notify", "Every fare found for it alerts, whatever the price.", "notifications_active"],
             ["muted", "Never notify", "No alerts for it, however cheap.", "notifications_off"]] as const).map(([value, label, help, icon]) => (
            <button key={value} role="radio" aria-checked={rule === value} onClick={() => setRule(value)}
                    className={`flex items-start gap-space-md rounded-lg border p-space-md text-left ${rule === value
                      ? "border-primary-container bg-primary-container/10" : "border-stroke hover:bg-surface-container-highest"}`}>
              <Icon name={icon} size={20} className={rule === value ? "text-primary" : "text-outline"} />
              <span className="flex flex-col">
                <span className="text-body font-semibold text-on-surface">{label}</span>
                <span className="text-caption text-on-surface-variant">{help}</span>
              </span>
            </button>
          ))}
        </div>
        <div className="flex justify-end gap-space-sm">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!chosen} onClick={() => chosen && onAdd(chosen, rule)}>
            {rule === "priority" ? "Always notify" : "Mute"} {chosen ? chosen.city : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

/** A destination the quick jump asked for that hasn't been seen lately: its rules, at least. */
function NotSeen({ code, onRule }: { code: string; onRule: (d: Destination, rule: Rule) => void }) {
  const airport = useLoad(() => api.airports(code, 1), [code]);
  const a = airport.data?.find((x) => x.code === code);
  if (!a) return null;
  const d: Destination = { code, city: a.city, country: a.country, best_price: null, best_depart: null, best_return: null,
    best_route: null, usual_price: null, trend: [], fares: 0, last_seen: null, is_priority: false, is_muted: false };
  return (
    <div className="pane flex items-center justify-between gap-space-md p-space-md">
      <div className="flex items-center gap-space-md">
        <Monogram code={code} />
        <div className="flex flex-col">
          <span className="text-body-lg font-semibold text-on-surface">{a.city} ({code})</span>
          <span className="text-body text-on-surface-variant">No fares to it in the last 30 days. You can still make it a priority, so any fare alerts.</span>
        </div>
      </div>
      <div className="flex gap-space-sm">
        <button className="btn-primary" onClick={() => onRule(d, "priority")}><Icon name="star" size={16} />Always notify</button>
        <button className="btn-secondary" onClick={() => onRule(d, "muted")}><Icon name="notifications_off" size={16} />Mute</button>
      </div>
    </div>
  );
}

export function Destinations({ focus }: { focus?: string }) {
  const [tab, setTab] = useState<DestinationTab>(focus ? "seen" : "priority");
  const [query, setQuery] = useState(focus ?? "");
  const [adding, setAdding] = useState<"priority" | "muted">();
  const seen = useLoad(() => api.destinations("seen"), []);
  const priority = useLoad(() => api.destinations("priority"), []);
  const muted = useLoad(() => api.destinations("muted"), []);
  const { status } = useEngine();
  const reload = () => { seen.reload(); priority.reload(); muted.reload(); };
  useReloadOn(["cycle_finished", "preferences_changed", "settings_changed"], reload);
  const rule = useRules(reload);

  useEffect(() => {
    if (focus) { setTab("seen"); setQuery(focus); }
  }, [focus]);

  const all = seen.data?.destinations ?? [];
  const pri = priority.data?.destinations ?? [];
  const mut = muted.data?.destinations ?? [];
  const cheapest = useMemo(() => all.filter((d) => d.best_price !== null).sort((a, b) => a.best_price! - b.best_price!)[0], [all]);
  const shown = (tab === "priority" ? pri : tab === "muted" ? mut : all).filter((d) => matchesQuery(d, query));
  const last = status?.last_cycle;
  const focusMissing = focus && seen.data && !all.some((d) => d.code === focus) && !pri.some((d) => d.code === focus);

  return (
    <div className="flex flex-col gap-space-lg">
      <PageHeader label="Radar · Destinations" title="Destinations" count={`${all.length} seen`} actions={
        <>
          <span className="relative w-64">
            <Icon name="search" size={18} className="pointer-events-none absolute top-[7px] left-space-md text-outline" />
            <input className="field pl-10" placeholder="Filter destinations or IATA…" aria-label="Filter destinations"
                   value={query} onChange={(e) => setQuery(e.target.value)} />
          </span>
          <button className="btn-primary" onClick={() => setAdding("priority")}><Icon name="add" size={18} />Add destination</button>
        </>
      }>
        Choose which destinations always alert and which never do, and see what every destination found in the last 30 days costs.
      </PageHeader>

      <section className="grid grid-cols-1 gap-gutter @2xl:grid-cols-2 @6xl:grid-cols-4" aria-label="Summary">
        <StatTile label="Priority" status="Always notify" value={`${pri.length} ${pri.length === 1 ? "place" : "places"}`}
                  detail="Alert at any price" bar={all.length ? pri.length / Math.max(all.length, 1) : 0} />
        <StatTile label="Muted" status="Never notify" statusTone="text-tertiary" barTone="bg-tertiary-container"
                  value={`${mut.length} ${mut.length === 1 ? "place" : "places"}`}
                  detail={mut.slice(0, 3).map((d) => d.code).join(", ") || "None"} bar={all.length ? mut.length / Math.max(all.length, 1) : 0} />
        <StatTile label="Lowest fare seen" status={cheapest && discount(cheapest) ? `-${pct(discount(cheapest))}` : undefined}
                  barTone="bg-primary-container"
                  value={cheapest ? <>{euros(cheapest.best_price)} <span className="text-primary">{cheapest.code}</span></> : "–"}
                  detail={cheapest ? `${cheapest.city}${cheapest.best_depart ? ` · ${shortDay(cheapest.best_depart)}` : ""}` : "No fares yet"}
                  bar={cheapest ? 1 : 0} />
        <StatTile label="Last search" status={status?.searching ? "Searching" : "Live"} statusTone="text-primary"
                  barTone="bg-primary-container" value={last ? ago(last.finished_at ?? last.started_at) : "–"}
                  detail={last ? `${last.fares ?? 0} fares checked` : "No search yet"}
                  bar={last && status?.next_search_at ? 1 : 0} />
      </section>

      <div className="flex flex-wrap items-center justify-between gap-space-md">
        <Tabs label="Destination lists" value={tab} onChange={setTab} tabs={[
          { value: "priority", label: "Priority", icon: "star", count: pri.length },
          { value: "muted", label: "Muted", icon: "notifications_off", count: mut.length },
          { value: "seen", label: "All seen", icon: "public", count: all.length },
        ]} />
        <div className="flex items-center gap-space-md font-mono text-mono-sm text-on-surface-variant">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-secondary" />Always notify ({pri.length})</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-tertiary-container" />Never notify ({mut.length})</span>
        </div>
      </div>

      {focusMissing && <NotSeen code={focus!} onRule={rule} />}

      {tab !== "seen" ? (
        <section className="flex flex-col gap-space-md">
          <div className="pane flex items-center justify-between gap-space-md p-space-md">
            <div className="flex items-center gap-space-md">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-container-highest text-primary">
                <Icon name={tab === "priority" ? "bolt" : "notifications_off"} size={20} />
              </div>
              <div className="flex flex-col">
                <span className="text-body font-semibold text-on-surface">{tab === "priority" ? "Always notify" : "Never notify"}</span>
                <span className="text-caption text-on-surface-variant">
                  {tab === "priority"
                    ? "Every fare found for these destinations alerts at once, whatever its price (within your hourly limit)."
                    : "No alerts for these destinations, however cheap. Their fares still count towards usual prices."}
                </span>
              </div>
            </div>
            <span className="font-mono text-mono-sm text-outline">Also from Telegram: /{tab === "priority" ? "priority" : "mute"} CODE</span>
          </div>
          <div className="grid grid-cols-1 gap-gutter @3xl:grid-cols-2 @5xl:grid-cols-3">
            {shown.map((d) => <DestinationCard key={d.code} d={d} onRule={rule} />)}
            <button onClick={() => setAdding(tab === "priority" ? "priority" : "muted")}
                    className="pane flex min-h-[220px] flex-col items-center justify-center gap-space-sm border-dashed p-space-lg text-center transition-colors hover:bg-surface-container-high"
                    style={{ backgroundImage: "radial-gradient(var(--c-stroke) 1px, transparent 1px)", backgroundSize: "12px 12px" }}>
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-container-highest text-primary">
                <Icon name="add_location_alt" size={24} />
              </div>
              <span className="text-headline-sm text-on-surface">{tab === "priority" ? "Track another destination" : "Mute another destination"}</span>
              <span className="max-w-xs text-body text-on-surface-variant">
                {tab === "priority" ? "Get every fare to it, whatever the price." : "Stop alerts for a place you don't want to go."}
              </span>
              <span className="btn-secondary mt-space-xs"><Icon name="add" size={16} />Add airport or city</span>
            </button>
          </div>
        </section>
      ) : (
        <section className="pane flex flex-col gap-space-md p-space-md" aria-label="All seen destinations">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-space-sm">
              <Icon name="table_rows" size={20} className="text-outline" />
              <h2 className="text-body-lg font-semibold text-on-surface">Every destination found in the last 30 days</h2>
            </div>
            <span className="font-mono text-mono-sm text-outline">Cheapest first</span>
          </div>
          <div className="overflow-x-auto">
            <div className="overflow-x-auto"><table className="w-full min-w-[42rem] text-left text-body">
              <thead>
                <tr className="bg-surface-container-lowest/50 text-caption tracking-wider text-outline uppercase">
                  <th className="rounded-l px-space-md py-2.5 font-normal">Destination</th>
                  <th className="px-space-md py-2.5 font-normal">Fares seen</th>
                  <th className="px-space-md py-2.5 text-right font-normal">Best fare</th>
                  <th className="px-space-md py-2.5 text-right font-normal">Usual</th>
                  <th className="px-space-md py-2.5 font-normal">30-day trend</th>
                  <th className="px-space-md py-2.5 font-normal">Rule</th>
                  <th className="rounded-r px-space-md py-2.5 text-right font-normal"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((d) => (
                  <tr key={d.code} data-testid="destination-row"
                      className={`transition-colors hover:bg-surface-container-high ${focus === d.code ? "bg-primary-container/10" : ""}`}>
                    <td className="px-space-md py-2">
                      <div className="flex items-center gap-space-md">
                        <span className="w-10 rounded bg-surface-container-highest py-1 text-center font-mono text-mono font-bold text-primary">{d.code}</span>
                        <div className="flex flex-col">
                          <span className="font-semibold text-on-surface">{d.city}</span>
                          <span className="text-caption text-outline">{countryName(d.country)}{d.best_depart ? ` · ${shortDay(d.best_depart)}` : ""}</span>
                        </div>
                      </div>
                    </td>
                    <td className="px-space-md py-2 font-mono text-mono-sm text-on-surface-variant">{d.fares}</td>
                    <td className="px-space-md py-2 text-right font-mono text-mono font-semibold text-secondary">{euros(d.best_price)}</td>
                    <td className="px-space-md py-2 text-right font-mono text-mono-sm text-outline">{euros(d.usual_price)}</td>
                    <td className="px-space-md py-2"><TrendLine points={d.trend} /></td>
                    <td className="px-space-md py-2">
                      {d.is_priority ? <Pill tone="ok">Priority</Pill> : d.is_muted ? <Pill tone="warn">Muted</Pill> : <Pill tone="off">Usual rules</Pill>}
                    </td>
                    <td className="px-space-md py-2 text-right"><RuleMenu d={d} onRule={rule} /></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
            {shown.length === 0 && (
              <p className="p-space-lg text-center text-body text-on-surface-variant">
                {all.length ? "No destination matches this filter." : "Nothing found yet: destinations appear after the first search."}
              </p>
            )}
          </div>
          <div className="flex items-center justify-between text-caption text-outline">
            <span>Showing {shown.length} of {all.length} destinations</span>
            {query && <button className="text-primary hover:underline" onClick={() => { setQuery(""); navigate({ screen: "destinations" }); }}>Clear the filter</button>}
          </div>
        </section>
      )}

      {adding && (
        <AddDialog initial={adding} onClose={() => setAdding(undefined)} onAdd={(airport, chosen) => {
          setAdding(undefined);
          void rule({ code: airport.code, city: airport.city, is_priority: false, is_muted: false }, chosen);
        }} />
      )}
    </div>
  );
}
