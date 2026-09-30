/**
 * The top bar (always visible): the engine status pill (click: last and next
 * search, or each source's progress while searching), the destination quick
 * jump, Search now, Pause alerts / Resume and the light/dark switch.
 */
import { useEffect, useRef, useState } from "react";
import { api, type Airport, type EngineState as State, type Status } from "../api";
import { progressLabel, useEngine, type Progress } from "../engine";
import { clock, fromNow, pausedUntil } from "../format";
import { href, navigate } from "../router";
import { attentionText, sourceName } from "../sources";
import { useTheme } from "../theme";
import { Icon } from "./Icon";
import { Logo } from "./Logo";
import { MenuHeading, MenuItem, Popover } from "./Popover";

const DOT: Record<State, string> = {
  running: "bg-secondary",
  searching: "bg-primary-container",
  paused: "bg-tertiary-container",
  attention: "bg-error",
  stopped: "bg-outline",
};

/** The state's coloured dot (pulsing while the engine works); the state is always in words next to it too. */
export function StateDot({ state }: { state: State }) {
  const live = state === "running" || state === "searching";
  return (
    <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
      {live && <span className={`animate-ping-dot absolute inline-flex h-full w-full rounded-full opacity-75 ${DOT[state]}`} />}
      <span className={`relative inline-flex h-2 w-2 rounded-full ${DOT[state]}`} />
    </span>
  );
}

export function statusText(status: Status, progress: Progress | undefined, now = new Date()): string {
  switch (status.state) {
    case "stopped":
      return "Stopped";
    case "searching":
      return `Searching… ${progressLabel(progress)}`.trim();
    case "paused":
      return `Alerts paused ${pausedUntil(status.paused_until, now)}`;
    case "attention":
      return "Needs attention";
    default:
      return status.next_search_at ? `Running · next search ${fromNow(status.next_search_at, now)}` : "Running";
  }
}

function useNow(everyMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs]);
  return now;
}

function StatusPill() {
  const { status, progress, error, resume } = useEngine();
  const now = useNow();
  if (!status) {
    return (
      <span className="flex h-10 items-center gap-space-sm rounded-full bg-surface-container px-space-lg font-mono text-mono-sm text-on-surface-variant">
        <StateDot state="stopped" />{error ? "Can't reach the engine" : "Connecting…"}
      </span>
    );
  }
  const last = status.last_cycle;
  return (
    <Popover className="w-80" button={(open, toggle) => (
      <button onClick={toggle} aria-expanded={open} aria-haspopup="menu" data-testid="state"
              className="flex h-10 items-center gap-space-sm rounded-full border border-stroke bg-surface-container px-space-lg font-mono text-mono-sm text-on-surface transition-colors hover:bg-surface-container-high">
        <StateDot state={status.state} />
        <span className="max-w-[16rem] truncate">{statusText(status, progress, now)}</span>
        <Icon name="expand_more" size={16} className="text-on-surface-variant" />
      </button>
    )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          <MenuHeading>Engine status</MenuHeading>
          {status.state === "searching" && progress?.planned.length ? (
            progress.planned.map((id) => {
              const done = progress.done[id];
              const failed = done && done.status === "failed";
              return (
                <div key={id} className="flex items-center gap-space-sm rounded-lg px-space-md py-1.5 font-mono text-mono-sm">
                  <Icon name={!done ? "progress_activity" : failed ? "error" : "check_circle"} size={16}
                        className={!done ? "animate-spin text-primary" : failed ? "text-error" : "text-secondary"} />
                  <span className="flex-1 text-on-surface">{sourceName(id)}</span>
                  <span className="text-on-surface-variant">
                    {!done ? "searching" : failed ? "failed" : `${done.count} found`}
                  </span>
                </div>
              );
            })
          ) : (
            <div className="flex flex-col gap-1 px-space-md py-1.5 font-mono text-mono-sm text-on-surface-variant">
              <span>
                Last search: {last ? `${clock(last.finished_at ?? last.started_at)} · ${last.fares ?? 0} fares` : "none yet"}
              </span>
              {status.next_search_at && (
                <span>Next search: {clock(status.next_search_at)} ({fromNow(status.next_search_at, now)})</span>
              )}
              <span>Searches today: {status.searches_today}</span>
            </div>
          )}
          {status.state === "paused" && (
            <MenuItem onSelect={() => { resume(); close(); }} className="text-tertiary">
              <Icon name="play_arrow" size={16} /> Resume alerts ({pausedUntil(status.paused_until, now)})
            </MenuItem>
          )}
          {status.attention.map((item) => (
            <div key={item} className="flex items-start gap-space-sm rounded-lg px-space-md py-1.5 text-body text-error">
              <Icon name="warning" size={16} /> <span>{attentionText(item)}</span>
            </div>
          ))}
          <a href={href({ screen: "activity" })} onClick={close}
             className="mt-1 flex items-center justify-between rounded-lg px-space-md py-1.5 text-body text-primary hover:bg-surface-container-highest">
            Open Activity <Icon name="chevron_right" size={16} />
          </a>
        </div>
      )}
    </Popover>
  );
}

