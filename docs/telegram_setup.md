# Telegram setup

## 1. Create a bot

1. Open Telegram, search for **@BotFather** and send `/newbot`.
2. Choose a display name and a username ending in `bot`.
3. Copy the token (`123456789:AAF...`) to `TELEGRAM_BOT_TOKEN` in `.env`.

## 2. Find your chat ID

Do this **before** starting the engine: while `main.py run` is running, the bot reads its messages itself (for commands), and `getUpdates` in the browser will show nothing.

1. Open your bot and send it any message (e.g. `/start`).
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
3. Copy the number in `"chat":{"id":...}` to `TELEGRAM_CHAT_ID`.

**Group chat:** add the bot to the group, send a message there, and use the group's ID (a negative number like `-1001234567890`). In groups, commands can be sent as `/deals@YourBot`.

## 3. Check

```bash
python main.py doctor       # "✅ Telegram: bot @YourBot, chat ..." and "✅ Telegram chat: the bot can write to ..."
python main.py test-alert   # a "🧪 Test message" with the current top deals arrives in the chat
python main.py cycle        # alerts or a cycle summary arrive in the chat
```

If `doctor` says **chat not found**, open the bot in Telegram and press Start (or add it to your group), and check `TELEGRAM_CHAT_ID`. If it says **a webhook is set**, commands can't reach the engine: open `https://api.telegram.org/bot<YOUR_TOKEN>/deleteWebhook` in a browser once.

## Commands

While `python main.py run` is running, the bot answers commands from `TELEGRAM_CHAT_ID` only (other chats are ignored):

| Command | Effect |
|---|---|
| `/help` | The command list |
| `/deals` | Top 10 deals of the last 24h, one per route |
| `/status` | Last cycle, deals and alerts today, pause, muted / priority / budget |
| `/health` | Source health |
| `/mute KRK` · `/unmute KRK` | Never / again alert a destination |
| `/priority KRK` · `/unpriority KRK` | Always alert a destination (any price) |
| `/budget 300` · `/budget off` | Skip trips above a total price |
| `/pause [hours]` · `/resume` | Hold instant alerts and the digest (default 24h); searching continues |

Changes are stored in the database and survive restarts; they apply on top of `config/user_preferences.yaml`.

## What you receive

- **Instant alerts** for unusually cheap fares, priority destinations and possible error fares: at most `INSTANT_ALERTS_PER_HOUR` (5) per hour, biggest discount first, and one per route every 6 hours unless the price drops further.
- **Daily digest** at `DIGEST_HOUR:DIGEST_MINUTE` (08:00, `TIMEZONE`): the cheapest deal per route from the last 24 hours.
- **Cycle summary** when a cycle found deals but none was instant.
- **System messages**: startup, a source becoming FAILING, and its recovery.

If a webhook is set for the bot (e.g. from another tool), Telegram refuses `getUpdates` and commands don't work; delete the webhook with `https://api.telegram.org/bot<YOUR_TOKEN>/deleteWebhook`.
