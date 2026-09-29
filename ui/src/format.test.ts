import { describe, expect, it } from "vitest";
import { ago, dates, dayLabel, euros, fromNow, pausedUntil } from "./format";

describe("formatting", () => {
  it("shows prices in euros", () => {
    expect(euros(30)).toBe("€30");
    expect(euros(1234.5, 2)).toBe("€1,234.50");
    expect(euros(null)).toBe("–");
  });

  it("shows trip dates compactly", () => {
    expect(dates({ depart_date: "2026-10-21", return_date: "2026-10-28", travel_window: null })).toBe("Wed 21–28 Oct");
    expect(dates({ depart_date: "2026-10-30", return_date: "2026-11-02", travel_window: null }))
      .toBe("Fri 30 Oct – Mon 2 Nov");
    expect(dates({ depart_date: "2026-10-21", return_date: null, travel_window: null })).toBe("Wed 21 Oct");
    expect(dates({ depart_date: null, return_date: null, travel_window: "November" })).toBe("November");
    expect(dayLabel("2026-10-21")).toBe("Wed 21 Oct");
  });

  it("says how long until and since", () => {
    const now = new Date("2026-09-29T10:00:00Z");
    expect(fromNow("2026-09-29T10:42:00", now)).toBe("in 42 min");
    expect(fromNow("2026-09-29T11:05:00", now)).toBe("in 1 h 5 min");
    expect(fromNow("2026-09-29T09:00:00", now)).toBe("now");
    expect(ago("2026-09-29T09:56:00", now)).toBe("4 min ago");
    expect(ago("2026-09-29T07:00:00", now)).toBe("3 h ago");
    expect(ago("2026-09-28T09:00:00", now)).toBe("yesterday");
    expect(pausedUntil("2036-09-29T08:00:00", now)).toBe("until you resume");
    expect(pausedUntil("2026-09-29T12:00:00", now)).toMatch(/^until \d\d:00$/);
  });
});
