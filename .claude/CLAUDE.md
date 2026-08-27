## Project

**Secretary Bot**

A personal accountability assistant on Telegram that works through relationship rather than metrics. Most productivity tools assume motivation already exists — they hand you a task list and a calendar, and when motivation is absent the list just becomes a record of things to feel guilty about. This bot is built on the opposite premise: it remembers *why* something mattered to you, asks about it with genuine curiosity, and adapts its tone and pressure to what you actually want from it.

Today it serves one user (the author). It may serve more later, but it is not being designed for scale.

**Core Value:** **The bot must know you, and prove it at the moment it matters.** Warmth layered on an amnesiac memory is worse than no warmth at all — a friendly assistant that cannot recall what you told it last week is the specific failure that destroys trust.

### Constraints

- **Tech stack**: Python, single-file `bot.py`, `python-telegram-bot` with APScheduler, hybrid storage across `state.json` and SQLite — new work should follow the existing patterns rather than introduce a parallel architecture.
- **Cost**: keyless users run on Groq's free tier under 30 AI calls/hour/user. Periodic per-user synthesis for the model of the user is a new and different cost shape, and must be designed with that in mind.
- **Deployment**: the bot runs under `nohup` with no process supervisor; only the MCP server has a systemd unit. Anything requiring reliable background execution inherits this fragility.
- **Scale**: one user today. Do not engineer for more.
- **Working style**: ideas are discussed and agreed before any code is written.
- **Privacy**: the bot holds a personal journal and a behavioural model of a real person. Live secrets and user data (`env`, `mcp_remote.env`, `state.json`, `bot_memory.db`) are gitignored and must never enter planning artifacts. Data-at-rest hardening (encryption of journal/observations, stronger key handling) is explicitly deferred — accepted as proportionate to a single-user personal server for this milestone, to be revisited if the user base grows.

## Technology Stack

## Languages

- Python 3.12+ - All application code (`bot.py`, `mcp_server.py`, `tests/test_bot.py`)

## Runtime

- Python 3.12 or later (verified by README)
- pip (Python)
- Lockfile: `requirements.txt` present

## Frameworks

- `python-telegram-bot` 21.6+ - Telegram bot with job queue for scheduled tasks
- `mcp` 1.0.0+ - Model Context Protocol server for Claude integration
- `openai` 1.0.0+ - OpenAI AsyncOpenAI client for LLM calls with function calling support; also used for Groq API via compatible endpoint
- `pytest` - Test runner (referenced in README and test file)
- `pytest-asyncio` - Async test support (referenced in README)
- None detected - single-file bot architecture, no build system

## Key Dependencies

- `python-telegram-bot[job-queue]` 21.6+ - Telegram bot framework with APScheduler job queue integration for persistent, timezone-aware scheduled jobs (check-ins, reminders, alerts)
- `openai` 1.0.0+ - Async OpenAI client, used for both OpenAI and Groq (via OpenAI-compatible API) LLM calls; enables function calling (tool use)
- `cryptography` 42.0.0+ - Fernet symmetric encryption for user API keys stored in SQLite
- `tzdata` - IANA timezone database for zoneinfo module
- `timezonefinder` 6.0.0+ - GPS coordinate → IANA timezone conversion (location-based timezone auto-detection)
- `mcp` 1.0.0+ - Model Context Protocol server library (FastMCP) for Claude Desktop/Code/claude.ai integration

## Configuration

