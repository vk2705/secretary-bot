# Secretary Bot

A multi-user personal secretary and accountability coach for Telegram, powered by OpenAI or Groq. Users interact entirely in **plain natural language** — the bot understands intent and acts without needing slash commands.

---

## What It Does

The bot is a persistent AI assistant that lives in Telegram and remembers everything about you across conversations. You can:

- Talk to it naturally: *"Remind me to drink water in 30 minutes"*, *"Add a tracker for my daily steps"*, *"I finished task 2"*
- Use slash commands for precise control
- Get proactive daily check-ins, deadline alerts, and habit nudges
- Export/import all your data as JSON

---

## Quick Start

### 1. Prerequisites

```bash
pip install -r requirements.txt
```

Requires Python 3.12+.

### 2. Configure credentials

Create an `env` file (already in `.gitignore`):

```bash
export TELEGRAM_TOKEN=your_bot_token_from_BotFather
export OPENAI_API_KEY=sk-...          # paid fallback for keyless users
export GROQ_API_KEY=gsk_...           # optional: free tier for all keyless users
export MY_CHAT_ID=123456789           # optional: enables /adminstats
```

### 3. Run

```bash
# Foreground
export $(grep -v '^#' env | xargs) && python3 bot.py

# Background (production)
export $(grep -v '^#' env | xargs) && nohup python3 bot.py &

# Check running
pgrep -a python3

# View logs
tail -f nohup.out
```

---

## Architecture

| File | Purpose |
|---|---|
| `bot.py` | Everything — ~3500 lines, single-file architecture |
| `state.json` | Per-user config, tasks, habits, trackers, reminders (gitignored) |
| `bot_memory.db` | SQLite — unlimited notes and journal entries (gitignored) |
| `requirements.txt` | Python dependencies |
| `tests/test_bot.py` | 155-test suite (unit + LLM sanity + NL tool-use) |

### State storage

Two stores work in parallel:

**`state.json`** — structured per-user config:
```
tasks, habits, trackers, reminders, timezone, checkin_times,
quiet_hours, context, today_focus, activity_days, archived_tasks,
llm (api_key + model), muted_until, language, milestones_sent, notes (legacy)
```

**`bot_memory.db`** — SQLite with two tables, unlimited rows:
```sql
notes   (id, chat_id, text, ts, auto)
journal (id, chat_id, entry, ts, auto)
```
`auto=1` means the entry was saved automatically by the AI without an explicit user command.

---

## LLM Integration

### Routing

Every message goes through `chat()`, which runs a **tool-call loop** (up to 5 rounds):

```
User message
  → build_system_prompt(user, chat_id)
  → OpenAI/Groq API with TOOLS list
  → AI may call zero or more tools (actions)
  → tool results fed back to the model
  → final text reply stored in history
```

Models that don't support function calling fall back to plain text automatically.

### Key fallback chain (for users without their own key)

```
1. User has own key  →  use their key + their chosen model
2. Bot's GROQ_API_KEY set  →  free Groq Llama 3 for everyone
3. Fallback  →  bot's OpenAI key + gpt-4o-mini (you pay)
```

Users can set their own key with `/setapikey sk-...` or `/setapikey gsk_...`.

### AI tools (19 total)

The AI can call any of these directly mid-conversation:

| Tool | Triggered by |
|---|---|
| `get_current_time` | "What time is it?" |
| `get_tasks` | "Show my tasks" |
| `add_task(text, due_date?)` | "Remind me to call the dentist" |
| `complete_task(task_number)` | "I finished task 2" |
| `remove_task(task_number)` | "Delete task 3" |
| `create_tracker(name, unit?)` | "Add a tracker for sleep" |
| `log_tracker(name, value)` | "I walked 8500 steps today" |
| `get_trackers()` | "Show my trackers" |
| `add_reminder(message, delay_minutes? / time?, once?)` | "Remind me in 30 min / at 09:00 daily" |
| `add_journal_entry(text)` | "Save this to my journal" |
| `get_journal(limit?)` | "Show my recent journal entries" |
| `save_memory(text, type?)` | *Auto-called* when user shares facts/reflections |
| `get_habits()` | "Show my habits" |
| `add_habit(name)` | "Add a daily habit: meditation" |
| `complete_habit(name)` | "I did my meditation today" |
| `remove_habit(name)` | "Delete the running habit" |
| `get_notes()` | "Show my notes" |
| `add_note(text)` | "Note: buy groceries" |
| `set_today_focus(text)` | "My focus today is the project deadline" |
| `search(query)` | "Search for dentist in my data" |
| `get_streak()` | "What's my current streak?" |

