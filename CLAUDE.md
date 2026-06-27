# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-user personal secretary/accountability Telegram bot (`bot.py`). Any Telegram user can register. It uses OpenAI or Groq for AI responses and stores all state in `state.json`.

## Running the bot

```bash
# Install dependencies
pip install -r requirements.txt

# Load credentials and run
export $(grep -v '^#' env | xargs) && python3 bot.py

# Background (current deploy method)
export $(grep -v '^#' env | xargs) && nohup python3 bot.py &

# Check running
pgrep -a python3

# View logs
tail -f nohup.out
```

## Environment (`env` file)

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Telegram bot token |
| `OPENAI_API_KEY` | Yes | Default LLM key (paid fallback) |
| `GROQ_API_KEY` | No | If set, keyless users get Groq Llama (free tier) |
| `MY_CHAT_ID` | No | Used once to migrate old single-user state.json |

## Architecture

Everything lives in `bot.py` (~1600 lines). No database — all state is `state.json`.

### State schema (per user)

```json
{
  "tasks": ["plain string" | {"text": "...", "due": "YYYY-MM-DD"}],
  "history": [{"role": "user|assistant", "content": "..."}],
  "context": "user-set description of themselves",
  "checkin_enabled": false,
  "timezone": "Asia/Jerusalem",
  "checkin_times": {"morning": "08:00", "evening": "21:00"},
  "quiet_hours": {"start": "23:00", "end": "07:00"},
  "reminders": [{"id": "uuid4", "time": "HH:MM", "message": "...", "once": false}],
  "trackers": {"weight": {"unit": "kg", "log": [{"ts": "ISO8601", "value": 85.5}]}},
  "habits": {"meditation": {"completions": ["2026-06-27"], "created": "2026-06-01"}},
  "journal": [{"ts": "ISO8601", "entry": "..."}],
  "activity_days": ["2026-06-27"],
  "archived_tasks": [{"text": "...", "due": null, "completed_at": "ISO8601"}],
  "llm": {"model": null, "api_key": null}
}
```

### Key functions

| Function | Purpose |
|---|---|
| `get_user(chat_id)` | Init or load user; forward-fills missing keys |
| `chat(chat_id, msg)` | Calls LLM with history + system prompt; error-handled |
| `build_system_prompt(user)` | Injects context, tracker readings, habits, task deadlines |
| `get_llm_client(user)` | Returns `AsyncOpenAI` with user or bot key; Groq auto-detected by `gsk_` prefix |
| `get_model(user)` | User model → Groq default → OpenAI default |
| `schedule_user_checkins(app, cid)` | Creates/removes per-user morning+evening daily jobs |
| `schedule_user_alerts(app, cid)` | Creates/removes deadline alert (09:00), habit reminder (20:00), idle nudge (11:00) |
| `schedule_user_reminder(app, cid, reminder)` | Per-reminder daily job |
| `restore_all_jobs(app)` | Called on startup — recreates all jobs from state |

### LLM routing

1. User's own key (`api_key` set) → user's model → Groq default if key starts with `gsk_`
2. Bot's `GROQ_API_KEY` set → Groq `llama-3.3-70b-versatile` (free tier for all keyless users)
3. Fallback → bot's `OPENAI_API_KEY` + `gpt-4o-mini`

### Scheduled jobs per subscribed user (all timezone-aware, quiet-hours-aware)

| Job | Time | Trigger |
|---|---|---|
| Morning check-in | `checkin_times.morning` local | `subscribe` / `settimezone` / `setcheckin` |
| Evening check-in | `checkin_times.evening` local | same |
| Deadline alert | 09:00 local | same |
| Habit reminder | 20:00 local | same |
| Idle nudge | 11:00 local | same (only fires if inactive 3+ days) |
| User reminders | per-reminder HH:MM local | `/remind add` |

Jobs are **in-memory only** — `restore_all_jobs()` recreates them from state on every restart.

### Custom tracker commands

`/addtracker weight kg` creates the `/weight` command. A `MessageHandler(filters.COMMAND, handle_custom_command)` is registered **last** after all named `CommandHandler`s — it catches anything else and routes to the matching user tracker. Subcommands: `<value>`, `stats`, `history [n]`, `chart [n]`.

### Check-in inline keyboard

Check-in messages include a 4-button `InlineKeyboardMarkup`. Tapping calls `handle_callback` (registered via `CallbackQueryHandler`), which removes the keyboard and triggers a contextual AI follow-up.

### State migration

Old single-user format `{tasks, history}` at top level is auto-migrated to `{"users": {MY_CHAT_ID: {...}}}` on first startup if `MY_CHAT_ID` is set.

## Commands reference

```
/start          Onboarding (new) or status (returning)
/help           Full command list
/tasks          Show active tasks (with deadline badges)
/addtask        Add task [due:YYYY-MM-DD]
/removetask n   Delete task
/donetask n     Mark done and archive
/archive        View completed tasks
/setcontext     Personalize AI behavior
/settimezone    IANA timezone
/setcheckin     Custom morning/evening times
/quiethours     Silence window (handles midnight-spanning)
/subscribe      Enable all daily jobs
/unsubscribe    Disable all daily jobs
/remind         add|once|list|remove
/addtracker     Create custom tracking command
/trackers       List trackers
/removetracker  Delete tracker
/habit          add|done|list|remove
/journal        Save entry + AI reflection
/weekly         7-day AI summary
/insights       Deep AI analysis of all data
/mystats        Dashboard: streak, counts, model
/streak         Consecutive active-day streak
/pomodoro       Focus timer (default 25 min)
/export         Download data as JSON
/import         Send JSON file to restore
/setapikey      OpenAI (sk-) or Groq (gsk-) key
/setmodel       Set model name
/clearapikey    Revert to default
/clear          Clear conversation history
/reset          Wipe all data (keeps timezone+LLM)
/adminstats     Bot-owner only: usage stats
```

## Key constraints

- Check-in prompts are injected into history as user messages (not system), so they appear in conversation context. Known quirk.
- `MAX_HISTORY = 20` per user; `MAX_LOG_ENTRIES = 500` per tracker; `MAX_JOURNAL_ENTRIES = 200`.
- Rate limit: 30 AI calls/hour/user (in-memory rolling window, resets on restart).
- APScheduler jobs are in-memory — all jobs recreated from state on every restart via `restore_all_jobs()`.
