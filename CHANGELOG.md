# Changelog

What each release of CheapTrip brought, newest first. The installers are on the [releases page](https://github.com/Chip-jpg/CheapTrip/releases), and the [development history](docs/ROADMAP.md) has every change card by card.

## 0.8.7 (2026-10-15)

Tidying before 1.0, with nothing new to learn.

- **Removed:** the Skyscanner source (RapidAPI). It was off unless switched on, and its free quota was too small to be useful. Settings files that still mention it keep working.
- **Added:** `SEARCH_ON_START`, on by default. Off, CheapTrip waits for its first scheduled search instead of searching as it starts.
- **Added:** `scripts/demo_data.py`, a month of demo searches with no network, for the screenshots and the end-to-end tests. Also the README's screenshots, the MIT license and this changelog.
- **Tests:** the end-to-end tests no longer need the internet, and the test run has no warnings.

## 0.8.6 (2026-10-15)

- A theme or text size chosen just as the window opened could switch back a moment later. Now it stays.
- A security update for a tool used only to build the app. Nothing changes in the app itself.

## 0.8.5 (2026-10-14)

- The setup wizard leaves the menu once it's finished. **Settings → App → Setup wizard** runs it again.

## 0.8.4 (2026-10-13)

- **Real hotel prices:** each of the best fares shows the cheapest hotel for exactly its dates, from Google Hotels, in Telegram, the notifications, the deal card and its details.
- Cities are searched with their country: Naples means Naples, Italy, not Naples, Florida.
- When Google asks CheapTrip to slow down, Google Flights and Google Hotels pause together.

## 0.8.3 (2026-09-30)

- **No more black window:** pressing a menu button after the setup wizard no longer blanked the whole app.
- Everything about a quarter bigger, with a **Text size** setting (**Ctrl +** and **Ctrl −** from anywhere).
- An automatic test now presses every button on every screen, and CI drives the app's real window on Windows. What they found is fixed: **Resume** during a search, readable reasons when a test notification fails, and **Undo** after hiding a deal.

## 0.8.2 (2026-09-30)

- The same deals, found with less work: searches about three times faster, a quarter of the memory, Destinations opens instantly, the database stays around 160 MB, and the app is smaller.

## 0.8.1 (2026-09-30)

- The Deals screen opens quickly again, even after weeks of searches.
- Settings refuse impossible values and say the allowed range.
- A destination is never both a priority and muted.
- The menu opens as you left it, and the window no longer flashes the wrong theme as it starts.

## 0.8.0 (2026-09-30)

- **CheapTrip is a desktop app**, in its own design, light and dark: Deals with a detail panel and charts, Destinations, Activity, Notifications & tray, Settings and a setup wizard.
- Windows notifications with **Book**, **Details** and **Mute**, and a tray icon showing what the engine is doing.
- One CheapTrip at a time, and `cheaptrip://` links open a deal.

## 0.6.0 (2026-09-29)

The first release with a Windows installer.

- Unusually cheap fares, judged against fares on the same route and trip length departing within 30 days either side. Possible error fares, and price drops that alert again.
- Sources: Ryanair, Google Flights, Travelpayouts, and PiratinViaggio deal posts read by Claude.
- Telegram alerts and commands, and setup checks (`doctor`, `test-alert`, `read-feed`).

Before 0.6.0, CheapTrip was a prototype: the [development history](docs/ROADMAP.md) starts with a review of it.
