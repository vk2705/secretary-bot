<!-- refreshed: 2026-08-01 -->
# Architecture

**Analysis Date:** 2026-08-01

## System Overview

```text
┌────────────────────────────────────────────────────────────────────┐
│                    Telegram Bot (python-telegram-bot)              │
│                         `bot.py` main()                             │
├──────────────────────────────────────────────────────────────────┬─┤
│                    Handler Chain & Dispatch                      │ │
│  CommandHandlers → MessageHandlers → CallbackQueryHandlers       │ │
│  (40+ explicit commands + 3 catch-all handlers)                  │ │
│                          ↓                                        │ │
│          handler → async chat(chat_id, message)                  │ │
└────────────────────────────────────────────────────────────────┬──┘
                                │
                                ↓
                ┌───────────────────────────────────────────┐
                │   Tool-Call Loop (up to 5 rounds)        │
                │  `chat()` → LLM → TOOLS → responses      │
                │  `_execute_tool()` dispatcher (if/elif)  │
                └──────────────┬────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
                ↓                             ↓
    ┌───────────────────────┐    ┌──────────────────────────┐
    │  state.json (users)   │    │  bot_memory.db (SQLite)  │
    │  Tasks, habits, etc.  │    │  Notes, journal, memory  │
    │  Conversation history │    │  Encrypted API keys      │
    │  Activity tracking    │    │  Rate limiting logs      │
    │  Reminders            │    │  Reminder history        │
    │  Prefs (timezone TTL) │    │  User prefs (durable)    │
    └───────────────────────┘    └──────────────────────────┘
                │                             │
                └──────────────┬──────────────┘
                               ↓
    ┌──────────────────────────────────────────────────────┐
    │  Job Scheduler (APScheduler in job_queue)            │
    │  In-memory: recreated on restart from state.json     │
    │  Tracks: check-ins, reminders, alerts, digest, idle  │
    └──────────────────────────────────────────────────────┘
```

