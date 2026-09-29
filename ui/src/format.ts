/** How prices, dates and times read on every screen. */
import type { Deal, DealType } from "./api";

export function euros(amount: number | null | undefined, decimals = 0): string {
  if (amount === null || amount === undefined) return "–";
  return `€${amount.toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function day(iso: string): { weekday: string; date: number; month: string; monthIndex: number } {
  const d = new Date(`${iso}T12:00:00Z`);
  return { weekday: WEEKDAYS[d.getUTCDay()], date: d.getUTCDate(), month: MONTHS[d.getUTCMonth()], monthIndex: d.getUTCMonth() };
}

/** "Wed 21–28 Oct", "Fri 30 Oct – Mon 2 Nov", "Wed 21 Oct" (one way), or the post's travel window. */
export function dates(deal: Pick<Deal, "depart_date" | "return_date" | "travel_window">): string {
  if (!deal.depart_date) return deal.travel_window ?? "";
  const out = day(deal.depart_date);
  if (!deal.return_date) return `${out.weekday} ${out.date} ${out.month}`;
  const back = day(deal.return_date);
  if (out.monthIndex === back.monthIndex) return `${out.weekday} ${out.date}–${back.date} ${back.month}`;
  return `${out.weekday} ${out.date} ${out.month} – ${back.weekday} ${back.date} ${back.month}`;
}

/** A UTC timestamp from the engine ("2026-09-29T11:00:00") in the user's time zone. */
export function when(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "–";
  const moment = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  const today = new Date().toDateString() === moment.toDateString();
  return moment.toLocaleString("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", ...(today ? {} : { weekday: "short", day: "numeric", month: "short" }),
  });
}

export const TYPE_LABELS: Record<DealType, string> = {
  flight: "Flight",
  one_way: "One way",
  package: "Package",
  hotel: "Hotel",
  flight_hotel: "Flight + hotel",
  via_hub: "Via a hub",
};