### Auto-memory

The system prompt contains rule 11:

> *Silently call `save_memory` whenever the user shares a personal fact, decision, plan, or reflection that is worth remembering for future conversations. Do NOT tell the user you are saving it.*

This means the bot passively remembers everything meaningful without the user needing to explicitly journal or take notes.

---

## Commands

### Tasks
| Command | Description |
|---|---|
| `/tasks` | Show active tasks with deadline badges (⚠️ overdue, 🔴 today, 🟡 soon) |
| `/addtask <text> [due:YYYY-MM-DD] [every:daily\|weekly\|monthly]` | Add task, optionally with deadline or recurrence |
| `/donetask <n>` | Mark done and archive; recurring tasks roll forward |
| `/removetask <n>` | Delete task permanently |
| `/archive` | View last 20 completed tasks |
| `/prioritize <n>` | Move task to top of list |
| `/duedate <n> YYYY-MM-DD` | Change a task's due date |
| `/extend <n> <days>` | Push due date forward N days |
| `/swap <n> <m>` | Swap two tasks' positions |

### Trackers
| Command | Description |
|---|---|
| `/addtracker <name> [unit]` | Create a custom tracker (e.g. `/addtracker weight kg`) |
| `/<name> <value>` | Log a value (e.g. `/weight 74.5`) |
| `/<name> stats` | Show latest, average, min/max, 7-day trend |
| `/<name> history [n]` | Show last N entries |
| `/<name> chart [n]` | ASCII chart of last N values |
| `/trackers` | List all trackers with last value |
| `/removetracker <name>` | Delete a tracker |

### Habits
| Command | Description |
|---|---|
| `/habit add <name>` | Create a daily habit |
| `/habit done <name>` | Mark today's completion |
| `/habit list` | Show all habits with streak and today's status |
| `/habit stats <name>` | Detailed stats: 7-day grid, longest streak, 30-day rate |
| `/habit remove <name>` | Delete a habit |

### Reminders
| Command | Description |
|---|---|
| `/remind add HH:MM <message>` | Daily recurring reminder |
| `/remind once 30m\|2h\|HH:MM <message>` | One-time reminder |
| `/remind annual MM-DD HH:MM <message>` | Yearly reminder (e.g. birthdays) |
| `/remind list` | Show all reminders |
| `/remind remove <n>` | Delete a reminder |

### Journal & Notes
| Command | Description |
|---|---|
| `/journal <text>` | Save a journal entry + get AI reflection |
| `/note <text>` | Save a quick note to the scratchpad |
| `/notes` | List all notes |
| `/removenote <n>` | Delete note by number |
| `/search <query>` | Search tasks, notes, and journal |

### AI Analysis
| Command | Description |
|---|---|
| `/weekly` | AI-generated weekly summary |
| `/insights` | Deep analysis of trackers, habits, journal, tasks |
| `/reflect` | Personal reflection: patterns, what's working, focus area |
| `/suggest` | 3 personalized task/habit suggestions |

### Check-ins & Scheduling
| Command | Description |
|---|---|
| `/subscribe [HH:MM HH:MM]` | Enable daily check-ins; optionally set times |
| `/unsubscribe` | Disable all daily jobs |
| `/setcheckin HH:MM HH:MM` | Change morning/evening check-in times |
| `/checkin` | Manual check-in now |
| `/quiethours HH:MM HH:MM` | Silence window (handles midnight-spanning) |
| `/quiethours off` | Disable quiet hours |
| `/mute 4h\|2d` | Suppress all notifications for a period |
| `/unmute` | Cancel mute early |

### Settings
| Command | Description |
|---|---|
| `/setcontext <text>` | Tell the bot about yourself |
| `/settimezone <IANA\|UTC+N>` | Set timezone; or share 📍 location for auto-detect |
| `/setlanguage <language>` | Force AI responses in a specific language |
| `/clearlanguage` | Reset to default language |
| `/setapikey <key>` | Set your own OpenAI (`sk-`) or Groq (`gsk-`) key |
| `/setmodel <model>` | Set preferred model |
| `/clearapikey` | Revert to bot's default |
| `/today [text]` | Set/view today's focus |

