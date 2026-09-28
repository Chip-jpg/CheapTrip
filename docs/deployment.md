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
