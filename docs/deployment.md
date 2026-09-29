# Deployment

## Check the setup first

```bash
python main.py doctor
```

It checks Telegram (with a live `getMe`), the optional keys, leftover old thresholds in `.env`, the preferences file and its airport codes, that the database is writable, and lists every source as enabled or why it is off. It exits 1 on blocking problems.

## Local

```bash
pip install -r requirements.txt
cp .env.example .env
cp config/user_preferences.yaml.example config/user_preferences.yaml
python main.py doctor
python main.py cycle     # one cycle
python main.py run       # 24/7; Ctrl+C stops it cleanly
```

## Docker Compose (recommended)

```bash
docker-compose up -d
docker-compose exec engine python main.py doctor
docker-compose logs -f engine
docker-compose exec engine python main.py health
docker-compose down      # SIGTERM: the engine stops its scheduler and Telegram polling cleanly
```

- **Volumes:** `./data` (SQLite database), `./logs`, `./config` (preferences) survive restarts and upgrades.
- **Non-root:** the container runs as user `app` (uid 1000). If the host folders already exist and belong to root (from an older image), give them to that user once:
  ```bash
  sudo chown -R 1000:1000 data logs config
  ```
- **Healthcheck:** `python main.py healthcheck` exits 1 when no cycle has completed in the last 3 × `SCRAPE_INTERVAL_MINUTES` (4.5 hours by default) or the database is unreadable. Compose runs it every 5 minutes after a 15-minute start period; `docker ps` shows the container as unhealthy when the engine is stuck.

## Windows

The installer (`CheapTrip-Setup-<version>.exe`, attached to each [GitHub release](https://github.com/Chip-jpg/CheapTrip/releases)) installs the app for the current user, without administrator rights:

| What | Where |
|---|---|
| Programs | `%LOCALAPPDATA%\Programs\CheapTrip\CheapTrip.exe` (the app) and `cheaptrip-cli.exe` (the console commands), replaced on upgrade |
| Settings (`.env`) | `%APPDATA%\CheapTrip\.env`, created at the first start; the setup wizard and Settings change it |
| Preferences | `%APPDATA%\CheapTrip\config\user_preferences.yaml` |
| Database and logs | `%APPDATA%\CheapTrip\data\`, `%APPDATA%\CheapTrip\logs\engine.log` |
| The running app's address | `%APPDATA%\CheapTrip\app.json`: its local API port and token, removed when it quits |

- **Start menu:**
  - **CheapTrip** opens the app. It is a window with the screens (Edge WebView2), a tray icon and Windows notifications; see [the desktop app](../README.md#the-desktop-app).
  - **Check setup (console)** runs `doctor` in a console window, for when the app won't start.
  - **Open data folder** opens `%APPDATA%\CheapTrip`. The app's Settings screen changes the files there and applies most changes at once; after editing them by hand, quit CheapTrip from the tray and open it again.
- **First run:** the app opens on its setup wizard. Its five steps are: welcome, home airports and trips, notifications (Windows, and Telegram with a Check button), optional keys, and the checks. It opens by itself until it's finished (`SETUP_DONE`), and the sidebar's **Setup wizard** runs it again. Up to 0.6 the installer asked these questions itself.
- **The tray:**
  - Closing the window keeps CheapTrip running in the tray (`CLOSE_TO_TRAY`).
  - **Quit** in the tray menu stops it; a running search is cancelled cleanly.
- **Start at sign-in:** the installer's option, ticked by default, adds `CheapTrip.exe --minimized` to the current user's Run key (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `CheapTrip`), so the app starts in the tray. Settings → App → **Start when I sign in to Windows** changes the same value, and Task Manager's Startup apps shows it too. Up to 0.6 it was a Startup folder shortcut, which the new installer removes.
- **One CheapTrip at a time:** the app, `cheaptrip-cli run` and `main.py ui` share one lock (a named mutex), so the engine never runs twice. Opening the app again brings the running one forward, and a `cheaptrip://` link (`cheaptrip://deal/<id>`, `cheaptrip://activity`) opens that screen. The installer registers the links for the current user.
- **Notifications:** the app registers its AppUserModelID (`CheapTrip`, the same as the Start menu shortcut), so its notifications show under CheapTrip in Windows' notification settings.
- **Without WebView2:** the screens open in the default browser instead, and the tray and notifications work as usual.
- **Upgrade:** run the new installer. It closes a running CheapTrip, replaces the program and keeps the settings, so it doesn't ask for them again.
- **Uninstall:** Settings → Apps, or Start menu → Uninstall CheapTrip. It stops the app and removes the programs, shortcuts and `cheaptrip://` links, but keeps `%APPDATA%\CheapTrip`; delete that folder to remove your settings and price history.
- **Command line:** `"%LOCALAPPDATA%\Programs\CheapTrip\cheaptrip-cli.exe" <command>` accepts every command in the README (`doctor`, `read-feed`, `status`, ...). It was `cheaptrip.exe` up to 0.6; Windows file names ignore case, so the app and the console program need different names. Set `CHEAPTRIP_HOME` to keep the files somewhere else.
- **Silent install** (e.g. for several PCs): `CheapTrip-Setup-<version>.exe /VERYSILENT`. To skip the setup wizard, put a prepared `.env` (with `SETUP_DONE=true`) and `config\user_preferences.yaml` in `%APPDATA%\CheapTrip` first; installs keep existing files.

**How it's built:** `packaging/build_windows.ps1` builds the screens (`ui/`, `npm ci && npm run build`), freezes the app with PyInstaller (`packaging/cheaptrip.spec`: both programs in one folder) and wraps it with Inno Setup (`packaging/installer.iss`). It then installs the result silently and checks the installed app:
- "start at sign-in" (the Run value) and the `cheaptrip://` link;
- `--version`, `doctor` on the settings the first start creates, and a real dry-run cycle;
- the app started in the tray:
  - its API and screens, its first search, and **Search now**;
  - the setup wizard's answers saved through the API, and the sign-in switch turned off and on;
  - a test notification;
  - a second launch handing over;
  - the console engine refusing to start alongside it;
  - Quit;
- the uninstaller.

CI runs this on every pull request. `.github/workflows/release.yml` runs it and publishes the release `v<version>` with the installer attached:
- **Automatically:** when a merge to `main` carries a version that has no release yet.
- **By hand:** on a `v*` tag, or when started manually.

To release, bump `utils/version.py` and `pyproject.toml`, update `packaging/release-notes.md`, and merge.

## systemd (VPS without Docker)

```ini
# /etc/systemd/system/cheaptrip.service
[Unit]
Description=CheapTrip deal engine
After=network-online.target

[Service]
Type=simple
User=cheaptrip
WorkingDirectory=/opt/cheaptrip
EnvironmentFile=/opt/cheaptrip/.env
ExecStart=/opt/cheaptrip/.venv/bin/python main.py run
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now cheaptrip
journalctl -u cheaptrip -f
```

For monitoring, a cron job or timer can run `python main.py healthcheck` and alert on a non-zero exit.

## Monitoring

- `python main.py health` — per-source status (OK / DEGRADED / FAILING / STALE / DISABLED with the reason); a source that becomes FAILING, and its recovery, are also announced once in Telegram.
- `/status` and `/health` in Telegram.
- `python main.py status` — deal, alert and price-history counts.
- Logs are structured (structlog) in `logs/engine.log`.

## Upgrading

```bash
git pull
pip install -r requirements.txt        # or: docker-compose build && docker-compose up -d
python main.py doctor
```

The database schema is upgraded automatically on startup (`init_db` adds missing tables and columns).
