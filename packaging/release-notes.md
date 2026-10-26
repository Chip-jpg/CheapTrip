## Install on Windows

1. Download **`CheapTrip-Setup-1.0.0.exe`** below and run it. No administrator rights are needed.
2. Windows SmartScreen may say *"Windows protected your PC"*, because the installer isn't code-signed. Click **More info → Run anyway**.
3. Leave **Open CheapTrip and set it up** ticked. The app opens on its setup wizard, which asks for:
   - your home airports and the trips you like;
   - how to tell you: Windows notifications, and Telegram if you like;
   - the optional Anthropic and Travelpayouts keys.

   It checks everything at the end.
4. CheapTrip then runs in the tray, by the clock, and starts when you sign in. **Settings → App** turns that off.

**Upgrading from 0.8.0 or later:** run the new installer. Everything is kept, and the setup wizard doesn't open again (if you never finished it, it opens once more). From 0.8.0 or 0.8.1, the first start tidies the database once, which can take a few seconds.

**Upgrading from 0.6:** run the new installer. It keeps your settings, preferences and price history in `%APPDATA%\CheapTrip`. The setup wizard opens once, filled in with what you already have: check it, then click **Finish**.

Step-by-step Telegram setup: [docs/telegram_setup.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/telegram_setup.md).

## CheapTrip 1.0

**CheapTrip is finished.** 1.0 is 0.8.7, tested from end to end and declared done: nothing in the app changes, and there is nothing new to learn. If you have 0.8.7, it's the same app with a new number.

What CheapTrip 1.0 does:
- **Searches by itself** every 90 minutes from the tray: Ryanair, Google Flights and, with a token, Travelpayouts. PiratinViaggio deal posts are read by Claude when an Anthropic key is set.
- **Knows what's cheap:** every fare is compared with similar fares (same route and trip length, departing within 30 days either side) seen over the last month. Fares far below the usual price, new lows and possible error fares alert at once; the rest wait for the daily digest.
- **Explains each deal:** how far below its usual price it is, how it compares, its price over time, and a hotel priced from Google Hotels for exactly its dates.
- **Tells you once:** Windows notifications with **Book**, **Details** and **Mute**, and Telegram if you like, within one hourly limit. A deal alerts again only if its price drops further.
- **Yours to steer:** priority destinations always alert and muted ones never do, from the app, a notification or Telegram.
- **A desktop app** in light and dark: Deals, Destinations, Activity, Notifications & tray, Settings and a setup wizard, with a text size setting and a tray icon that shows what the engine is doing.

Every release in short: [CHANGELOG.md](https://github.com/Chip-jpg/CheapTrip/blob/main/CHANGELOG.md). How it was built, card by card: [docs/ROADMAP.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/ROADMAP.md).
