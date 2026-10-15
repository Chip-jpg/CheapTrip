import { act, cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { attentionText } from "./sources";
import { applyTextSize, textSizeForKey, ThemeProvider, useTheme } from "./theme";

const key = (k: string, ctrl = true) => ({ key: k, ctrlKey: ctrl, metaKey: false, altKey: false });

describe("the text size", () => {
  it("grows with Ctrl +, shrinks with Ctrl −, and Ctrl 0 goes back to standard", () => {
    expect(textSizeForKey(key("="), "standard")).toBe("large");
    expect(textSizeForKey(key("+"), "large")).toBe("largest");
    expect(textSizeForKey(key("+"), "largest")).toBe("largest");
    expect(textSizeForKey(key("-"), "largest")).toBe("large");
    expect(textSizeForKey(key("-"), "standard")).toBe("standard");
    expect(textSizeForKey(key("0"), "largest")).toBe("standard");
    expect(textSizeForKey(key("=", false), "standard")).toBeUndefined();  // typing, not a shortcut
    expect(textSizeForKey(key("k"), "standard")).toBeUndefined();
  });

  it("is marked on the page, which sizes everything from it", () => {
    applyTextSize("largest");
    expect(document.documentElement.dataset.textSize).toBe("largest");
    applyTextSize("standard");
    expect(document.documentElement.dataset.textSize).toBeUndefined();
  });
});

describe("the saved theme and text size", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete document.documentElement.dataset.theme;
    delete document.documentElement.dataset.textSize;
  });

  /** The app opened on a dark page, with its saved settings (dark, largest) held back until answer() */
  function open() {
    let answer!: () => void;
    const settings = new Promise<Response>((resolve) => {
      answer = () => resolve(new Response(JSON.stringify({ app: { theme: "dark", text_size: "largest" } })));
    });
    vi.stubGlobal("fetch", (_url: string, init: RequestInit = {}) =>
      (init.method ?? "GET") === "GET" ? settings : Promise.resolve(new Response("{}")));
    document.documentElement.dataset.theme = "dark";
    const app = { state: undefined as unknown as ReturnType<typeof useTheme>, answer };
    const Probe = () => { app.state = useTheme(); return null; };
    render(createElement(ThemeProvider, null, createElement(Probe)));
    return app;
  }
  const arrive = (answer: () => void) => act(async () => {
    answer();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("apply once they arrive", async () => {
    const app = open();
    await arrive(app.answer);
    expect(app.state.choice).toBe("dark");
    expect(document.documentElement.dataset.textSize).toBe("largest");
  });

  it("don't undo a choice made while they were on their way", async () => {
    const app = open();
    act(() => {
      app.state.choose("light");
      app.state.chooseTextSize("large");
    });
    await arrive(app.answer);
    expect(app.state.choice).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.dataset.textSize).toBe("large");
  });
});

describe("what needs attention", () => {
  it("names sources the way the screens do", () => {
    expect(attentionText("google_flights: failing")).toBe("Google Flights isn't working");
    expect(attentionText("secret_flying: degraded")).toBe("Secret Flying is having trouble");
    expect(attentionText("ryanair: unknown")).toBe("Ryanair: unknown");
    expect(attentionText("The last search failed: timeout")).toBe("The last search failed: timeout");
  });
});
