import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useReloadOn } from "./events";

/** An EventSource the test can speak through. */
class TalkingEventSource {
  static last: TalkingEventSource | undefined;
  private handlers: Record<string, ((message: MessageEvent) => void)[]> = {};
  constructor(public url: string) { TalkingEventSource.last = this; }
  addEventListener(type: string, handler: (message: MessageEvent) => void) {
    (this.handlers[type] ??= []).push(handler);
  }
  close() {}
  emit(type: string) {
    (this.handlers[type] ?? []).forEach((h) => h(new MessageEvent(type, { data: JSON.stringify({ type }) })));
  }
}

let visibility: DocumentVisibilityState = "visible";
const setVisibility = (state: DocumentVisibilityState) => {
  visibility = state;
  document.dispatchEvent(new Event("visibilitychange"));
};

function Screen({ reload }: { reload: () => void }) {
  useReloadOn(["cycle_finished"], reload);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", TalkingEventSource);
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  visibility = "visible";
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("refreshing on engine events", () => {
  it("batches a burst of events into one refresh", () => {
    const reload = vi.fn();
    render(<Screen reload={reload} />);
    act(() => { TalkingEventSource.last!.emit("cycle_finished"); TalkingEventSource.last!.emit("cycle_finished"); });
    act(() => { vi.advanceTimersByTime(600); });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("waits while the window is hidden in the tray, then refreshes once when shown", () => {
    const reload = vi.fn();
    render(<Screen reload={reload} />);
    act(() => setVisibility("hidden"));
    act(() => { for (let i = 0; i < 5; i++) TalkingEventSource.last!.emit("cycle_finished"); });
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(reload).not.toHaveBeenCalled();

    act(() => setVisibility("visible"));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    expect(reload).toHaveBeenCalledTimes(1);  // nothing happened meanwhile: no refresh
  });
});
