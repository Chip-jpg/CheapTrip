## Install on Windows

1. Download **`CheapTrip-Setup-0.8.3.exe`** below and run it. No administrator rights are needed.
2. Windows SmartScreen may say *"Windows protected your PC"*, because the installer isn't code-signed. Click **More info → Run anyway**.
3. Leave **Open CheapTrip and set it up** ticked. The app opens on its setup wizard, which asks for:
   - your home airports and the trips you like;
   - how to tell you: Windows notifications, and Telegram if you like;
   - the optional Anthropic and Travelpayouts keys.

   It checks everything at the end.
4. CheapTrip then runs in the tray, by the clock, and starts when you sign in. **Settings → App** turns that off.

**Upgrading from 0.8.0, 0.8.1 or 0.8.2:** run the new installer. Everything is kept, and the setup wizard doesn't open again (if you never finished it, it opens once more: this time the menu works from it). From 0.8.0 or 0.8.1, the first start tidies the database once, which can take a few seconds.

**Upgrading from 0.6:** run the new installer. It keeps your settings, preferences and price history in `%APPDATA%\CheapTrip`. The setup wizard opens once, filled in with what you already have: check it, then click **Finish**.

Step-by-step Telegram setup: [docs/telegram_setup.md](https://github.com/Chip-jpg/CheapTrip/blob/main/docs/telegram_setup.md).

## What's new in 0.8.3

**No more black window.** Pressing a button in the menu no longer turns CheapTrip's window black.
- **The cause:** the setup wizard, which a new install opens on, handed Microsoft Edge WebView2 something it couldn't handle when you left it, and the whole screen disappeared.
- **Tested in the real window:** every change is now checked in CheapTrip's own window on Windows, not only in a browser.
- **If a screen ever fails:** it now shows what happened, with **Try this screen again** and **Open the log folder**, instead of going blank. The menu keeps working, and the error is saved in the log.

**Bigger and easier to read.**
- Everything is about a quarter larger: text, buttons, the menu and the cards. Secondary text has more contrast.
- **Text size** in **Settings → App**: Standard, Large or Largest. **Ctrl +** and **Ctrl −** change it from anywhere, and **Ctrl 0** goes back to standard.
- The screens rearrange themselves to fit the window and the text size, so nothing is cut short.
- The window opens larger (1440×900) where the screen allows.

**Every button checked.** An automatic test now presses every button on every screen. It found three things, now fixed:
- **Pause alerts** during a search now shows **Resume** at once.
- A test notification that Windows or Telegram refuses says why, instead of "HTTP 502".
- **Hide this deal** has an **Undo**.

**Also:**
- **Settings → App → Advanced:** open the log folder, and turn on developer tools (F12) if you need to report a problem.
- Source problems read "Google Flights isn't working".
- The picture in a deal's notification no longer overlaps its caption.
- The icons come from the full icon font again, so the installer is about 4 MB larger than 0.8.2's.

## What got faster in 0.8.2

Nothing new to learn: the same deals, the same alerts, and less work to find them. With a month of price history:

- **Searches do their own work about three times faster**: about 2.4 seconds on Windows. They also open the database once instead of about 1,900 times. CheapTrip used to read every price it had saved (about 370,000 after a month) to judge each fare. Now the database adds them up for it, and it finds exactly the same deals as before.
- **About a quarter of the memory** while it searches: about 86 MB on Windows.
- **Destinations opens instantly.** Its figures are worked out right after each search, not when you open it.
- **A database that stays small.** Prices are kept 45 days (everything looks back 30), and the space old ones leave is handed back to the disk. A database that reached about 445 MB after four months now stays around 160 MB.
- **A smaller app.** The icons now come from a font holding only the ones CheapTrip uses: 99 KB instead of 4 MB. They look exactly the same.
- **Quieter in the tray.** While the window is hidden, the screens don't refresh; they catch up once when you open it.

## What was fixed in 0.8.1

- **The Deals screen opens quickly again.** After a few weeks of searches it could take up to a minute to load, and the menu's deal count as long. Both now take a fraction of a second. The first start after the update spends about a second preparing the price history.
- **Settings refuse impossible values.** For example: 0 days ahead, which searched nothing, or a hotel rating of 42 out of 10. The field goes back to what was saved and says the allowed range, for example "How far ahead: enter a number from 14 to 365". A value like that already in your preferences file now falls back to its default alone, instead of resetting the whole file.
- **"Clear filters" resets the max price** on the Deals screen too.
- **A destination is never both a priority and muted.** Making it a priority unmutes it, and muting it ends its priority, from the app, Telegram or a notification's **Mute** button.
- **The menu stays as you left it.** If you collapse it, it opens collapsed next time.
- **No light flash on start** when you've chosen the dark theme and Windows is light, or the other way round.
- **Behind the scenes:** the build and release steps are updated to the tools GitHub supports.

## New in 0.8.0: CheapTrip is a desktop app

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