**Separate MCP Server Layer (`mcp_server.py`):**
```text
┌────────────────────────────────────────────────────────┐
│   MCP Server (Model Context Protocol)                  │
│   Transports: stdio (Claude Desktop/Code) or HTTP      │
│   Direct read/write to state.json & bot_memory.db      │
│   Tools: list_users, get_tasks, add_task, etc.         │
│   Bypasses bot.py runtime — parallel access path       │
└────────────────────────────────────────────────────────┘
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

**Overall:** Single-file synchronous command dispatch with embedded async tool-call loop.

**Key Characteristics:**
- **Single-file app** (`bot.py` ~4200 lines) — all handlers, tools, state logic in one module
- **Tool-call loop** — LLM generates tool calls → `_execute_tool()` executes → results fed back to LLM (max 5 rounds)
- **Dual storage backend** — split between JSON (state.json, fast but lossy on crash) and SQLite (bot_memory.db, durable, encrypted keys)
- **Hybrid in-memory scheduler** — APScheduler jobs created per user on startup from state, recreated after each restart via `restore_all_jobs()`
- **Parallel MCP access** — `mcp_server.py` reads/writes same two stores directly without going through bot.py, enabling Claude Desktop/Code to access data
- **Telegram handler precedence** — 40+ CommandHandlers (highest priority) → CallbackQueryHandler → custom command catch-all → location/document handlers → free-text handler (lowest)

## Layers

**Telegram Handler Layer:**
- Purpose: Route incoming messages to appropriate handler based on message type (command, callback, text, location, document)
- Location: `bot.py:4160-4225` (handler registration in `main()`)
- Contains: 40+ `CommandHandler`, 1 `CallbackQueryHandler`, 3 `MessageHandler` catch-alls
- Depends on: python-telegram-bot library, context (Application)
- Used by: Telegram gateway (external)

**Message Processing Layer (Command Handlers):**
- Purpose: Handle individual user commands, build context, call `chat()` with prompts
- Location: `bot.py` — individual command functions (`start`, `help_cmd`, `show_tasks`, etc., ~40 functions)
- Contains: Telegram-specific parsing (arg extraction, message building, keyboard markup)
- Depends on: `chat()`, `get_user()`, job scheduling functions
- Used by: Handler chain

**LLM & Tool-Call Layer:**
- Purpose: Orchestrate multi-turn LLM conversation with function calling
- Location: `chat()` at `bot.py:1757-1842`
- Contains: Message history assembly, LLM API call, tool-call loop (up to 5 rounds), response storage
- Depends on: OpenAI/Groq client, TOOLS list, `_execute_tool()`, `build_system_prompt()`
- Used by: All command handlers and scheduled jobs

**Tool Dispatch Layer:**
- Purpose: Execute individual tool calls from the LLM; single big if/elif chain covering all ~20 tools
- Location: `_execute_tool()` at `bot.py:1063-1515`
- Contains: All tool handlers — timezone, tasks, reminders, trackers, habits, memory, etc.
- Depends on: State manager (`save_state()`), database helpers, `_app` (job scheduler)
- Used by: Tool-call loop in `chat()`

**State Management Layer:**
- Purpose: Load/save JSON state atomically; forward-fill missing keys; overlay SQLite prefs
- Location: `bot.py:493-548`
- Contains: `load_state()`, `save_state()`, `get_user()`, module-level `state` dict
- Depends on: Filesystem, SQLite for preference reads
- Used by: All tool handlers and command handlers

**Data Persistence Layer (SQLite):**
- Purpose: Store long-lived, security-sensitive, or frequently-queried data (notes, journal, encrypted API keys, rate logs, reminder history)
- Location: `bot.py:80-450` (helper functions), schema defined in `_init_db()` at `bot.py:88-165`
- Contains: 10+ tables (notes, journal, api_keys, rate_log, profile_memory, episodic_memory, job_log, user_prefs, reminder_log)
- Depends on: SQLite 3 (WAL mode)
- Used by: Tool handlers, rate limiter, reminder history search

**Job Scheduler Layer:**
- Purpose: Create and manage per-user recurring jobs (check-ins, reminders, alerts, digest, idle nudge)
- Location: `bot.py:1857-2156` (schedule_* functions and `restore_all_jobs()`)
- Contains: APScheduler job definitions, job name registry, job recovery after restarts
- Depends on: APScheduler (via python-telegram-bot), state, database
- Used by: Tool handlers (when updating reminders/check-ins), startup

**MCP Server (Parallel Layer):**
- Purpose: Expose state.json and bot_memory.db as tools to Claude Desktop/Code without going through bot.py
- Location: `mcp_server.py:1-500+`
- Contains: FastMCP server definition, tools for list_users, tasks, habits, trackers, notes, journal
- Depends on: mcp library, same state.json and bot_memory.db files as bot.py
- Used by: Claude Desktop/Code via stdio or remote HTTP transport

## Data Flow

### Primary Request Path (User sends message)

1. **Message received** (Telegram gateway) → handler selection based on message type
2. **Handler invoked** (e.g., `handle_message()` at `bot.py:3545+` for free text)
3. **Build context** — handler calls `chat(chat_id, user_message)` at `bot.py:1757`
4. **Fetch user** — `chat()` calls `get_user(chat_id)` to load tasks, history, context, prefs
5. **Build system prompt** — `build_system_prompt(user)` injects task list, memory, habit status at `bot.py:1647`
6. **Assemble messages** — combine system prompt + history + new user message
7. **LLM call (round 1)** — `client.chat.completions.create()` at `bot.py:1782`
8. **Check for tool calls** — if `msg.tool_calls`, iterate each; else return reply
9. **Execute tool** — `await _execute_tool(chat_id, tool_name, args)` at `bot.py:1815`
   - Tool updates state via `save_state(state)` or database helpers
   - Returns result dict
10. **Append result to messages** — feed back to LLM as `tool` role message
11. **LLM call (round N)** — repeat until no tool calls (max 5 rounds)
12. **Store exchange** — append user + assistant turns to history at `bot.py:1837-1840`
13. **Send reply** — handler sends text + optional keyboard to user

### Scheduled Job Path (e.g., morning check-in)

1. **APScheduler triggers job** (at user's local check-in time)
2. **Job closure runs** — `_job()` in `schedule_user_checkins()` at `bot.py:1895`
3. **Check quiet hours/mute** — skip if user has muted notifications
4. **Build dynamic prompt** — add stale tracker warnings or pending check-in ack
5. **Call `chat()`** with `touch_activity=False` to prevent marking as user response
6. **Set `pending_checkin` label** — mark that a proactive message was sent
7. **Send with keyboard** — include 4-button inline keyboard for quick response
8. **Log job fire time** — `db_log_job()` for recovery after restart

### Tool Execution Path (Tool dispatch in `_execute_tool()`)

- **set_timezone** → update both state["users"][cid]["timezone"] and SQLite user_prefs (so SQLite wins on `get_user()`)
- **add_task** → append to state["users"][cid]["tasks"], persist to JSON
- **complete_task** → pop from tasks (or roll forward if recurring), archive, call `_check_milestones()` if needed
- **add_reminder** → create dict with uuid, append to state["users"][cid]["reminders"], persist, schedule job
- **log_tracker** → append {ts, value} to state["users"][cid]["trackers"][tname]["log"], cap at 5000 entries
- **save_memory** → route to SQLite: profile_memory (permanent), episodic_memory (30-day TTL), or journal

**State Management:**
- All tool handlers call `save_state(state)` at the end (atomic write via tempfile + os.replace)
- No state changes without persistence — ensures restart safety
- Exceptions halt tool execution and return `{"error": "..."}` without persisting partial state

## Key Abstractions

**Message/Tool Result Dict:**
- Purpose: Represent the outcome of a tool call as JSON for LLM feedback
- Pattern: Always includes at least one of `success: bool`, `error: string`, or data fields
- Examples: `{"success": True, "logged": "85.5kg", "tracker": "weight"}` or `{"error": "Task not found"}`

**User State Dict:**
- Purpose: Canonical per-user object, loaded on demand via `get_user()` and forward-filled with defaults
- Schema: Defined in `_new_user()` at `bot.py:466-490`; contains tasks, history, timezone, trackers, habits, reminders, prefs
- Precedence: state.json is authoritative except for `timezone`, which SQLite overrides if set

**Tool Definition (OpenAI format):**
- Purpose: Describe tool schema for LLM function calling
- Pattern: `{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}`
- Collection: TOOLS list at `bot.py:641` defines ~20 tools exposed to all LLMs

**Job Name Convention:**
- Pattern: `{job_type}_{chat_id}_{optional_id}` (e.g., `checkin_morning_12345`, `reminder_12345_uuid4`)
- Used to: Identify and reschedule jobs; dedup reminders after restart

## Entry Points

**Telegram Bot (main process):**
- Location: `bot.py:4237-4238` (if __name__ == "__main__": main())
- Triggers: Manual startup, systemd/supervisor restart
- Responsibilities: Register handlers, init DB, restore jobs, start polling

**MCP Server (separate process):**
- Location: `mcp_server.py:1-12` (shebang, docstring)
- Triggers: `python3 mcp_server.py` (stdio) or via `MCP_TRANSPORT=remote` env var (HTTP)
- Responsibilities: Expose tools, authenticate if remote, serve reads/writes

**Test Suite (pytest):**
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

**What happens:** `_execute_tool()` is a massive if/elif chain (~450 lines) with one branch per tool name.

**Why it's wrong:** Hard to extend — adding a new tool requires modifying the giant function, no opportunity for composition or reuse. Each branch has its own error handling, validation logic, and state update pattern.

**Do this instead:** Refactor to a tool registry dict (name → handler function). Example at `bot.py:1063` — convert to:
```python
TOOL_HANDLERS = {
    "get_current_time": handle_get_current_time,
    "set_timezone": handle_set_timezone,
    # etc.
}

