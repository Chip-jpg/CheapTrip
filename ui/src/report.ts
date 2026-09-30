/**
 * Errors the screens hit go to the engine's log (POST /ui/error, B47): the app's
 * window has no console, and an error used to leave nothing but a blank window.
 */
import { api, type UiError } from "./api";

const MAX_REPORTS = 20;  // per page load: a screen failing on every refresh fills no log
const sent = new Set<string>();

export function reportError(kind: UiError["kind"], error: unknown, component?: string): void {
  const err = error instanceof Error ? error : new Error(typeof error === "string" ? error : JSON.stringify(error) ?? String(error));
  const key = `${kind}:${err.message}`;
  if (sent.has(key) || sent.size >= MAX_REPORTS) return;
  sent.add(key);
  api.reportError({
    kind, message: err.message || err.name, stack: err.stack?.slice(0, 4000), component: component?.slice(0, 4000),
    route: window.location.hash || "#/",
  }).catch(() => {});  // the engine is gone: nothing to tell
}

/** Report what no error boundary catches: errors in event handlers and timers, and unhandled rejections. */
export function reportUncaught(): () => void {
  const onError = (event: ErrorEvent) => reportError("uncaught", event.error ?? event.message);
  const onRejection = (event: PromiseRejectionEvent) => reportError("rejection", event.reason);
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
