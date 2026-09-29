/** How each source reads on screen (the engine's source ids). */
export const SOURCE_NAMES: Record<string, string> = {
  ryanair: "Ryanair",
  google_flights: "Google Flights",
  travelpayouts: "Travelpayouts",
  skyscanner_api: "Skyscanner",
  piratinviaggio: "PiratinViaggio",
  piratinviaggio_hotels: "PiratinViaggio hotels",
  secret_flying: "Secret Flying",
  going: "Going",
  holiday_pirates: "HolidayPirates",
  holiday_pirates_hotels: "HolidayPirates hotels",
  booking_com_api: "Booking.com",
  google_hotels: "Google Hotels",
};

export function sourceName(id: string): string {
  return SOURCE_NAMES[id] ?? id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