### Utilities
| Command | Description |
|---|---|
| `/mystats` | Dashboard: streak, activity chart, tasks, habits, model |
| `/streak` | Current activity streak |
| `/limit` | Rate limit status |
| `/time` | Your current local time |
| `/pomodoro [minutes]` | Focus timer (default 25 min) |
| `/focus [task_n] [minutes]` | Pomodoro linked to a specific task |
| `/compress` | Summarise conversation history to save tokens |
| `/export` | Download all your data as JSON |
| `/import` | Restore from a JSON export file |
| `/clear` | Clear conversation history |
| `/reset` | Wipe all data (keeps timezone and API settings) |
| `/feedback <text>` | Send feedback to the bot admin |
| `/help` | Full command reference |
| `/adminstats` | Bot owner only: usage statistics |

---

## Scheduled Jobs (per subscribed user)

All jobs are timezone-aware and respect quiet hours and mute status.

| Job | Default time | Trigger |
|---|---|---|
| Morning check-in | 08:00 local | `/subscribe` or `/setcheckin` |
| Evening check-in | 21:00 local | same |
| Deadline alert | 09:00 local | same |
| Habit reminder | 20:00 local | same |
| Idle nudge | 11:00 local | same (only if inactive 3+ days) |
| Weekly digest | Sunday 10:00 local | same |
| User reminders | per-reminder HH:MM | `/remind add` |
| Annual reminders | per-reminder date | `/remind annual` |

Jobs are **in-memory only** — `restore_all_jobs()` recreates them from state on every restart.

---

## Timezone Detection

Three methods, in order of user preference:

1. **Share location** — tap 📎 → Location in Telegram. The bot uses `timezonefinder` to convert GPS coordinates to an IANA timezone name automatically.
2. **UTC offset** — `/settimezone UTC+7` is converted to the POSIX-correct `Etc/GMT-7`.
3. **IANA name** — `/settimezone Europe/London`.

---

## Rate Limiting

30 AI calls per user per hour (in-memory rolling window, resets on bot restart). Bot-owner can adjust `RATE_LIMIT` in source.

---

## Data Limits

| Data type | Limit |
|---|---|
| Conversation history | 20 messages (kept short for LLM context) |
| Notes | Unlimited (SQLite) |
| Journal entries | Unlimited (SQLite) |
| Tracker log entries | 5000 per tracker |
| Archived tasks | 100 most recent |

---

## Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Load credentials
export $(grep -v '^#' env | xargs)

# Run all 155 tests (~40s including real API calls)
python3 -m pytest tests/test_bot.py -v

# Unit tests only (no API calls, <1s)
python3 -m pytest tests/test_bot.py -k "not sanity and not nl" -v

# LLM sanity tests only
python3 -m pytest tests/test_bot.py -k "sanity" -v

