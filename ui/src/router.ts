/**
 * Hash routes ("#/deals/<id>"): they work from any address the app is opened
 * at, and the engine's cheaptrip:// links and notification buttons set them.
 */
import { useEffect, useState } from "react";

export type Screen = "deals" | "destinations" | "activity" | "settings" | "setup";
export const SCREENS: Screen[] = ["deals", "destinations", "activity", "settings", "setup"];

export interface Route {
  screen: Screen;
  dealId?: string;
}

export function parseRoute(hash: string): Route {
  const [screen, id] = hash.replace(/^#?\/?/, "").split("/");
  if (screen === "deals" && id) return { screen: "deals", dealId: decodeURIComponent(id) };
  return { screen: (SCREENS as string[]).includes(screen) ? (screen as Screen) : "deals" };
}

export function href(route: Route): string {
  return route.dealId ? `#/deals/${encodeURIComponent(route.dealId)}` : `#/${route.screen}`;
}

export function navigate(route: Route): void {
  window.location.hash = href(route);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}
