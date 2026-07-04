# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-user personal secretary/accountability Telegram bot (`bot.py`, ~3800 lines). Any Telegram user can register. It uses OpenAI or Groq for AI responses with full function-calling (tool-use) support. State is split across `state.json` (user prefs/tasks/habits/trackers) and `bot_memory.db` (SQLite for notes, journal, profile/episodic memory, encrypted API keys, rate log, job log).

## Running the bot

```bash
# Install dependencies
pip install -r requirements.txt

# Load credentials and run
export $(grep -v '^#' env | xargs) && python3 bot.py

# Background (current deploy method)
export $(grep -v '^#' env | xargs) && nohup python3 bot.py &

# Check running / view logs
pgrep -a python3
tail -f nohup.out
```

## Running tests

```bash
# All tests
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v

# Unit tests only (no API calls)
python -m pytest tests/test_bot.py -v -k "not sanity and not nl"

# LLM sanity check (one real API call)
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v -k sanity

# NL tool-use tests (real API calls)
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v -k nl
```

The test file stubs out `telegram`, `telegram.ext`, and `timezonefinder` at import time so `bot.py` can be imported without a running Telegram app. Each test gets an isolated SQLite temp file via an autouse `isolate_db` fixture.

## Environment (`env` file)

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Telegram bot token |
| `OPENAI_API_KEY` | Yes | Default LLM key (paid fallback) |
| `GROQ_API_KEY` | No | If set, keyless users get Groq Llama (free tier) |
| `MASTER_KEY` | No | Fernet key for encrypting stored API keys; auto-generated (ephemeral) if unset |
| `MY_CHAT_ID` | No | Used once to migrate old single-user state.json |

Generate a `MASTER_KEY` once with:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
If not set, a temporary key is printed to stderr and API keys are unreadable after restart.

## Architecture

### Storage: hybrid

- **`state.json`** — tasks, conversation history, user prefs (timezone, check-in times, quiet hours, habits, trackers, archived tasks, `today_focus`, `language`, `milestones_sent`, `muted_until`, `llm`). Written atomically via `tempfile.mkstemp` + `os.replace`.
- **`bot_memory.db`** (SQLite, WAL mode) — notes, journal entries, profile memory, episodic memory, encrypted API keys, rate-limit log, job-fire log. `_db()` opens a new connection per call (thread-safe). `_init_db()` creates all tables and migrates legacy data from state.json.

### State schema (per user, authoritative source: `_new_user()`)

```json
{
  "tasks": ["plain string" | {"text": "...", "due": "YYYY-MM-DD", "recur": "daily|weekly|monthly"}],
  "history": [{"role": "user|assistant", "content": "..."}],
  "context": "user-set description of themselves",
  "checkin_enabled": false,
  "timezone": "UTC",
  "checkin_times": {"morning": "08:00", "evening": "21:00"},
  "quiet_hours": {"start": null, "end": null},
  "reminders": [{"id": "uuid4", "time": "HH:MM", "message": "...", "once": false}],
  "trackers": {"weight": {"unit": "kg", "log": [{"ts": "ISO8601", "value": 85.5}]}},
  "habits": {"meditation": {"completions": ["2026-06-27"], "created": "2026-06-01"}},
  "journal": [],
  "activity_days": ["2026-06-27"],
  "archived_tasks": [{"text": "...", "due": null, "completed_at": "ISO8601"}],
  "today_focus": {"date": "YYYY-MM-DD", "text": ""},
  "language": "",
  "milestones_sent": ["streak_7", "tasks_10"],
  "muted_until": "",
  "llm": {"model": null, "api_key": null}
}
```

`get_user(chat_id)` forward-fills any missing keys using `_new_user()` defaults, so new fields are automatically backward-compatible.

### Key functions

| Function | Purpose |
|---|---|
| `get_user(chat_id)` | Init or load user; forward-fills missing keys |
| `chat(chat_id, msg)` | Tool-call loop (up to 5 rounds) → final text reply; stores exchange in history |
| `build_system_prompt(user)` | Injects context, tracker readings, habits, task deadlines, profile/episodic memory |
| `get_llm_client(user)` | Returns `AsyncOpenAI` with user or bot key; Groq auto-detected by `gsk_` prefix |
| `get_model(user)` | User model → Groq default → OpenAI default |
| `_execute_tool(chat_id, name, args)` | Dispatches all LLM tool calls; single big if/elif chain |
| `schedule_user_checkins(app, cid)` | Creates/removes per-user morning+evening daily jobs |
| `schedule_user_alerts(app, cid)` | Deadline alert (09:00), habit reminder (20:00), idle nudge (11:00) |
| `schedule_user_reminder(app, cid, reminder)` | Per-reminder daily/one-shot job |
| `restore_all_jobs(app)` | Called on startup — recreates all jobs from state; catches up missed jobs via `job_log` |
| `_check_milestones(chat_id, app)` | Sends congratulation messages for streak/task-count milestones |

### LLM tool-use (function calling)

`chat()` runs a tool-call loop (max 5 rounds). The `TOOLS` list (defined at module level, ~line 528) exposes these functions to the model:

