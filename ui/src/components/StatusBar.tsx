import { useState } from "react";
import { api, type PauseChoice, type Status } from "../api";
import { useEngineEvents, useReloadOn } from "../events";
import { when } from "../format";
import { useLoad } from "../load";

const LABELS: Record<Status["state"], string> = {
  running: "Watching prices",
  searching: "Searching…",
  paused: "Alerts paused",
  attention: "Needs attention",
  stopped: "Stopped",
};

/** The top bar: what the engine is doing, and Search now / Pause / Resume. */
export function StatusBar() {
  const status = useLoad(api.status, []);
  const [progress, setProgress] = useState<{ planned: string[]; done: string[] }>();
  const [busy, setBusy] = useState(false);
  useReloadOn(["cycle_started", "cycle_finished", "paused", "resumed", "engine_started", "engine_stopped",
               "settings_changed"], status.reload);
  useEngineEvents((event) => {  // "Searching… 2 of 4 sources"
    if (event.type === "cycle_planned") setProgress({ planned: event.sources as string[], done: [] });
    if (event.type === "source_done") {
      const id = event.source as string;
      setProgress((p) => (p && p.planned.includes(id) && !p.done.includes(id) ? { ...p, done: [...p.done, id] } : p));
    }
    if (event.type === "cycle_finished") setProgress(undefined);
  });

  const s = status.data;
  const act = (call: () => Promise<unknown>) => {
    setBusy(true);
    call().finally(() => { setBusy(false); status.reload(); });
  };
  const pause = (choice: PauseChoice) => act(() => api.pause(choice));

  if (!s) return <header className="status-bar">{status.error ? `Can't reach the engine: ${status.error}` : "…"}</header>;
  const detail =
    s.state === "paused" && s.paused_until ? `until ${when(s.paused_until)}`
      : s.state === "searching" && progress?.planned.length
        ? `${progress.done.length} of ${progress.planned.length} sources`
        : s.next_search_at ? `next search ${when(s.next_search_at)}` : "";

  return (
    <header className="status-bar">
      <span className={`pill pill-${s.state}`} data-testid="state">{LABELS[s.state]}</span>
      <span className="muted">{detail}</span>
      <span className="muted">{s.searches_today} searches today</span>
      {s.learning && s.learning.trips ? (
        <span className="muted" title="Trips found in the last search that already have a usual price to compare with">
          usual price known for {s.learning.with_usual_price ?? 0} of {s.learning.trips} trips
        </span>
      ) : null}
      <span className="spacer" />
      <button disabled={busy || s.searching || s.state === "stopped"} onClick={() => act(api.searchNow)}>Search now</button>
      {s.state === "paused" ? (
        <button disabled={busy} onClick={() => act(api.resume)}>Resume alerts</button>
      ) : (
        <select value="" disabled={busy} aria-label="Pause alerts"
                onChange={(e) => e.target.value && pause(JSON.parse(e.target.value) as PauseChoice)}>
          <option value="">Pause alerts…</option>
          <option value='{"hours":1}'>For 1 hour</option>
          <option value='{"hours":4}'>For 4 hours</option>
          <option value='{"until":"tomorrow"}'>Until tomorrow morning</option>
          <option value='{"until":"resume"}'>Until I resume</option>
        </select>
      )}
      {s.attention.length > 0 && (
        <div className="attention" role="alert">{s.attention.join(" · ")}</div>
      )}
    </header>
  );
}
