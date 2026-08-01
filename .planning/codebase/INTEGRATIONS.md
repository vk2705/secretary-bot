# External Integrations

**Analysis Date:** 2026-08-01

## APIs & External Services

**Telegram:**
- Telegram Bot API (https://core.telegram.org/bots/api)
  - SDK/Client: `python-telegram-bot` 21.6+
  - Auth: `TELEGRAM_TOKEN` (environment variable)
  - Usage: All user interactions, command handling, message sending, inline keyboards, callback queries, location sharing (for timezone auto-detect)
  - Implementation: `bot.py` uses `Application`, `CommandHandler`, `MessageHandler`, `CallbackQueryHandler` from `telegram.ext`

**LLM - Primary (OpenAI):**
- OpenAI API (https://api.openai.com/v1)
  - SDK/Client: `openai>=1.0.0` (AsyncOpenAI for async/tool support)
  - Auth: `OPENAI_API_KEY` (sk-..., environment variable)
  - Usage: Main fallback LLM for AI responses and function calling; default model `gpt-4o-mini`
  - Implementation: `bot.py:get_llm_client()` returns AsyncOpenAI with this key; up to 5 rounds of tool-call loops per message

**LLM - Secondary (Groq):**
- Groq API OpenAI-compatible endpoint (https://api.groq.com/openai/v1)
  - SDK/Client: `openai>=1.0.0` (AsyncOpenAI with custom base_url)
  - Auth: `GROQ_API_KEY` (gsk_..., environment variable, optional)
  - Usage: Free tier LLM for all users without their own key (if bot-owner sets this). Model: `llama-3.3-70b-versatile`
  - Fallback chain: User's own key → Bot's Groq key (if set) → Bot's OpenAI key (fallback)
  - Implementation: `bot.py:get_llm_client()` detects gsk_ prefix, routes to Groq endpoint; same AsyncOpenAI interface as OpenAI

**Geolocation:**
- Timezone detection from GPS coordinates
  - Service: `timezonefinder>=6.0.0` library (offline, uses embedded data)
  - Usage: User shares 📍 location in Telegram → bot detects IANA timezone automatically
  - Implementation: `bot.py:handle_location()` calls `_tf.timezone_at(lat=..., lng=...)`
  - Auth: None (local library)

## Data Storage

**Databases:**
- SQLite3 (local)
  - File: `bot_memory.db` (gitignored)
  - Connection: `_db()` opens WAL-mode connection per call (thread-safe)
  - Client: sqlite3 (stdlib)
  - Tables:
    - `notes` — quick notes scratchpad (chat_id, text, ts, auto flag)
    - `journal` — journal entries (chat_id, entry, ts, auto flag)
    - `api_keys` — encrypted user API keys (chat_id, encrypted_key, updated_at)
    - `rate_log` — per-user rate limiting (chat_id, timestamp)
    - `profile_memory` — permanent facts about user (chat_id, fact, ts)
    - `episodic_memory` — time-bound events with 30-day TTL (chat_id, event, ts, expires_at)
    - `job_log` — missed job detection after restarts (chat_id, job_type, fired_at)
    - `user_prefs` — critical settings that survive state.json overwrites; currently `timezone` (chat_id, key, value)
    - `reminder_log` — append-only history of all reminders (id, chat_id, reminder_uuid, message, reason, kind, time, created_at, removed_at)
  - Mode: WAL (Write-Ahead Logging) for concurrent access
  - Isolation: Tests use fresh temp DB per run via `isolate_db` fixture

**State Storage (JSON):**
- File: `state.json` (gitignored)
  - Contains: Per-user config, tasks, habits, trackers, reminders, timezone, conversation history, language, milestones sent, muted_until, llm (model + api_key), pending_checkin
  - Atomicity: Written via `tempfile.mkstemp` + `os.replace` (atomic on POSIX)
  - Access: Serialized via `load_state()` / `save_state()`

**File Storage:**
- Local filesystem only — no cloud storage integration
- JSON export/import via `/export` and `/import` commands

**Caching:**
- In-memory: `_snooze_cache` dict for reminder tokens during session
- No distributed cache; jobs recreated from state.json on restart

## Authentication & Identity

**Auth Provider:**
- Custom (no OAuth)
- Telegram user ID is primary identity; every user identified by numeric `chat_id`
- No user registration server — any Telegram user can start a conversation

**API Key Management:**
- User can set their own OpenAI (sk-...) or Groq (gsk_...) keys via `/setapikey <key>`
- Keys encrypted in SQLite with Fernet symmetric cipher (`cryptography>=42.0.0`)
- Master key: `MASTER_KEY` env var (auto-generated ephemeral if unset, printed to stderr)
- Bot-owner keys: `OPENAI_API_KEY` and `GROQ_API_KEY` in environment
- Plaintext keys in `state.json` migrated to encrypted storage on startup (`_init_db` migration)

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, Rollbar, etc.)

**Logs:**
- Python logging to stdout/stderr
- Foreground: logs to console
- Background (nohup): `nohup.out` file
- systemd service: journalctl (StandardOutput=journal, StandardError=journal)
- MCP server: `secretary_mcp.remote` logger (method, path, client, key validity, status — never token value)

**Metrics:**
- In-memory rate limit tracking: `rate_log` SQLite table (persists across restarts)
- Job fire log: `job_log` SQLite table (detects missed jobs after restarts)
- No distributed metrics (Prometheus, CloudWatch, etc.)

## CI/CD & Deployment

**Hosting:**
- Self-hosted (Amazon Linux 2, EC2 implied by `/etc/`)
- No cloud deployment detected

**Deployment Method:**
- Manual via nohup: `export $(grep -v '^#' env | xargs) && nohup python3 bot.py &`
- systemd service: `secretary-mcp.service` (remote MCP server only)

**Process Management:**
- Foreground bot: nohup with `nohup.out` log
- MCP remote server: systemd unit with auto-restart on failure, `Restart=on-failure`, `RestartSec=2`

**CI Pipeline:**
- None detected; no GitHub Actions, GitLab CI, etc.

**Reverse Proxy / TLS:**
- nginx (terminating TLS on port 443, reverse-proxying to MCP server on 127.0.0.1:8545)
- TLS certificates: Let's Encrypt via certbot (webroot method)
- Cert renewal: auto via renewal deploy-hook (`/etc/letsencrypt/renewal-hooks/deploy/copy-to-nginx.sh`)
- nginx config: `/etc/nginx/conf.d/mcp-sbot.alteon.help.conf`

## Environment Configuration

**Required env vars:**
- `TELEGRAM_TOKEN` — Telegram bot token from BotFather
- `OPENAI_API_KEY` — OpenAI API key (sk-...)

**Optional env vars:**
- `GROQ_API_KEY` — Groq API key (gsk-...); if set, free-tier users bypass OpenAI
- `MASTER_KEY` — Fernet encryption key for stored API keys (auto-generated if missing)
- `MY_CHAT_ID` — Single user's chat_id for one-time state.json migration

**Secrets location:**
- `env` file (gitignored) — source into shell with `export $(grep -v '^#' env | xargs)`
- `mcp_remote.env` (gitignored) — EnvironmentFile for systemd service

## Webhooks & Callbacks

**Incoming:**
- Telegram Bot API polling (long-polling, not webhooks)
- Inline keyboard callbacks: `CallbackQueryHandler` for check-in button responses

**Outgoing:**
- None detected (no third-party webhook notifications)

**Scheduled Jobs (via APScheduler):**
- In-memory, timezone-aware, quiet-hours-aware
- Per-user subscribed jobs: morning/evening check-ins, deadline alert, habit reminder, idle nudge, weekly digest
- Per-reminder jobs: daily or one-time at specified HH:MM or anniversary date
- On restart: `restore_all_jobs()` recreates all jobs from state; `job_log` table catches up missed fires
- Implementation: `schedule_user_checkins()`, `schedule_user_alerts()`, `schedule_user_reminder()` in `bot.py`

## LLM Tool-Use (Function Calling)

**Tool Definitions:**
- 29 tools exposed to OpenAI/Groq via `TOOLS` list in `bot.py` (line 641–1060)
- All tools implemented in `_execute_tool()` (async dispatcher, line 1063+)

**Tool Categories:**
1. **Time/Location:** `get_current_time`, `set_timezone`
2. **Tasks:** `get_tasks`, `add_task`, `complete_task`, `remove_task`
3. **Trackers:** `create_tracker`, `log_tracker`, `get_trackers`
4. **Reminders:** `add_reminder`, `get_reminders`, `remove_reminder`
5. **Journal/Notes:** `add_journal_entry`, `get_journal`, `add_note`, `get_notes`, `remove_note`
6. **Habits:** `add_habit`, `complete_habit`, `remove_habit`, `get_habits`
7. **Memory:** `save_memory` (type: profile, episodic, note, journal)
8. **Goals:** `set_today_focus`
9. **Search:** `search` (tasks, notes, journal, reminders)
10. **Status:** `get_streak`
11. **Check-ins:** `set_checkins` (enable/disable daily jobs, set times)

**Model Fallback:**
- If model doesn't support tools, `chat()` retries without tools
- No custom endpoint support (hardcoded OpenAI/Groq detection)

## MCP (Model Context Protocol) Server

**Purpose:**
- Exposes secretary-bot data as MCP tools for Claude Desktop, Claude Code, and claude.ai
- Reads/writes `state.json` and `bot_memory.db` directly (not through bot.py runtime)

**Transports:**

**stdio (Claude Desktop/Code):**
- `python3 mcp_server.py` (default)
- Bidirectional stdin/stdout communication

**remote HTTP (claude.ai web):**
- `MCP_TRANSPORT=remote` mode
- ASGI server: uvicorn on `127.0.0.1:8545` (default)
- nginx reverse proxy: TLS on port 443 (hostname: `mcp-sbot.alteon.help`)
- Auth: `?key=<MCP_REMOTE_TOKEN>` query parameter (not OAuth)
- systemd service: `secretary-mcp.service` (managed separately from bot.py)

**Tools Exposed (mcp_server.py):**
- `list_users` — discover registered users
- `get_tasks`, `add_task`, `complete_task`, `remove_task`, `get_archived_tasks` — task CRUD
- `get_habits`, `log_habit` — habit tracking
- `get_trackers`, `log_tracker` — custom tracker CRUD
- `get_journal`, `add_journal_entry` — journal entries
- `get_notes`, `add_note`, `remove_note` — quick notes
- `get_memory` — profile + episodic memory
- `get_reminders` — active + historical reminders
- `get_user_stats` — user dashboard data

**Data Sync:**
- MCP server reads/writes same `state.json` and `bot_memory.db` as bot.py
- Changes via MCP are immediately visible in Telegram and vice versa
- Timezone precedence: SQLite `user_prefs` wins over state.json (same as bot.py's `get_user()`)

---

*Integration audit: 2026-08-01*
