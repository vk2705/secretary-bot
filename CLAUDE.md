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
- `MY_CHAT_ID` — Telegram chat ID of the sole authorized user

## Architecture

Everything lives in `bot.py`:

- **State** — persisted to `state.json` as `{"tasks": [...], "history": [...]}`. Tasks are the accountability goals; history is the last 20 messages sent to OpenAI as context.
- **`chat()`** — core function that appends user message to history, calls GPT-4o with the system prompt + history, appends reply, and saves state.
- **Scheduled jobs** — `morning_checkin` at 05:00 UTC and `evening_checkin` at 18:00 UTC (Israel time = UTC+3, so 08:00 and 21:00 local). These inject a prompt as the "user" turn and send the AI reply to Telegram.
- **Commands** — `/tasks`, `/addtask`, `/removetask`, `/checkin`, `/clear`, `/start`. All handlers guard against non-authorized chat IDs at the top.

## Key constraints

- The scheduled job prompts are inserted into history as user messages (not as system messages), which means the bot's internal check-in prompts appear in conversation history and leak into future context. This is a known quirk.
- `MAX_HISTORY = 20` — when exceeded, the oldest messages are trimmed from the front.
- Model is hardcoded to `gpt-4o`; `max_tokens=600`.
