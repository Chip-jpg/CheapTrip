import { describe, expect, it } from "vitest";
import { attentionText } from "./sources";
import { applyTextSize, textSizeForKey } from "./theme";

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

describe("what needs attention", () => {
  it("names sources the way the screens do", () => {
    expect(attentionText("google_flights: failing")).toBe("Google Flights isn't working");
    expect(attentionText("secret_flying: degraded")).toBe("Secret Flying is having trouble");
    expect(attentionText("ryanair: unknown")).toBe("Ryanair: unknown");
    expect(attentionText("The last search failed: timeout")).toBe("The last search failed: timeout");
  });
});