- `TELEGRAM_TOKEN` - Telegram bot token (required)
- `OPENAI_API_KEY` - OpenAI API key for fallback LLM (required)
- `GROQ_API_KEY` - Groq API key for free-tier LLM (optional; if set, keyless users use Groq instead of OpenAI)
- `MASTER_KEY` - Fernet encryption key for API keys stored in SQLite (optional; auto-generated ephemeral if unset, printed to stderr)
- `MY_CHAT_ID` - Single user's chat_id for one-time state.json migration (optional)
- `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` - Google OAuth credentials backing the remote MCP server's own Authorization Server (Milestone B; required for remote mode — replaced the old `MCP_REMOTE_TOKEN` shared-secret `?key=` auth)
- `MCP_REMOTE_DOMAIN` - Public hostname for Host-header DNS-rebinding check (required for remote mode)
- `MCP_REMOTE_HOST` - Local bind address (default `127.0.0.1`)
- `MCP_REMOTE_PORT` - Local bind port (default `8545`)
- `MCP_TRANSPORT` - Set to `"remote"` to enable HTTP server mode (default: stdio for Claude Desktop)
- `BOT_STATE_FILE` - Override `state.json` path (default: relative to `mcp_server.py`)
- `BOT_DB_FILE` - Override `bot_memory.db` path (default: relative to `mcp_server.py`)
- No build configuration; single-file architecture
- Deployment: `nohup python3 bot.py &` (background) or systemd service for MCP server

## Platform Requirements

- Python 3.12+
- pip
- SQLite3 (standard library)
- POSIX-compatible filesystem for state.json atomic writes (`tempfile.mkstemp` + `os.replace`)
- Timezone database via `tzdata` package
- Python 3.12+
- Linux (Amazon Linux 2 verified in `/etc/systemd/`)
- Writable filesystem for `state.json` and `bot_memory.db` (SQLite WAL)
- Telegram API connectivity (outbound HTTPS)
- OpenAI or Groq API connectivity (outbound HTTPS)
- Python 3.12+
- uvicorn ASGI server (imported at runtime in `_run_remote()`, not listed in requirements.txt — must be installed separately)
- Linux with systemd (unit: `secretary-mcp.service`)
- nginx reverse proxy on port 443 with TLS certificates (`/etc/pki/nginx/mcp-sbot.alteon.help.*`)
- Let's Encrypt TLS via certbot (`/etc/letsencrypt/renewal-hooks/deploy/`)
- Local bind to 127.0.0.1:8545 (reverse proxy only, no direct public port)

## External API Endpoints

| Service | Endpoint | Purpose | Auth |
|---------|----------|---------|------|
| Telegram | `api.telegram.org` | Bot polling/updates, sending messages | `TELEGRAM_TOKEN` |
| OpenAI | `https://api.openai.com/v1` | LLM requests, function calling | `OPENAI_API_KEY` (sk-...) |
| Groq | `https://api.groq.com/openai/v1` | LLM requests, free tier | `GROQ_API_KEY` (gsk_...) |

## Conventions

## Naming Patterns

- Python modules use lowercase with underscores: `bot.py`, `mcp_server.py`, `test_bot.py`
- Test files follow pattern: `tests/test_*.py`
- Public functions: `snake_case` (e.g., `get_user()`, `save_state()`, `build_system_prompt()`)
- Private/internal functions: prefixed with single underscore (e.g., `_new_user()`, `_task_text()`, `_db()`)
- Database helpers: `db_*` prefix for SQLite operations (e.g., `db_add_note()`, `db_get_journal()`, `db_store_key()`)
- Tool executor and handler functions: named descriptively without prefixes (e.g., `chat()`, `_execute_tool()`)
- Snake_case throughout (e.g., `state`, `user`, `chat_id`, `task_number`, `tracker_name`)
- Constants: UPPERCASE with underscores (e.g., `RATE_LIMIT`, `DEFAULT_MODEL`, `GROQ_BASE_URL`, `STATE_FILE`)
- Private module-level variables: prefixed with underscore (e.g., `_fernet`, `_app`, `_tf`)
- Dictionary keys: lowercase snake_case (e.g., `chat_id`, `api_key`, `is_rate_limited`)
- Type hints used throughout for function parameters and return types
- Union types: `str | None` syntax (Python 3.10+)
- SQLite Row objects: typed as `sqlite3.Row` for database returns

## Code Style

- No linter or formatter configured (code relies on developer discipline)
- 4-space indentation throughout
- Line length: not strictly enforced but generally kept reasonable (80-100 chars observed)
- Docstrings: used for functions, describing purpose and key parameters
- Comments: explain **why** not **what**; sparse and meaningful
- Not enforced; no `.flake8`, `pyproject.toml`, or `.pylintrc` present
- Code quality relies on manual review and tests