| Tool | What it does |
|---|---|
| `get_current_time` | User's local time/date/weekday |
| `set_timezone` | Natural-language timezone change (e.g. "I'm in Berlin now") |
| `set_checkins` | Natural-language subscribe/unsubscribe to daily check-ins |
| `get_tasks` / `add_task` / `complete_task` / `remove_task` | Task CRUD |
| `log_tracker` / `get_trackers` / `create_tracker` | Tracker logging |
| `add_reminder` / `get_reminders` / `remove_reminder` | Schedule/list/delete reminders (delay_minutes for relative, time for clock; remove by 1-based number from `get_reminders`) |
| `add_journal_entry` / `get_journal` | Journal write/read |
| `save_memory` | Persist facts: `profile` (permanent), `episodic` (30-day TTL), `note`, or `journal` |
| `add_habit` / `complete_habit` / `remove_habit` / `get_habits` | Habit management |
| `get_notes` / `add_note` / `remove_note` | Quick notes scratchpad (remove by 1-based number from `get_notes`) |
| `set_today_focus` | Set daily intention |
| `search` | Cross-search tasks, notes, journal |
| `get_streak` | Activity streak |

If the model doesn't support tools (custom model endpoint), `chat()` retries without tools.

### LLM routing

1. User's own key (`api_key` in DB) → user's model → Groq default if key starts with `gsk_`
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
| Weekly digest | Sunday 10:00 local | same |
| User reminders | per-reminder HH:MM local | `/remind add` |
| Annual reminders | per-reminder MM-DD | `/remind annual` |

Jobs are **in-memory only** — `restore_all_jobs()` recreates them from state on every restart. `job_log` table records each fire time so missed jobs can be caught up after a restart.

### Custom tracker commands

`/addtracker weight kg` creates the `/weight` command. A `MessageHandler(filters.COMMAND, handle_custom_command)` is registered **last** after all named `CommandHandler`s — it catches anything else and routes to the matching user tracker. Subcommands: `<value>`, `stats`, `history [n]`, `chart [n]`.

### Check-in inline keyboard

Check-in messages include a 4-button `InlineKeyboardMarkup`. Tapping calls `handle_callback` (registered via `CallbackQueryHandler`), which removes the keyboard and triggers a contextual AI follow-up.

### Registration order in `main()`

Handlers are registered in this order (important for precedence):
1. Named `CommandHandler`s (all explicit commands)
2. `CallbackQueryHandler` (inline keyboard)
3. `MessageHandler(filters.COMMAND, handle_custom_command)` — catches unknown commands → custom trackers
4. `MessageHandler(filters.LOCATION, handle_location)` — auto-detect timezone
5. `MessageHandler(filters.Document.ALL, handle_import)` — JSON import
6. `MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)` — free text → AI

## Commands reference

```
/start          Onboarding (new) or status (returning)
/help           Full command list
/tasks          Show active tasks (with deadline badges)
/addtask        Add task [due:YYYY-MM-DD] [every:daily|weekly|monthly]
/removetask n   Delete task
/donetask n     Mark done and archive
/duedate n DATE Set/change due date
/extend n DAYS  Extend due date by N days
/prioritize n   Move task to top
/swap n m       Swap two tasks
/today          Set/show today's focus
/archive        View completed tasks
/setcontext     Personalize AI behavior
/context        Show current context
/settimezone    IANA timezone (or share location)
/setcheckin     Custom morning/evening times
/quiethours     Silence window (handles midnight-spanning)
/mute           Mute notifications for a duration
/unmute         Undo mute
/subscribe      Enable all daily jobs
/unsubscribe    Disable all daily jobs
/checkin        Manual check-in with dashboard
/remind         add|annual|once|list|remove
/note           Save a quick note
/notes          List notes
/removenote n   Delete note
/search         Search tasks, notes, journal
/addtracker     Create custom tracking command
/trackers       List trackers
/removetracker  Delete tracker
/habit          add|done|list|remove
/journal        Save entry + AI reflection
/weekly         7-day AI summary
/reflect        Personal reflection prompt
/suggest        AI task suggestions
/insights       Deep AI analysis of all data
/mystats        Dashboard: streak, counts, model
/streak         Consecutive active-day streak
/pomodoro       Focus timer (default 25 min)
/focus          Pomodoro linked to a specific task
/compress       Summarise and compress history
/setlanguage    Set response language
/clearlanguage  Revert to default language
/time           Show current local time
/export         Download data as JSON
/import         Send JSON file to restore
/setapikey      OpenAI (sk-) or Groq (gsk-) key
/setmodel       Set model name
/clearapikey    Revert to default
/clear          Clear conversation history
/reset          Wipe all data (keeps timezone+LLM)
/feedback       Send feedback to bot owner
/broadcast      Bot-owner only: message all users
/limit          Show remaining AI calls this hour
/adminstats     Bot-owner only: usage stats
```

## Key constraints

- Check-in prompts are injected into history as user messages (not system), so they appear in conversation context. Known quirk.
- `MAX_HISTORY = 20` per user; tracker logs are capped at 5000 entries.
- Rate limit: 30 AI calls/hour/user — backed by SQLite `rate_log`, persists across restarts.
- APScheduler jobs are in-memory — all jobs recreated from state on every restart via `restore_all_jobs()`.
- Tasks support optional `"recur": "daily|weekly|monthly"` — completing a recurring task advances the due date instead of archiving.
- API keys are Fernet-encrypted in SQLite; plaintext never written to state.json after migration.

## `mcp_server.py`

A separate MCP (Model Context Protocol) server exposing a user's tasks/habits/trackers/reminders as tools for Claude Desktop/Code, run standalone (`python3 mcp_server.py`, stdio transport). It reads/writes `state.json` directly and does **not** go through `bot.py`'s runtime or `_execute_tool` dispatch. Its `journal`/`notes` tools operate on a legacy `state.json["journal"]` array — this has diverged from `bot.py`, which stores journal/notes in `bot_memory.db` (SQLite). Edits made through this MCP server will not appear in the bot's actual journal/notes, and vice versa; treat it as task/habit/tracker-only in practice.
