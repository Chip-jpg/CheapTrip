import { describe, expect, it } from "vitest";
import { href, parseRoute } from "./router";

describe("routes", () => {
  it("reads the screens, a deal, a destination and a settings section from the address", () => {
    expect(parseRoute("")).toEqual({ screen: "deals" });
    expect(parseRoute("#/activity")).toEqual({ screen: "activity" });
    expect(parseRoute("#/notifications")).toEqual({ screen: "notifications" });
    expect(parseRoute("#/deals/abc-123")).toEqual({ screen: "deals", dealId: "abc-123" });
    expect(parseRoute("#/destinations/KRK")).toEqual({ screen: "destinations", param: "KRK" });
    expect(parseRoute("#/settings/keys")).toEqual({ screen: "settings", param: "keys" });
    expect(parseRoute("#/nowhere")).toEqual({ screen: "deals" });
  });

  it("writes them back", () => {
    for (const hash of ["#/settings", "#/deals/abc-123", "#/destinations", "#/destinations/KRK", "#/settings/app"]) {
      expect(href(parseRoute(hash))).toBe(hash);
    }
  });
});