# Natural language tool-use tests only
python3 -m pytest tests/test_bot.py -k "nl" -v
```

### Test sections

| Section | Count | What it covers |
|---|---|---|
| Unit — timezone | 6 | `UTC±N` normalization, IANA passthrough |
| Unit — task helpers | 10 | text/due/tags extraction, deadline badge formatting |
| Unit — state | 3 | `get_user`, forward-fill, new user init |
| Unit — LLM routing | 6 | client selection, model selection |
| Unit — system prompt | 6 | tasks/context/language/focus injection |
| Unit — rate limit | 5 | per-user isolation, over-limit blocking |
| Unit — streak | 4 | consecutive days, broken streak |
| Unit — execute tool | 14 | all 7 original tools + edge cases |
| Unit — quiet hours | 3 | active, inactive, midnight-spanning |
| Unit — SQLite notes | 10 | CRUD, isolation, search, unlimited count |
| Unit — SQLite journal | 7 | CRUD, limit param, chronological order |
| Unit — new tools | 34 | all 10 new tools + edge cases |
| Unit — habit streak | 5 | streak calculation edge cases |
| Unit — mute logic | 4 | future/expired/invalid timestamps |
| Unit — parse helpers | 8 | `_parse_once_delay`, `_parse_local_time`, etc. |
| Unit — system prompt + DB | 3 | notes/journal appear in prompt |
| LLM sanity | 4 | model responds, coherent, language, bad key |
| NL original | 7 | 7 original natural language → tool mappings |
| NL new tools | 7 | 7 new natural language → tool mappings |

---

## Project Structure

```
secretary-bot/
├── bot.py               # Main bot (~3500 lines)
├── mcp_server.py        # MCP server exposing bot data to Claude Desktop
├── mcp_oauth.py         # Google OIDC login for mcp_server.py's remote transport
├── state.json           # User data (gitignored)
├── bot_memory.db        # SQLite notes + journal (gitignored)
├── nohup.out            # Log file (gitignored)
├── env                  # Credentials (gitignored)
├── requirements.txt     # Python dependencies
├── tests/
│   └── test_bot.py      # 155-test suite
├── PLAN.md              # Feature planning log
└── README.md            # This file
```

---

## MCP Server

`mcp_server.py` exposes the bot's data as an [MCP](https://modelcontextprotocol.io/) server so Claude Desktop, Claude Code, or claude.ai can read and modify any user's data directly — tasks, habits, trackers, reminders (`state.json`), plus notes, journal, and profile/episodic memory (`bot_memory.db`).

### stdio (Claude Desktop / Claude Code, local)

```bash
python3 mcp_server.py
```

### Remote HTTPS (claude.ai "Add custom connector")

claude.ai's web app can't reach a local stdio server, so a second deployment runs `mcp_server.py` in `MCP_TRANSPORT=remote` mode as a long-running service (systemd unit `secretary-mcp.service`), listening on `127.0.0.1:8545`. nginx terminates TLS on port 443 for the public hostname and reverse-proxies to that port (`/etc/nginx/conf.d/mcp-sbot.alteon.help.conf`) — the app itself never binds a public port directly. This box also fronts an unrelated Alteon MCP server the same way, routed by hostname.

Auth is real OIDC (`mcp_oauth.py`), not a shared token: `mcp_server.py` acts as an OAuth 2.1 Authorization Server towards MCP clients — claude.ai's "Add custom connector" dialog does the standard dynamic-client-registration + authorize + PKCE dance against it — but delegates the actual login step to Google. After a user approves the Google consent screen, `mcp_server.py` mints its own short-lived signed access token that encodes the caller's Telegram `chat_id`, resolved via the `MCP_OIDC_USERS` mapping; every tool call is then restricted to that `chat_id` (or every `chat_id`, for an entry mapped to the special `"*"` admin value).

Add a custom connector in claude.ai with just the base URL — no query-string token needed anymore:

```
https://mcp-sbot.alteon.help/mcp
```

claude.ai will discover the OAuth endpoints from `/.well-known/oauth-protected-resource`, register itself, and redirect the user to Google to log in.

Config (`mcp_remote.env`, gitignored):
- `GOOGLE_OIDC_CLIENT_ID` / `GOOGLE_OIDC_CLIENT_SECRET` — an OAuth 2.0 Web application client from Google Cloud Console, with `https://mcp-sbot.alteon.help/google/callback` registered as its authorized redirect URI.
- `MCP_OIDC_USERS` — JSON mapping of allowed Google account emails to Telegram `chat_id` (or `"*"` for admin access to every user), e.g. `{"vitaly.kroivets@gmail.com": "123456789"}`. Anyone not in this map is rejected at the Google-callback step with `403`.
- `MCP_OIDC_JWT_SECRET` — HMAC secret this server uses to sign its own access/refresh tokens. Generate one the same way as `MASTER_KEY`: `python3 -c "import secrets; print(secrets.token_hex(32))"`. If unset, an ephemeral secret is generated at startup and logged as a warning — every issued token stops validating on the next restart, forcing callers to log back in.
- `MCP_REMOTE_DOMAIN` (public hostname — used both for the Host-header DNS-rebinding check and as the OIDC `issuer_url`/Google callback base), `MCP_REMOTE_HOST`/`MCP_REMOTE_PORT` (local bind address, default `127.0.0.1:8545`).

All OAuth state (pending Google logins, issued codes, refresh tokens) is kept in memory only, so a restart just forces a fresh login — nothing sensitive is written to disk. Requests are logged to `journalctl -u secretary-mcp.service` (method, path, client, status) for debugging connector issues.

### Tools exposed

`list_users`, `get_tasks`, `add_task`, `complete_task`, `remove_task`, `get_archived_tasks`, `get_habits`, `log_habit`, `get_trackers`, `log_tracker`, `get_journal`, `add_journal_entry`, `get_notes`, `add_note`, `remove_note`, `get_memory`, `get_user_stats`, `get_reminders`.

---

## Security Notes

- `env` and `state.json` are gitignored — never committed
- User API keys stored in `state.json` in plaintext — secure your server
- `/setapikey` auto-deletes the message after saving to avoid key exposure in chat
- Rate limiting prevents abuse of the bot owner's API key
- If `GROQ_API_KEY` is set, keyless users use Groq's free tier — bot owner's OpenAI key is never consumed for them
