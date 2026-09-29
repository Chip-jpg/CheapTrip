## Install on Windows

1. Download **`CheapTrip-Setup-0.6.0.exe`** below and run it. No administrator rights are needed.
2. Windows SmartScreen may say *"Windows protected your PC"*, because the installer isn't code-signed. Click **More info → Run anyway**.
3. Answer the three pages: your Telegram bot token and chat ID, the optional Anthropic / Travelpayouts keys, and your home airports. You can leave any of them empty and fill them in later from **Start menu → CheapTrip → Edit settings**.
4. Leave **Check my setup now** ticked on the last page. `doctor` should show ✅ for Telegram.
5. Start **CheapTrip** from the Start menu. It also starts minimized when you sign in, unless you unticked that option.

Your settings, preferences and price history are kept in `%APPDATA%\CheapTrip`. Upgrading keeps them, and so does uninstalling.

Step-by-step Telegram setup: [docs/telegram_setup.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/telegram_setup.md). Every setting is described in [docs/configuration.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/configuration.md).

## What's in 0.6.0

- **Unusually cheap fares, judged fairly:** each fare is compared with fares on the same route and trip length departing within 30 days either side. Each fare counts once, at its latest price.
- **Alerts:** a possible error fare needs days of history and a saving of at least €100. Price drops re-alert, while repeats and near-duplicates stay quiet.
- **Sources:** Ryanair, Google Flights (pauses itself after a block), Travelpayouts (with a token), and PiratinViaggio deal posts read by Claude (with an Anthropic key).
- **Telegram commands:** `/deals`, `/status`, `/health`, `/mute`, `/priority`, `/budget`, `/pause`, `/resume`.
- **Setup checks:** Check setup (`doctor`), Send a test message (`test-alert`), and `read-feed`.
- **Windows:** a one-file installer, a per-user app home, and UTF-8 settings files (Notepad-safe).

The full list of changes is in [docs/ROADMAP.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/ROADMAP.md).
