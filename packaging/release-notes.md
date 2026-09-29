## Install on Windows

1. Download **`CheapTrip-Setup-0.8.0.exe`** below and run it. No administrator rights are needed.
2. Windows SmartScreen may say *"Windows protected your PC"*, because the installer isn't code-signed. Click **More info → Run anyway**.
3. Leave **Open CheapTrip and set it up** ticked. The app opens on its setup wizard, which asks for:
   - your home airports and the trips you like;
   - how to tell you: Windows notifications, and Telegram if you like;
   - the optional Anthropic and Travelpayouts keys.

   It checks everything at the end.
4. CheapTrip then runs in the tray, by the clock, and starts when you sign in. **Settings → App** turns that off.

**Upgrading from 0.6:** run the new installer. It keeps your settings, preferences and price history in `%APPDATA%\CheapTrip`. The setup wizard opens once, filled in with what you already have: check it, then click **Finish**.

Step-by-step Telegram setup: [docs/telegram_setup.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/telegram_setup.md).

## What's new in 0.8.0: CheapTrip is a desktop app

**A window with its own design**, in light and dark (it follows Windows, or you choose):
- **Deals:**
  - today's unusually cheap fares as cards, each showing how far below its usual price it is and where it sits among similar fares;
  - then the best of the rest, with filters;
  - each deal opens a panel with **How this fare compares**, **This fare over time**, the flights and **Book**.
- **Destinations:** the ones that always alert, the muted ones, and every destination seen in the last 30 days with its trend. Add one with a search box.
- **Activity:** what each source found, every search, and every notification sent.
- **Notifications & tray:** what CheapTrip's notifications will look like with today's deals. Any of them can be sent to your PC.
- **Settings:** changes save as you make them. Keys are never shown back, and **Check** buttons test Telegram and Anthropic. It also has **Check for updates**.
- The top bar says what the engine is doing ("Running · next search in 42 min", "Searching… 2 of 4 sources"). It has **Search now**, **Pause alerts**, and a quick jump to any destination (Ctrl+K).

**Windows notifications**
- Each deal comes with a picture of its price and fare range, and **Book**, **Details** and **Mute** buttons.
- There's also a daily digest, and a notice when a source stops working.
- Telegram still works alongside, and one hourly limit covers both.

**The tray icon** shows whether CheapTrip is running, searching, paused or needs attention. Its menu has Search now, Pause alerts, Settings and Quit. Closing the window keeps CheapTrip searching.

**Also:** only one CheapTrip runs at a time, and `cheaptrip://` links open a deal. The console commands are now `cheaptrip-cli.exe`. The engine's local API only answers this PC, with a new password each launch.

The full list of changes is in [docs/ROADMAP.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/ROADMAP.md).
