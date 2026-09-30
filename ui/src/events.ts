/** Keeps a screen current: reload() runs when the engine announces something relevant. */
import { useEffect, useRef } from "react";
import { subscribe, type EngineEvent } from "./api";

export function useEngineEvents(onEvent: (event: EngineEvent) => void): void {
  const latest = useRef(onEvent);
  latest.current = onEvent;
  useEffect(() => {
    return subscribe((event) => latest.current(event));
  }, []);
}

/** Whether nobody can see the page: the window is hidden in the tray, or minimized. */
export const pageHidden = (): boolean => document.visibilityState === "hidden";

/** Calls `callback` when the page is shown again (the window opened from the tray). */
export function useWhenShown(callback: () => void): void {
  const latest = useRef(callback);
  latest.current = callback;
  useEffect(() => {
    const changed = () => { if (!pageHidden()) latest.current(); };
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, []);
}

/**
 * Calls reload (at most every 500 ms) when one of `types` arrives. While the page is
 * hidden it only notes that something changed, and reloads once when it's shown.
 */
export function useReloadOn(types: string[], reload: () => void): void {
  const timer = useRef<number | undefined>(undefined);
  const missed = useRef(false);
  useWhenShown(() => {
    if (!missed.current) return;
    missed.current = false;
    reload();
  });
  useEngineEvents((event) => {
    if (!types.includes(event.type)) return;
    if (pageHidden()) {
      missed.current = true;
      return;
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(reload, 500);
  });
}
