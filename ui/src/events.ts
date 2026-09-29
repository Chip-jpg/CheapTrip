/** Keeps a screen current: reload() runs when the engine announces something relevant. */
import { useEffect, useRef } from "react";
import { subscribe, type EngineEvent } from "./api";

export function useEngineEvents(onEvent: (event: EngineEvent) => void): void {
  const latest = useRef(onEvent);
  latest.current = onEvent;
  useEffect(() => subscribe((event) => latest.current(event)), []);
}

/** Calls reload (at most every 500 ms) when one of `types` arrives. */
export function useReloadOn(types: string[], reload: () => void): void {
  const timer = useRef<number | undefined>(undefined);
  useEngineEvents((event) => {
    if (!types.includes(event.type)) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(reload, 500);
  });
}
