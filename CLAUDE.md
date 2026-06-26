# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file personal Telegram bot (`bot.py`) that acts as a personal secretary/accountability coach. It uses OpenAI GPT-4o for responses and is restricted to a single Telegram user (`MY_CHAT_ID`).

## Running the bot

Load environment variables and start:
```bash
source env && python3 bot.py
```

To run in the background (as currently deployed):
```bash
source env && nohup python3 bot.py &
```

Check if it's running:
```bash
pgrep -a python3
```

View live logs:
```bash
tail -f nohup.out
```

## Environment

Credentials are stored in `env` (not tracked by git ideally). Required variables:
- `TELEGRAM_TOKEN` — Telegram bot token
- `OPENAI_API_KEY` — OpenAI API key
- `MY_CHAT_ID` — (optional) used only once to migrate old single-user `state.json` to the new per-user format

## Architecture

Everything lives in `bot.py`:

- **State** — persisted to `state.json` as `{"users": {"<chat_id>": {tasks, history, context, checkin_enabled}}}`. Each user has isolated state. `get_user(chat_id)` initializes a new user on first contact.
- **`chat(chat_id, message)`** — appends to that user's history, builds the system prompt (generic + optional user context), calls GPT-4o, appends reply, saves state.
- **Scheduled jobs** — `morning_checkin` at 05:00 UTC and `evening_checkin` at 18:00 UTC (Israel time = UTC+3, so 08:00 and 21:00 local). Each job iterates over all users where `checkin_enabled=True` and sends each one a personalized check-in.
- **Commands** — `/start`, `/tasks`, `/addtask`, `/removetask`, `/setcontext`, `/context`, `/subscribe`, `/unsubscribe`, `/checkin`, `/clear`. No chat ID guards — any Telegram user can register.

## State migration

The old single-user `state.json` format (`{tasks, history}` at top level) is auto-migrated on startup to the new per-user format using the optional `MY_CHAT_ID` env var. After migration, `MY_CHAT_ID` is no longer required.

## Key constraints

- The scheduled job prompts are inserted into history as user messages (not as system messages), so internal check-in prompts appear in conversation history and leak into future context. Known quirk.
- `MAX_HISTORY = 20` per user — when exceeded, oldest messages are trimmed from the front.
- Model is hardcoded to `gpt-4o`; `max_tokens=600`.
- Check-ins fire at fixed UTC times for all users (no per-user timezone support).