## Import Organization

- None observed; single-file architecture minimizes need for aliases

## Error Handling

- Try/except blocks used for expected errors (API calls, date parsing, timezone validation)
- All exceptions logged with `logger.error()` or `logger.warning()`
- User-facing errors: return error dict from tool functions (e.g., `{"error": "..."}`)
- Chat responses: transform exceptions into friendly messages prefixed with ⚠️

## Logging

- `logger.info()`: important state transitions and tool calls
- `logger.warning()`: recoverable issues and configuration problems
- `logger.error()`: unrecoverable issues that affect user experience

## Comments

- Comment the **why**, not the **what** (code shows what; comments explain purpose)
- Use section headers with dashes: `# ─────────────── rate limiting ───────────────`
- Single-line comments for assumptions and non-obvious logic
- Avoid redundant comments (e.g., don't comment `x = x + 1`)
- Used for public functions and helpers
- Describe purpose, key parameters, return value
- Single-line docstrings for simple functions

## Function Design

- Functions are generally small and focused on one responsibility
- Longest functions are async task handlers and tool dispatchers (necessary for complexity)
- Helper functions are short (5-20 lines typical)
- Use type hints for all parameters and return values
- Avoid large parameter lists; pass dicts when needed
- Named parameters used liberally (e.g., `limit=50`, `include_history=True`)
- Functions return dicts from tool handlers (containing success/error/data)
- Async functions return strings (for chat responses) or None (for side effects)
- SQLite functions return list[Row] or Row or bool
- Parse/validation functions return value or None

## Module Design

- No explicit `__all__` defined; all public (non-underscore-prefixed) functions are importable
- Convention: underscore prefix indicates internal use only
- Not used; single-file architecture (`bot.py`)
- `mcp_server.py` is a separate entry point, not imported by `bot.py`
- `state.json`: user prefs, tasks, history, habits, reminders (written atomically)
- `bot_memory.db`: SQLite (WAL mode, thread-safe) for notes, journal, profile/episodic memory, rate-limit log, job-fire log, encrypted API keys, user-prefs overrides
- Single-file architecture uses functions and dicts, not classes
- User state: dict with known schema (defined in `_new_user()`)
- No ORM or data models; direct SQLite and JSON manipulation

## Architecture

## System Overview

```text

```

```text

```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **Handler Chain** | Route Telegram messages to command/message handlers | `bot.py:4160-4225` |
| **Tool-Call Loop** | Orchestrate LLM + tool invocations (5-round max) | `bot.py:1757-1842` |
| **Tool Dispatcher** | Route tool calls to individual executors | `bot.py:1063-1515` |
| **State Manager** | Load/save user prefs, tasks, habits to JSON | `bot.py:493-548` |
| **Database Helpers** | Read/write notes, journal, memory, rate logs, API keys to SQLite | `bot.py:78-450` |
| **Job Scheduler** | Create and manage per-user recurring jobs (APScheduler) | `bot.py:1857-2215` |
| **MCP Server** | Expose state.json + bot_memory.db as tools to Claude | `mcp_server.py:35-500+` |
| **Test Suite** | Unit tests, LLM sanity checks, tool-use validation | `tests/test_bot.py:1-600+` |

## Pattern Overview

- **Single-file app** (`bot.py` ~4200 lines) — all handlers, tools, state logic in one module
- **Tool-call loop** — LLM generates tool calls → `_execute_tool()` executes → results fed back to LLM (max 5 rounds)
- **Dual storage backend** — split between JSON (state.json, fast but lossy on crash) and SQLite (bot_memory.db, durable, encrypted keys)
- **Hybrid in-memory scheduler** — APScheduler jobs created per user on startup from state, recreated after each restart via `restore_all_jobs()`
- **Parallel MCP access** — `mcp_server.py` reads/writes same two stores directly without going through bot.py, enabling Claude Desktop/Code to access data
- **Telegram handler precedence** — 40+ CommandHandlers (highest priority) → CallbackQueryHandler → custom command catch-all → location/document handlers → free-text handler (lowest)

## Layers

- Purpose: Route incoming messages to appropriate handler based on message type (command, callback, text, location, document)
- Location: `bot.py:4160-4225` (handler registration in `main()`)
- Contains: 40+ `CommandHandler`, 1 `CallbackQueryHandler`, 3 `MessageHandler` catch-alls
- Depends on: python-telegram-bot library, context (Application)
- Used by: Telegram gateway (external)
- Purpose: Handle individual user commands, build context, call `chat()` with prompts
- Location: `bot.py` — individual command functions (`start`, `help_cmd`, `show_tasks`, etc., ~40 functions)
- Contains: Telegram-specific parsing (arg extraction, message building, keyboard markup)
- Depends on: `chat()`, `get_user()`, job scheduling functions
- Used by: Handler chain
- Purpose: Orchestrate multi-turn LLM conversation with function calling
- Location: `chat()` at `bot.py:1757-1842`
- Contains: Message history assembly, LLM API call, tool-call loop (up to 5 rounds), response storage
- Depends on: OpenAI/Groq client, TOOLS list, `_execute_tool()`, `build_system_prompt()`
- Used by: All command handlers and scheduled jobs
- Purpose: Execute individual tool calls from the LLM; single big if/elif chain covering all ~20 tools
- Location: `_execute_tool()` at `bot.py:1063-1515`
- Contains: All tool handlers — timezone, tasks, reminders, trackers, habits, memory, etc.
- Depends on: State manager (`save_state()`), database helpers, `_app` (job scheduler)
- Used by: Tool-call loop in `chat()`
- Purpose: Load/save JSON state atomically; forward-fill missing keys; overlay SQLite prefs
- Location: `bot.py:493-548`
- Contains: `load_state()`, `save_state()`, `get_user()`, module-level `state` dict
- Depends on: Filesystem, SQLite for preference reads
- Used by: All tool handlers and command handlers
- Purpose: Store long-lived, security-sensitive, or frequently-queried data (notes, journal, encrypted API keys, rate logs, reminder history)
- Location: `bot.py:80-450` (helper functions), schema defined in `_init_db()` at `bot.py:88-165`
- Contains: 10+ tables (notes, journal, api_keys, rate_log, profile_memory, episodic_memory, job_log, user_prefs, reminder_log)
- Depends on: SQLite 3 (WAL mode)
- Used by: Tool handlers, rate limiter, reminder history search
- Purpose: Create and manage per-user recurring jobs (check-ins, reminders, alerts, digest, idle nudge)
- Location: `bot.py:1857-2156` (schedule_* functions and `restore_all_jobs()`)
- Contains: APScheduler job definitions, job name registry, job recovery after restarts
- Depends on: APScheduler (via python-telegram-bot), state, database
- Used by: Tool handlers (when updating reminders/check-ins), startup
- Purpose: Expose state.json and bot_memory.db as tools to Claude Desktop/Code without going through bot.py
- Location: `mcp_server.py:1-500+`
- Contains: FastMCP server definition, tools for list_users, tasks, habits, trackers, notes, journal
- Depends on: mcp library, same state.json and bot_memory.db files as bot.py
- Used by: Claude Desktop/Code via stdio or remote HTTP transport

## Data Flow

### Primary Request Path (User sends message)

### Scheduled Job Path (e.g., morning check-in)

### Tool Execution Path (Tool dispatch in `_execute_tool()`)

- **set_timezone** → update both state["users"][cid]["timezone"] and SQLite user_prefs (so SQLite wins on `get_user()`)
- **add_task** → append to state["users"][cid]["tasks"], persist to JSON
- **complete_task** → pop from tasks (or roll forward if recurring), archive, call `_check_milestones()` if needed
- **add_reminder** → create dict with uuid, append to state["users"][cid]["reminders"], persist, schedule job
- **log_tracker** → append {ts, value} to state["users"][cid]["trackers"][tname]["log"], cap at 5000 entries
- **save_memory** → route to SQLite: profile_memory (permanent), episodic_memory (30-day TTL), or journal
- All tool handlers call `save_state(state)` at the end (atomic write via tempfile + os.replace)
- No state changes without persistence — ensures restart safety
- Exceptions halt tool execution and return `{"error": "..."}` without persisting partial state

## Key Abstractions

- Purpose: Represent the outcome of a tool call as JSON for LLM feedback
- Pattern: Always includes at least one of `success: bool`, `error: string`, or data fields
- Examples: `{"success": True, "logged": "85.5kg", "tracker": "weight"}` or `{"error": "Task not found"}`
- Purpose: Canonical per-user object, loaded on demand via `get_user()` and forward-filled with defaults
- Schema: Defined in `_new_user()` at `bot.py:466-490`; contains tasks, history, timezone, trackers, habits, reminders, prefs
- Precedence: state.json is authoritative except for `timezone`, which SQLite overrides if set
- Purpose: Describe tool schema for LLM function calling
- Pattern: `{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}`
- Collection: TOOLS list at `bot.py:641` defines ~20 tools exposed to all LLMs
- Pattern: `{job_type}_{chat_id}_{optional_id}` (e.g., `checkin_morning_12345`, `reminder_12345_uuid4`)
- Used to: Identify and reschedule jobs; dedup reminders after restart

## Entry Points

- Location: `bot.py:4237-4238` (if __name__ == "__main__": main())
- Triggers: Manual startup, systemd/supervisor restart
- Responsibilities: Register handlers, init DB, restore jobs, start polling
- Location: `mcp_server.py:1-12` (shebang, docstring)
- Triggers: `python3 mcp_server.py` (stdio) or via `MCP_TRANSPORT=remote` env var (HTTP)
- Responsibilities: Expose tools, authenticate if remote, serve reads/writes
- Location: `tests/test_bot.py:1-600+`
- Triggers: `pytest tests/test_bot.py -v`
- Responsibilities: Unit tests, LLM sanity checks, tool-use verification

## Architectural Constraints

- **Threading:** Single-threaded event loop (python-telegram-bot's Application uses asyncio). All database calls use `check_same_thread=False`, making connections thread-safe but not concurrent-write-safe. `save_state()` is atomic (tempfile + os.replace).
- **Global state:** Module-level `state` dict (loaded once at import time), module-level `_app` set during `main()` for access from tool executor, module-level `_fernet` for encryption key caching.
- **Circular imports:** None detected. Imports are layered: external libs → module-level setup → state load → function definitions.
- **In-memory jobs only:** All APScheduler jobs are created at startup via `restore_all_jobs()` from state.json and recreated after every restart. Job metadata is not persisted — only state.json user config is. `job_log` SQLite table tracks fire times for recovery (e.g., if a job was supposed to run while bot was down, it's caught up on restart).
- **Rate limiting persistence:** Backed by SQLite `rate_log` table, survives restarts. Checked before every LLM call.
- **Encryption key lifecycle:** `MASTER_KEY` env var (optional) decrypts API keys at runtime. If not set, a temporary key is generated and printed to stderr — plaintext API keys stored during the session become unreadable after restart.

## Anti-Patterns

### Hardcoded Tool Dispatch Chain

```python

```

### State Forward-Fill on Every get_user()

### Tool-Call Loop Hardcodes 5 Rounds

## Error Handling

- **LLM call failures** — catch Exception, check error string for auth/rate/model errors, return user-friendly message at `bot.py:1825-1834`
- **Tool argument parsing** — JSON decode with fallback to empty dict at `bot.py:1812-1814`; individual tools validate their own args and return `{"error": "..."}` without side effects
- **State updates** — wrap in try/except before `save_state()` to prevent partial/corrupted JSON writes
- **Database failures** — let sqlite3 exceptions propagate (they're rare with WAL mode); no catch-and-ignore pattern

## Cross-Cutting Concerns

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
