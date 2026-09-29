import { describe, expect, it } from "vitest";
import { dates, euros } from "./format";

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
  });
});
