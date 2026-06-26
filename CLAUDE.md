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

- **State** — persisted to `state.json` as `{"users": {"<chat_id>": {...}}}`. `get_user(chat_id)` initializes a new user on first contact and forward-fills any keys added in newer versions.
- **`chat(chat_id, message)`** — appends to that user's history, builds a per-user system prompt (includes context, recent tracker readings, task list), calls OpenAI, appends reply, saves state.
- **Scheduling** — `schedule_user_checkins(app, chat_id)` creates per-user APScheduler daily jobs at 08:00 and 21:00 in the user's IANA timezone. `schedule_user_reminder` does the same for individual reminders. `restore_all_jobs(app)` recreates everything on startup.
- **Custom tracker commands** — a catch-all `MessageHandler(filters.COMMAND, handle_custom_command)` registered after all named handlers intercepts `/weight`, `/mood`, etc. and routes to the matching user tracker.
- **LLM per user** — `get_llm_client(user)` and `get_model(user)` use the user's stored key/model if set, falling back to `DEFAULT_API_KEY` / `DEFAULT_MODEL` (`gpt-4o-mini`).

## State schema (per user)

```json
{
  "tasks": [],
  "history": [],
  "context": "",
  "checkin_enabled": false,
  "timezone": "UTC",
  "reminders": [{"id": "uuid4", "time": "HH:MM", "message": "..."}],
  "trackers": {"weight": {"unit": "kg", "log": [{"ts": "ISO8601", "value": 85.5}]}},
  "journal": [{"ts": "ISO8601", "entry": "..."}],
  "llm": {"model": null, "api_key": null}
}
```

## Commands reference

| Command | Description |
|---|---|
| `/settimezone <IANA>` | Set timezone used for check-ins and reminders |
| `/remind add HH:MM <msg>` | Schedule a daily reminder |
| `/addtracker <name> [unit]` | Create a custom tracking command |
| `/<name> <value>` | Log a tracker value |
| `/<name> stats\|history` | View tracker stats or history |
| `/setapikey <key>` | Use own OpenAI key (message auto-deleted) |
| `/setmodel <model>` | Switch OpenAI model |
| `/journal <text>` | Save journal entry + get AI reflection |
| `/weekly` | AI weekly summary of tasks + tracker data |
| `/export` | Download all data as JSON |

## State migration

The old single-user `state.json` format (`{tasks, history}` at top level) is auto-migrated on startup using the optional `MY_CHAT_ID` env var.

## Key constraints

- Check-in prompts are inserted into history as user messages (not system messages), so they appear in conversation context. Known quirk.
- `MAX_HISTORY = 20` per user; `MAX_LOG_ENTRIES = 500` per tracker; `MAX_JOURNAL_ENTRIES = 200`.
- Default model is `gpt-4o-mini`; users with their own key can switch to `gpt-4o` or others.
- APScheduler jobs are in-memory only — `restore_all_jobs()` recreates them from state on every restart.