async def _execute_tool(chat_id: int, name: str, args: dict) -> dict:
    if name not in TOOL_HANDLERS:
        return {"error": f"Unknown tool {name}"}
    return await TOOL_HANDLERS[name](chat_id, args)
```

### State Forward-Fill on Every get_user()

**What happens:** `get_user()` calls `setdefault()` on every key from `_new_user()` every time it's called, even though the user's state dict is already initialized.

**Why it's wrong:** Expensive no-op for most calls; masks versioning — if a new field is added but not in an existing user's state.json, it silently fills it in, making it hard to detect old/new state versions.

**Do this instead:** Forward-fill only at new user creation time. Use a version field in user state or migration functions to handle schema changes explicitly.

### Tool-Call Loop Hardcodes 5 Rounds

**What happens:** `for _round in range(5):` at `bot.py:1780` — if LLM exceeds 5 tool calls, it's silently capped and returns "I got stuck in a loop."

**Why it's wrong:** No way to know if truncation happened; user never knows their request was partially executed. Tool side effects (e.g., task added) may not match LLM's intent.

**Do this instead:** Track and expose loop depth in response context, or set configurable max rounds per user/command type.

## Error Handling

**Strategy:** Defensive — assume external inputs (user messages, Telegram API, LLM responses) are malformed or adversarial.

**Patterns:**
- **LLM call failures** — catch Exception, check error string for auth/rate/model errors, return user-friendly message at `bot.py:1825-1834`
- **Tool argument parsing** — JSON decode with fallback to empty dict at `bot.py:1812-1814`; individual tools validate their own args and return `{"error": "..."}` without side effects
- **State updates** — wrap in try/except before `save_state()` to prevent partial/corrupted JSON writes
- **Database failures** — let sqlite3 exceptions propagate (they're rare with WAL mode); no catch-and-ignore pattern

## Cross-Cutting Concerns

**Logging:** Python stdlib logger at `bot.py:26`, logs to stderr at INFO level by default. Tool execution logged at `bot.py:1816`. Job failures logged at error level (e.g., `bot.py:1943`).

**Validation:** User-supplied data (task text, timezone, reminder time) validated per tool at `_execute_tool()`, not centrally. Tasks and reminders use enum-like strings (recur: "daily"|"weekly"|"monthly") with explicit checks.

**Authentication:** Telegram-native (bot token) — no user login layer. chat_id (numeric Telegram user ID) is the identity. MCP server uses optional query-param key auth for remote HTTP transport.

**Rate Limiting:** SQLite-backed at `bot.py:553-571`, checked before every LLM call in handlers. 30 calls/hour/user, sliding window. Persistent across restarts.

**Timezone Awareness:** All local times converted via ZoneInfo (stdlib). Overlaid from SQLite user_prefs at `get_user()` (survives state.json overwrites). Jobs scheduled with timezone-aware time objects.

---

*Architecture analysis: 2026-08-01*
