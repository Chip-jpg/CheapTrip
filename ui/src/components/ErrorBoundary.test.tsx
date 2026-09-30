import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeFetch } from "../test/fixtures";
import { ErrorBoundary } from "./ErrorBoundary";

let fetch: ReturnType<typeof fakeFetch>;
let broken = true;

function Screen() {
  if (broken) throw new Error("cannot read the deals");
  return <p>The deals</p>;
}

beforeEach(() => {
  broken = true;
  fetch = fakeFetch({ "/ui/error": { logged: true }, "/app/open": { opened: "C:/logs" } });
  vi.stubGlobal("fetch", fetch.impl);
  vi.spyOn(console, "error").mockImplementation(() => {});  // React logs the caught error too
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a screen that fails", () => {
  it("shows what happened instead of a blank window, and logs it with the engine", async () => {
    window.location.hash = "#/activity";
    render(<><nav aria-label="Monitor" /><ErrorBoundary where="screen"><Screen /></ErrorBoundary></>);
    expect(screen.getByRole("heading", { name: "This screen hit a problem" })).toBeTruthy();
    expect(screen.getByText("cannot read the deals")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Monitor" })).toBeTruthy();  // the rest of the window stays
    await waitFor(() => expect(fetch.calls.some((c) => c.url.endsWith("/ui/error"))).toBe(true));
    const sent = JSON.parse(fetch.calls.find((c) => c.url.endsWith("/ui/error"))!.body!);
    expect(sent).toMatchObject({ kind: "screen", message: "cannot read the deals", route: "#/activity" });
    expect(sent.stack).toContain("Screen");
  });

  it("can be tried again, and opens the log folder", async () => {
    render(<ErrorBoundary where="screen"><Screen /></ErrorBoundary>);
    fireEvent.click(screen.getByRole("button", { name: /Open the log folder/ }));
    await waitFor(() => expect(fetch.calls.some((c) => c.url.endsWith("/app/open") && c.body === '{"what":"logs"}')).toBe(true));
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: /Try this screen again/ }));
    expect(screen.getByText("The deals")).toBeTruthy();
  });
});
