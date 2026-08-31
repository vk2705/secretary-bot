# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-user personal secretary/accountability Telegram bot (`bot.py`, ~4200 lines, single-file architecture). Any Telegram user can register. It uses OpenAI or Groq for AI responses with full function-calling (tool-use) support. State is split across `state.json` (user prefs/tasks/habits/trackers) and `bot_memory.db` (SQLite for notes, journal, profile/episodic memory, encrypted API keys, rate log, job log).

## Running the bot

```bash
# Install dependencies
pip install -r requirements.txt

# Load credentials and run (foreground, e.g. for local debugging)
export $(grep -v '^#' env | xargs) && python3 bot.py
```

**Deployed as `secretary-bot.service`** (systemd, `Restart=on-failure`,
`RestartSec=5`) — the same pattern as `secretary-mcp.service`. `ExecStart`
is `start_bot.sh`, which sources `env`'s `export KEY=value` lines itself
(systemd's own `EnvironmentFile=` can't parse those). Prefer `admin.py
restart-bot` for a code-change restart (goes through the same stop/start
path the `post-commit` hook uses); for anything else:

```bash
sudo systemctl status secretary-bot.service
sudo systemctl restart secretary-bot.service
journalctl -u secretary-bot.service -f
```

`StartLimitBurst=5`/`StartLimitIntervalSec=300` caps a runaway restart loop
(e.g. a persistently bad token) rather than hammering Telegram's API
forever — if the unit lands in `failed` from hitting that limit, fix the
underlying cause first, then `sudo systemctl reset-failed
secretary-bot.service` before starting it again.

## Running tests

```bash
# All tests
export $(grep -v '^#' env | xargs) && python -m pytest tests -v

# Unit tests only (no API calls)
python -m pytest tests -v -k "not sanity and not nl"

# LLM sanity check (one real API call)
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v -k sanity

# NL tool-use tests (real API calls)
export $(grep -v '^#' env | xargs) && python -m pytest tests/test_bot.py -v -k nl
```

The test file stubs out `telegram`, `telegram.ext`, and `timezonefinder` at import time so `bot.py` can be imported without a running Telegram app. `bot.STATE_FILE` and `bot.DB_FILE` are both redirected to temp files at import time (so the suite never touches the real `state.json`/`bot_memory.db`), and each test additionally gets a fresh SQLite temp file via an autouse `isolate_db` fixture.

## `console.py` — Telegram-free back door

A local debug console that drives `bot.py`'s **real** handlers without Telegram,
against a **throwaway sandbox** copy of the data. Built for walking through a
brand-new user's first session (onboarding, free-text conversation) repeatedly.

```bash
sandbox              # ~/bin/sandbox — sources env, uses the venv, works from any cwd

sandbox --keep             # reuse sandbox instead of wiping it
sandbox --chat-id 424242   # pretend to be another user
sandbox --owner            # this chat_id becomes MY_CHAT_ID → /debug, /adminstats work
sandbox --seed-from-live   # copy real state.json/bot_memory.db into a fresh sandbox
sandbox -v                 # show INFO logging (tool calls, LLM HTTP)

# equivalent, from the repo:
export $(grep -v '^#' env | xargs) && python3 console.py
```

At the prompt, `/command` runs the real command handler, anything else goes
through `chat()` to the **real LLM** with full tool-use, and `:`-prefixed words
are console meta-commands (`:help`, `:state`, `:jobs`, `:commands`, `:reset`,
`:chat <id>`, `:verbose`, `:quit`).

How it stays honest and contained:

- **Sandbox by default** — `bot.STATE_FILE`/`bot.DB_FILE` are repointed at
  `/tmp/secretary-bot-console/` *before* `import bot`, because `state =
  load_state()` runs at bot.py's module bottom. The real `state.json` /
  `bot_memory.db` are never opened. Deleting the sandbox dir gives you a virgin
  user; `--keep` preserves it between runs.
- **Runs under the venv, always** — dependencies live in `venv/`, but a bare
  `python3 console.py` picks up `/usr/bin/python3` (no `openai`) and dies at
  bot.py's import line. `_reexec_in_venv()` transparently `os.execv`s into
  `venv/bin/python3` when started outside a virtualenv, guarded against
  looping; `~/bin/sandbox` names that interpreter directly, the way
  `start_bot.sh` does.
- **Zero changes to `bot.py`** — the console is purely additive. It stubs
  `telegram`/`timezonefinder` and fakes `Update`/`context` the same way
  `tests/test_bot.py` does, deliberately keeping that trick recognisably
  identical in both places.
