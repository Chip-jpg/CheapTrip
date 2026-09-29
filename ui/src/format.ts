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

/** A UTC timestamp from the engine as a Date. */
export function moment(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

/** "14:00" (local time). */
export function clock(iso: string | null | undefined): string {
  if (!iso) return "–";
  return moment(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

/** "in 42 min", "in 1 h 5 min", "now". */
export function fromNow(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const minutes = Math.round((moment(iso).getTime() - now.getTime()) / 60000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `in ${hours} h ${minutes % 60} min` : `in ${hours} h`;
}

/** "just now", "4 min ago", "2 h ago", "3 days ago". */
export function ago(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "–";
  const minutes = Math.round((now.getTime() - moment(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/** How long a pause lasts: "until 14:00", "until Thu 08:00", or "until you resume" (a pause without end). */
export function pausedUntil(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const end = moment(iso);
  if (end.getFullYear() - now.getFullYear() > 1) return "until you resume";
  const time = end.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (end.toDateString() === now.toDateString()) return `until ${time}`;
  return `until ${WEEKDAYS[end.getDay()]} ${time}`;
}

/** "Wed 21 Oct" for an ISO date. */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = day(iso.slice(0, 10));
  return `${d.weekday} ${d.date} ${d.month}`;
}

/** "21 Oct" for an ISO date. */
export function shortDay(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = day(iso.slice(0, 10));
  return `${d.date} ${d.month}`;
}
