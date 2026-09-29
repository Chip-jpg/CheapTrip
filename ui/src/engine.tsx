/**
 * The engine's status, shared by the top bar, the sidebar and the screens: what
 * it is doing, the running search's progress per source, and the controls
 * (Search now, Pause alerts, Resume).
 */
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type PauseChoice, type Status } from "./api";
import { useEngineEvents, useReloadOn } from "./events";
import { useLoad } from "./load";

export interface SourceProgress {
  status: string; // the source's outcome: ok, empty, failed, …
  count: number;
}

export interface Progress {
  planned: string[];
  done: Record<string, SourceProgress>;
}

export interface EngineState {
  status: Status | undefined;
  error: string | undefined;
  progress: Progress | undefined;
  busy: boolean;
  searchNow: () => void;
  pause: (choice: PauseChoice) => void;
  resume: () => void;
  reload: () => void;
}

const EngineContext = createContext<EngineState | null>(null);

export const STATUS_EVENTS = ["cycle_started", "cycle_finished", "paused", "resumed", "engine_started",
                              "engine_stopped", "settings_changed"];

export function EngineProvider({ children }: { children: ReactNode }) {
  const loaded = useLoad(api.status, []);
  const [progress, setProgress] = useState<Progress>();
  const [busy, setBusy] = useState(false);
  useReloadOn(STATUS_EVENTS, loaded.reload);

  useEngineEvents((event) => {  // "Searching… 2 of 4 sources"
    if (event.type === "cycle_started") setProgress({ planned: [], done: {} });
    if (event.type === "cycle_planned") setProgress((p) => ({ planned: event.sources as string[], done: p?.done ?? {} }));
    if (event.type === "source_done") {
      const id = event.source as string;
      setProgress((p) => p && ({
        ...p, done: { ...p.done, [id]: { status: String(event.status), count: Number(event.count ?? 0) } },
      }));
    }
    if (event.type === "cycle_finished") setProgress(undefined);
  });

  useEffect(() => {  // keeps "next search in 42 min" and a missed event from going stale
    const timer = window.setInterval(loaded.reload, 60_000);
    return () => window.clearInterval(timer);
  }, [loaded.reload]);

  const act = useCallback((call: () => Promise<unknown>) => {
    setBusy(true);
    call().catch(() => {}).finally(() => { setBusy(false); loaded.reload(); });
  }, [loaded.reload]);

  const value: EngineState = {
    status: loaded.data,
    error: loaded.error,
    progress: loaded.data?.searching ? progress : undefined,
    busy,
    searchNow: () => act(api.searchNow),
    pause: (choice) => act(() => api.pause(choice)),
    resume: () => act(api.resume),
    reload: loaded.reload,
  };
  return <EngineContext.Provider value={value}>{children}</EngineContext.Provider>;
}

export function useEngine(): EngineState {
  const state = useContext(EngineContext);
  if (!state) throw new Error("useEngine needs an EngineProvider");
  return state;
}

/** "2 of 4 sources" while searching, from the live progress. */
export function progressLabel(progress: Progress | undefined): string {
  if (!progress?.planned.length) return "";
  const done = progress.planned.filter((id) => id in progress.done).length;
  return `${done} of ${progress.planned.length} sources`;
}