- **Command list is derived, not copied** — `_discover_commands()` parses
  `main()`'s `CommandHandler(...)` registrations out of bot.py's source with
  `ast`, so the console can never drift out of sync with the real command set.
  Unknown `/commands` fall through to `handle_custom_command`, matching
  `main()`'s handler precedence.
- **Jobs are recorded, not run** — `_FakeJobQueue` logs what `schedule_user_*`
  tried to schedule (inspect with `:jobs`) instead of firing it. To actually
  execute a job body, use the owner-gated `/debug fire <job>`, which works here
  under `--owner`.

## `admin.py` — CLI for editing live data

Stops the bot, edits `state.json`/`bot_memory.db` through `bot.py`'s real
functions (never hand-edits JSON/SQLite), restarts the bot. Same stub trick
as `console.py`. Commands: `list-users`, `show-user <chat_id>`,
`set-timezone <chat_id> <iana_tz>`, `list-reminders <chat_id>`,
`remove-reminder <chat_id> <n>`. Read-only commands don't touch the running
process. Refuses to start a second bot instance (Telegram allows only one
`getUpdates` poller — a second process crashes both with `409 Conflict`).

```bash
export $(grep -v '^#' env | xargs) && python3 admin.py list-users
python3 admin.py set-timezone 5838336004 Asia/Yekaterinburg
```

## `githooks/post-commit` — auto-restart the bot after a code change

Every commit to `master` that touches `bot.py` or `requirements.txt`
restarts the live bot automatically (via `admin.py restart-bot`), so a code
change goes live without a manual kill+nohup. Commits that only touch
docs/tests/`PLAN.md` are left alone — nothing in memory changed, so nothing
needs restarting. Only fires on `master` (this repo is worked on directly
there — see the top-level project instructions).

Hooks live in the tracked `githooks/` directory, not `.git/hooks/` (which
git never versions), wired in via `core.hooksPath`. **One-time setup on any
fresh clone**: `git config core.hooksPath githooks`.

## `backup.sh` — hourly data backup

Snapshots `state.json` + `bot_memory.db` into a **separate, private** GitHub
repo (`vk2705/secretary-bot-backups`, cloned at
`/home/ec2-user/secretary-bot-backups`) and commits + pushes if anything
changed. Deliberately a different repo from this one — those two files hold
a real person's journal and behavioral memory, so they must never end up in
this (public) code repository's history, gitignored or not.

Run via `secretary-bot-backup.timer` (systemd, hourly, `Persistent=true` so
a missed run catches up after downtime) → `secretary-bot-backup.service` →
`backup.sh`. Follows the same systemd pattern as `secretary-mcp.service`
rather than cron, which isn't installed on this host. Check with
`systemctl status secretary-bot-backup.timer` / `journalctl -u
secretary-bot-backup.service`. No bot stop/restart is needed. `state.json` is
copied from an atomic on-disk version, while `bot_memory.db` is captured through
SQLite's online backup API and verified with `PRAGMA integrity_check`, including
committed WAL data.

Restore: `git show <commit>:state.json > state.json` (and `bot_memory.db`
the same way) from inside the backup repo, after stopping the bot — see
that repo's README.

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
- **`bot_memory.db`** (SQLite, WAL mode) — notes, journal entries, profile memory, episodic memory, encrypted API keys, rate-limit log, job-fire log, `user_prefs` (critical prefs that must survive a `state.json` overwrite/restore — currently just `timezone`), `reminder_log` (append-only history of every reminder ever created/removed, so removed reminders stay searchable). `_db()` opens a new connection per call (thread-safe). `_init_db()` creates all tables and migrates legacy data from state.json.

`get_user(chat_id)` overlays `user_prefs.timezone` from SQLite onto `state.json`'s copy on every call — SQLite wins if set. This is why `set_timezone`/`/settimezone`/location-detection all call `db_set_pref(chat_id, "timezone", ...)` in addition to setting `user["timezone"]`: without the SQLite write, a stale `/import` or manual `state.json` edit would silently revert the user's timezone. The same three call sites also set `user["timezone_confirmed"] = True` and `db_set_pref(chat_id, "timezone_confirmed", "1")` (via the `_set_user_timezone()` helper) — see "Timezone confirmation gate" below.

### State schema (per user, authoritative source: `_new_user()`)

