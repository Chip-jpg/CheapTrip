/**
 * Hash routes ("#/deals/<id>"): they work from any address the app is opened
 * at, and the engine's cheaptrip:// links and notification buttons set them
 * (desktop/links.py accepts the same routes).
 */
import { useEffect, useState } from "react";

export type Screen = "deals" | "destinations" | "activity" | "settings" | "setup" | "notifications";
export const SCREENS: Screen[] = ["deals", "destinations", "activity", "settings", "setup", "notifications"];

export interface Route {
  screen: Screen;
  dealId?: string;
  /** destinations/<airport>, settings/<section> */
  param?: string;
}

export function parseRoute(hash: string): Route {
  const [screen, id] = hash.replace(/^#?\/?/, "").split("/");
  if (!(SCREENS as string[]).includes(screen)) return { screen: "deals" };
  if (screen === "deals" && id) return { screen: "deals", dealId: decodeURIComponent(id) };
  if ((screen === "destinations" || screen === "settings") && id) {
    return { screen, param: decodeURIComponent(id) };
  }
  return { screen: screen as Screen };
}

export function href(route: Route): string {
  if (route.dealId) return `#/deals/${encodeURIComponent(route.dealId)}`;
  return route.param ? `#/${route.screen}/${encodeURIComponent(route.param)}` : `#/${route.screen}`;
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
