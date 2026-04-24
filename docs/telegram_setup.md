English | [简体中文](telegram_setup_zh-CN.md)

# Telegram Bot Setup Guide

## 1. Create a Telegram Bot (2 minutes)

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Enter a name for your bot (e.g. `My VPS Agent`)
4. Enter a username (must end with `bot`, e.g. `my_vps_agent_bot`)
5. BotFather will return a Token (format: `123456789:ABCdefGHIjklMNO...`)
6. Add it to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNO...
   ```

## 2. Get Your User ID (for whitelist)

1. Search for **@userinfobot** and send any message
2. It will return your User ID (a number)
3. Add to `.env` (optional — leave empty to allow all users):
   ```
   TELEGRAM_ALLOWED_USERS=123456789
   ```
   Multiple users: `TELEGRAM_ALLOWED_USERS=111,222,333`

## 3. Start

```bash
cd /path/to/nono-cowork

# Run directly
.venv/bin/python -m src.channels.telegram

# Or via uv entry point
uv run telegram-bot
```

You should see this output on successful startup:
```
==================================================
🚀 Telegram Bot started (Polling mode)
   Bot: @my_vps_agent_bot
   Allowed users: 123456789
==================================================
```

## 4. Run in Background

### Option 1: screen / tmux

```bash
screen -S telegram-bot
cd /path/to/nono-cowork
.venv/bin/python -m src.channels.telegram
# Ctrl+A, D to detach
```

### Option 2: systemd service (recommended)

Use the unified multi-channel service file:

```bash
# Edit nono-cowork.service: replace YOUR_USERNAME with your actual username
# Set CHANNELS=telegram in .env (or add telegram to existing channels)
sudo cp nono-cowork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nono-cowork
```

## 5. Usage

- Find your Bot in Telegram and send messages directly
- Send `/reset` to reset the session
- Send `/help` to see available commands