```json
{
  "tasks": ["plain string" | {"text": "...", "due": "YYYY-MM-DD", "recur": "daily|weekly|monthly"}],
  "history": [{"role": "user|assistant", "content": "..."}],
  "context": "user-set description of themselves",
  "checkin_enabled": false,
  "timezone": "UTC",
  "timezone_confirmed": false,
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
  "llm": {"model": null, "api_key": null},
  "pending_checkin": null
}
```

`pending_checkin` holds `"morning"`/`"evening"`/`null` — set to the check-in label whenever a proactive check-in fires, cleared whenever the user genuinely engages (any `chat()` call with `touch_activity=True`, the default). Used to (a) tell the model when a check-in went unanswered so it can acknowledge the silence instead of repeating the same prompt, and (b) keep `activity_days`/streak honest — see below.

`get_user(chat_id)` forward-fills any missing keys using `_new_user()` defaults, so new fields are automatically backward-compatible.

**Timezone confirmation gate (PLAN.md #61):** `timezone_confirmed` distinguishes "the user explicitly told us their timezone" from "still sitting on the `UTC` default nobody chose." `_timezone_gate(user)` returns a tool-error dict (fed back to the LLM, which asks the user for their timezone and retries) whenever it's `False` and something is about to be scheduled at an absolute clock time — applied to the `add_reminder` tool's absolute-time branches and `set_checkins` when enabling, plus the equivalent direct checks in `/remind add|once|annual`, `/setcheckin`, and `/quiethours`. It deliberately does **not** gate relative delays (`add_reminder`'s `delay_minutes`, or `/remind once 30m`), which don't depend on timezone at all. `build_system_prompt()`'s `tz_section` states the confirmation status explicitly so the model asks proactively instead of only discovering it via a tool error.

### Key functions

| Function | Purpose |
|---|---|
| `get_user(chat_id)` | Init or load user; forward-fills missing keys |
| `chat(chat_id, msg, touch_activity=True)` | Tool-call loop (up to 5 rounds) → final text reply; stores exchange in history. `touch_activity=False` (used by proactive/automated sends — check-ins, weekly digest, missed-checkin catch-up, reminder fires) skips marking the day active and skips clearing `pending_checkin`, so a bot-initiated nudge is never counted as the user having responded |
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

`chat()` runs a tool-call loop (max 5 rounds). The `TOOLS` list (defined at module level, ~line 623) exposes these functions to the model:

| Tool | What it does |
|---|---|
| `get_current_time` | User's local time/date/weekday |
| `set_timezone` | Natural-language timezone change (e.g. "I'm in Berlin now") |
| `set_checkins` | Natural-language subscribe/unsubscribe to daily check-ins |
| `get_tasks` / `add_task` / `complete_task` / `remove_task` | Task CRUD |
| `log_tracker` / `get_trackers` / `create_tracker` | Tracker logging |
| `add_reminder` / `get_reminders` / `remove_reminder` | Schedule/list/delete reminders (delay_minutes for relative, time for clock; remove by 1-based number from `get_reminders`; `get_reminders(include_history=true)` also returns removed reminders from `reminder_log`) |
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

### Reminder snooze button

Reminder messages (daily, one-time, and delay-based) include a single "🔁 Snooze 30 min" `InlineKeyboardButton`. Tapping it calls `handle_callback` (registered via `CallbackQueryHandler`), which re-fires the same reminder through `_run_reminder` after the delay. Check-in messages are plain text with no buttons — users respond in natural language.

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

A separate MCP (Model Context Protocol) server exposing a user's data as tools for Claude Desktop/Code/claude.ai, run standalone. It does **not** go through `bot.py`'s runtime or `_execute_tool` dispatch — it reads/writes `state.json` and `bot_memory.db` directly with its own helpers (`_load`/`_save`, `_db()`). Tasks/habits/trackers/reminders live in `state.json`; notes/journal/profile+episodic memory are read and written directly against `bot_memory.db` (same tables as `bot.py`, so edits made through either surface are immediately visible in the other). Timezone is read from `state.json` but overridden by the SQLite `user_prefs` row when present (`_timezone()`), matching `bot.py`'s `get_user()` precedence — `state.json`'s copy can go stale after `settimezone`.

Two transports:
- **stdio** (default, `python3 mcp_server.py`) — for Claude Desktop/Code.
- **remote HTTP** (`MCP_TRANSPORT=remote`) — serves `streamable-http` on `MCP_REMOTE_HOST:MCP_REMOTE_PORT` (default `127.0.0.1:8545`) so claude.ai's web app can connect, since it can't reach local stdio servers. This process does **not** terminate TLS itself — nginx owns port 443 on the host (it also fronts an unrelated Alteon MCP server on the same box, port-routed by hostname) and reverse-proxies `https://mcp-sbot.alteon.help` to this port; see `/etc/nginx/conf.d/mcp-sbot.alteon.help.conf`. Managed as systemd unit `secretary-mcp.service` (`EnvironmentFile=mcp_remote.env`, `Restart=on-failure`). Config via `mcp_remote.env` (gitignored): `MCP_REMOTE_DOMAIN`, `MCP_REMOTE_HOST`, `MCP_REMOTE_PORT`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`. `MCP_REMOTE_DOMAIN` also sets the allowed `Host` header via `TransportSecuritySettings` to block DNS-rebinding — it must match the public hostname nginx forwards (`Host: $host`), not the internal port.

  **Auth (Milestone B, AUTH-01/02/03)**: replaced the old `?key=` shared-secret query param — see `git log` around the `milestone-b-mcp-oidc` branch if you need the removed `_TokenAuthMiddleware` for reference. `mcp_server.py` is now its own thin OAuth 2.1 Authorization Server (`SecretaryOAuthProvider`, wired via `FastMCP(auth=..., auth_server_provider=...)`), which gates `/mcp` behind a real Bearer token and auto-wires `/authorize`, `/token`, `/register`, `/revoke` and the discovery well-known paths. Google is used only to verify *identity* (OIDC `id_token`, checked against Google's JWKS via `PyJWT`) — it never sees the MCP resource and issues no tokens this server relies on. Two independent Google round-trips share one `/oauth/google/callback` route, disambiguated by the `state` prefix:
  - `link:<code>` — user-initiated. `/link` in Telegram (`bot.py`, writes a one-time code to `mcp_link_codes`) → tap → `/link/<code>` redirects straight to Google → the callback verifies identity and binds `(google_sub, email) → chat_id` into `mcp_identity`. Never touches claude.ai's OAuth dance.
  - `authz:<id>` — claude.ai-initiated, via its own `/authorize` request (parked in `mcp_pending_authorize` while the Google round-trip happens). Resolves the caller's `chat_id` by looking up the already-bound `mcp_identity` row (403 if not yet linked — the error message tells the user to `/link` first), mints our own authorization code, and redirects back to claude.ai's `redirect_uri`.

  Access/refresh tokens are stored in `mcp_tokens` as sha256 hashes only, never plaintext (mirrors how `api_keys` is never plaintext). Access tokens expire after 1 hour; refresh tokens don't expire but rotate on every use. All new tables (`mcp_link_codes`, `mcp_identity`, `mcp_oauth_clients`, `mcp_pending_authorize`, `mcp_auth_codes`, `mcp_tokens`) are created by `bot.py`'s `_init_db()`, matching the existing pattern where `mcp_server.py` never creates schema itself.

  **Per-user tool scoping (AUTH-03)**: `chat_id` is no longer a caller-supplied parameter on the remote transport — a valid Bearer token proves who's calling, and that's the only identity a tool call can act as. All 18 tool bodies live as undecorated `_impl` functions (`_get_tasks`, `_add_task`, etc., `chat_id` kept as their first param) that are wrapped differently per transport, decided once at import time via `_IS_REMOTE = os.environ.get("MCP_TRANSPORT") == "remote"`: the remote branch defines thin `@mcp.tool()` wrappers with `chat_id` dropped from the schema entirely, resolving it instead via `_authed_chat_id()` (reads `.subject` off `mcp.server.auth.middleware.auth_context.get_access_token()`); the stdio branch registers the same `_impl` functions under their public names with `chat_id` still explicit. `list_users()` and the `bot://users*` resources exist only on stdio — remote callers have no way to enumerate or address another user's data.
  - TLS cert for `mcp-sbot.alteon.help` is Let's Encrypt via certbot (`webroot` method, since nginx permanently owns 80/443 — `standalone` would conflict), copied into `/etc/pki/nginx/` (nginx's expected location on this box) rather than read from `/etc/letsencrypt/live/` directly. A renewal deploy-hook (`/etc/letsencrypt/renewal-hooks/deploy/copy-to-nginx.sh`, host-wide — also benefits the Alteon cert) re-copies and reloads nginx automatically on renewal.
  - nginx has no explicit `default_server` on port 80/443; the first server block loaded from `conf.d/*.conf` (alphabetical) wins as the fallback for unmatched hostnames. Any new vhost needs its own explicit `server_name` block, or requests to it will silently fall through to whichever vhost happens to load first.
