import { describe, expect, it } from "vitest";
import { href, parseRoute } from "./router";

describe("routes", () => {
  it("reads the screens and a deal from the address", () => {
    expect(parseRoute("")).toEqual({ screen: "deals" });
    expect(parseRoute("#/activity")).toEqual({ screen: "activity" });
    expect(parseRoute("#/deals/abc-123")).toEqual({ screen: "deals", dealId: "abc-123" });
    expect(parseRoute("#/nowhere")).toEqual({ screen: "deals" });
  });

  it("writes them back", () => {
    for (const hash of ["#/settings", "#/deals/abc-123", "#/destinations"]) {
      expect(href(parseRoute(hash))).toBe(hash);
    }
  });
});