/** Jump to a destination by city or airport code: opens it on the Destinations screen. */
function QuickJump() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Airport[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setResults([]); return; }
    const timer = window.setTimeout(() => {
      api.airports(q, 6).then((found) => { setResults(found); setActive(0); }).catch(() => setResults([]));
    }, 150);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {  // Ctrl+K jumps here from anywhere
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  const go = (airport: Airport) => {
    navigate({ screen: "destinations", param: airport.code });
    setQuery("");
    setOpen(false);
    input.current?.blur();
  };

  return (
    <div className="relative mx-space-md max-w-md flex-1">
      <Icon name="travel_explore" size={18} className="pointer-events-none absolute top-[7px] left-space-md text-outline" />
      <input ref={input} value={query} role="combobox" aria-expanded={open && results.length > 0}
             aria-label="Jump to a destination" placeholder="Jump to destination or airport code… (e.g. LHR, Tokyo)"
             className="field pl-10"
             onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
             onFocus={() => setOpen(true)}
             onBlur={() => window.setTimeout(() => setOpen(false), 150)}
             onKeyDown={(e) => {
               if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, results.length - 1)); }
               if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
               if (e.key === "Enter" && results[active]) go(results[active]);
               if (e.key === "Escape") { setQuery(""); input.current?.blur(); }
             }} />
      {open && results.length > 0 && (
        <div role="listbox" className="flyout absolute right-0 left-0 z-50 mt-space-xs p-space-xs">
          {results.map((airport, i) => (
            <button key={airport.code} role="option" aria-selected={i === active}
                    onMouseDown={(e) => e.preventDefault()} onClick={() => go(airport)}
                    className={`flex w-full items-center gap-space-md rounded-lg px-space-md py-1.5 text-left ${i === active ? "bg-surface-container-highest" : ""}`}>
              <span className="w-9 font-mono text-mono font-semibold text-primary">{airport.code}</span>
              <span className="flex-1 truncate text-body text-on-surface">{airport.city}</span>
              <span className="truncate text-caption text-outline">{airport.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function EngineControls() {
  const { status, busy, searchNow, pause, resume } = useEngine();
  const searching = status?.searching ?? false;
  const canSearch = !!status && !busy && !searching && status.state !== "stopped";
  return (
    <div className="flex items-center gap-space-xs">
      <button className="btn-primary" disabled={!canSearch} onClick={searchNow}
              title={searching ? "A search is running" : "Search all sources now"}>
        <Icon name={searching ? "progress_activity" : "radar"} size={18} className={searching ? "animate-spin" : ""} />
        {searching ? "Searching…" : "Search now"}
      </button>
      {status?.state === "paused" ? (
        <button className="btn-secondary" disabled={busy} onClick={resume} title="Resume alerts">
          <Icon name="play_arrow" size={18} /> Resume
        </button>
      ) : (
        <Popover align="right" className="w-52" button={(open, toggle) => (
          <button className="btn-secondary px-space-sm" onClick={toggle} disabled={!status || busy}
                  aria-expanded={open} aria-haspopup="menu" aria-label="Pause alerts" title="Pause alerts">
            <Icon name="pause" size={18} /><Icon name="expand_more" size={16} />
          </button>
        )}>
          {(close) => (
            <>
              <MenuHeading>Pause alerts for</MenuHeading>
              {([["1 hour", { hours: 1 }], ["4 hours", { hours: 4 }], ["Until tomorrow morning", { until: "tomorrow" }],
                 ["Until I resume", { until: "resume" }]] as const).map(([label, choice]) => (
                <MenuItem key={label} onSelect={() => { pause(choice); close(); }}>{label}</MenuItem>
              ))}
            </>
          )}
        </Popover>
      )}
    </div>
  );
}

function ThemeToggle() {
  const { theme, choose } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button className="btn-icon" onClick={() => choose(next)} aria-label={`Switch to the ${next} theme`}
            title={`Switch to the ${next} theme`}>
      <Icon name={theme === "dark" ? "light_mode" : "dark_mode"} size={18} />
    </button>
  );
}

export function TopBar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  return (
    <header className="fixed top-0 right-0 left-0 z-40 flex h-16 items-center justify-between gap-space-lg border-b border-stroke bg-surface-container-low px-space-lg">
      <div className="flex min-w-0 items-center gap-space-md">
        <button className="btn-icon" onClick={onToggleSidebar} aria-label="Show or hide the navigation">
          <Icon name="menu" size={22} />
        </button>
        <a href={href({ screen: "deals" })} className="flex items-center gap-space-sm" aria-label="CheapTrip: Deals">
          <Logo size={34} />
          <span className="text-headline-sm tracking-tight text-on-surface">CheapTrip</span>
          <span className="rounded bg-surface-container-highest px-space-xs py-0.5 font-mono text-mono-sm tracking-wider text-primary uppercase">Radar</span>
        </a>
        <StatusPill />
      </div>
      <QuickJump />
      <div className="flex items-center gap-space-xs">
        <EngineControls />
        <div className="mx-space-xs h-4 w-px bg-surface-container-highest" />
        <ThemeToggle />
      </div>
    </header>
  );
}
