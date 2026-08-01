# Codebase Concerns

**Analysis Date:** 2026-08-01

## Tech Debt

### Single-File Monolith Architecture

**Issue:** `bot.py` is 4,238 lines with all business logic, handlers, and tool implementations in one file. The `_execute_tool()` function (`bot.py:1063-1510`) implements 26 tool handlers using a large if/elif chain, making it difficult to navigate, test, or extend.

**Files:** `bot.py`

**Impact:** 
- New tool implementations require editing a 447-line function
- Testing individual tools requires mocking the entire handler chain
- Changes to one tool risk breaking others due to shared state handling
- Onboarding new developers requires understanding the entire file's control flow

**Fix approach:** 
- Extract tool implementations into a separate module with one function per tool
- Create a tool registry or dispatcher class to route tool calls
- Move handlers into logical groups (tasks, reminders, trackers, etc.) into separate modules
- Maintain backward compatibility by keeping public APIs unchanged

---

### Non-Atomic State Writes in MCP Server

**Issue:** `mcp_server.py` uses non-atomic writes to `state.json` (lines 67-69):
```python
def _save(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
```

Meanwhile, `bot.py` writes atomically via tempfile + os.replace (`bot.py:514-527`). If both processes write simultaneously (e.g., user modifies state via MCP server while bot receives a message), one write silently overwrites the other.

**Files:** `mcp_server.py:67-69`, `bot.py:514-527`

**Impact:** 
- User data loss if MCP server and bot.py write state.json concurrently
- Severity depends on usage pattern: low if MCP server only reads, high if both read+write

**Current mitigation:** MCP server is typically used by a single Claude instance via stdio transport, making concurrent writes unlikely in practice.

**Fix approach:** 
- Use identical atomic-write pattern in `mcp_server.py._save()` as in `bot.py.save_state()`
- Add file locking (e.g., `fcntl.flock()`) for cross-process synchronization
- Consider moving all state writes through bot.py via IPC or an additional API layer

---

## Deployment & Operational Fragility

### No Process Supervisor for Bot

**Issue:** Bot is deployed as `nohup python3 bot.py &` with no process manager. If the bot crashes, it remains down until manually restarted. The MCP server (`secretary-mcp.service`) has a systemd unit with `Restart=on-failure`, but the bot does not.

**Files:** Deployment documentation (CLAUDE.md), bot startup in `main()` (`bot.py:4160`)

**Impact:** 
- Undetected crashes lead to service unavailability
- No automatic recovery or alerting
- Manual intervention required to restart after any failure

**Fix approach:** 
- Create a systemd unit file for the bot with `Restart=on-failure` and `RestartSec=5`
- Add health checks (e.g., a `/healthz` endpoint or periodic self-test)
- Set up monitoring/alerting (e.g., systemd journal parsing or Prometheus metrics)

---

### Limited Missed Job Catch-Up Window

**Issue:** `restore_all_jobs()` (`bot.py:2156-2211`) only catches up check-ins and alerts missed within a 2-hour window (`catchup_window = timedelta(hours=2)`). Jobs missed beyond 2 hours ago are silently skipped.

**Files:** `bot.py:2161`

**Impact:** 
- If bot is down for >2 hours, users miss their scheduled check-ins with no recovery attempt
- Quiet-hours and mute status are checked on catch-up, so some users might not see any notification
- No user-facing indication that a job was missed

**Current rationale:** 2-hour window prevents bombarding users with stale notifications after long downtime.

**Considerations:** This is likely intentional design (don't surprise users with 20 old check-ins after 8-hour outage), but is worth documenting as a known limitation.

---

## Data Consistency & Race Conditions

### Timezone Stored in Two Places

**Issue:** User timezone is stored in both `state.json` (user["timezone"]) and SQLite (`user_prefs` table). The rationale is that SQLite timezone survives a `state.json` overwrite (e.g., via `/import`), but this introduces dual-source-of-truth complexity.

**Files:** `bot.py:545-547` (overlay logic), `bot.py:1076-1078` (dual write), `bot.py:3082`, `bot.py:3124` (other dual writes)

**Impact:** 
- Developers must remember to call both `save_state()` and `db_set_pref()` when updating timezone
- Stale `state.json` copies can differ from SQLite's canonical value
- `/import` could restore an old timezone if SQLite isn't checked first

**Current mitigation:** `get_user()` always overlays SQLite timezone onto state.json on every call, so the in-memory value is correct. Documentation in CLAUDE.md explains the rationale.

**Fix approach:** 
- Document the pattern clearly in code comments at each dual-write location
- Consider storing timezone exclusively in SQLite and removing it from state.json's schema
- Add a migration script to move all state.json timezones to SQLite on startup

---

## Security Considerations

### Query-Parameter Authentication for Remote MCP Server

**Issue:** MCP server remote transport (`mcp_server.py:562-620`) uses query-param authentication (`?key=<token>`). This token is:
- Visible in nginx logs (if not explicitly redacted)
- Visible in Claude.ai connector URL history
- Not encrypted in transit over TLS (though TLS protects it, it's visible in logs)

**Files:** `mcp_server.py:562-620`, nginx configuration (external)

**Current mitigation:** 
- `_TokenAuthMiddleware` logs only whether key matched, not the token itself (`bot.py` line 616)
- TLS is enforced by nginx
- Auth is application-level, not HTTP Basic/Bearer (which are standard but also loggable)

**Recommendations:** 
- Document that the MCP token should be treated as a secret (like an API key) and rotated if exposed
- Consider using HTTP Basic auth header over query param (standard practice, though equally loggable)
- Ensure nginx doesn't log query strings, or use a redaction filter

---

### API Key Encryption

**Observation (positive):** User API keys are properly encrypted with Fernet before storage in SQLite. The `MASTER_KEY` is stored in `env` (gitignored), and a temporary ephemeral key is generated if missing, with a clear warning on stderr.

**Files:** `bot.py:44-67` (encryption setup), `bot.py:349-371` (encrypt/decrypt functions)

---

## Fragile Areas

### Tool-Call Loop Error Handling

**Issue:** In `chat()` (`bot.py:1757-1842`), if the LLM API fails during a tool-call round, the entire response is lost and a generic error message is returned. Tool execution results (e.g., "task added successfully") are not persisted or shown to the user.

**Files:** `bot.py:1825-1834`

**Impact:** 
- User may not know whether a tool call succeeded before the API failure
- No way to retry or see partial results
- User must re-issue the command to confirm if the action took effect

**Fix approach:** 
- Log tool results to the database even if the LLM response fails
- Return a hybrid message: "Tool X succeeded, but the AI response failed. Try again."
- Persist tool-call history separately for debugging

---

### Job Management Complexity

**Issue:** APScheduler jobs are recreated from state on every restart (`restore_all_jobs()`), but job names must be unique and carefully coordinated across multiple scheduling functions (`schedule_user_checkins`, `schedule_user_alerts`, `schedule_user_reminder`, etc.). Job cancellation relies on `get_jobs_by_name()` and `schedule_removal()`, which is implicit (jobs aren't removed immediately).

**Files:** `bot.py:1857-2150` (multiple scheduling functions)

**Impact:** 
- Risk of duplicate jobs if restart happens during job creation
- Job names are constructed by hand (e.g., `f"checkin_{label}_{chat_id}"`) with no type safety
- Job removal is deferred (not immediate), potentially causing stale jobs to fire

**Fix approach:** 
- Use a job registry class with explicit add/remove/list methods
- Validate job names at startup to prevent duplicates
- Add a cleanup step to remove any orphaned jobs on startup

---

## Scaling Limits

### History and Tracker Log Caps

**Issue:** Conversation history is capped at 20 pairs (40 messages) per user (`MAX_HISTORY = 20`, `bot.py:42`). Tracker logs are capped at 5,000 entries per tracker (`bot.py:1222`). These are enforced by simple list truncation on each append.

**Files:** `bot.py:1839-1840` (history), `bot.py:1222-1223` (tracker log)

**Impact:** 
- Long conversations lose context after 20 pairs; important earlier messages are discarded
- Heavy tracker users (e.g., daily weight logs for years) lose old data
- No warning to user when truncation occurs
- No configurable limits per user

**Considerations:** These limits prevent runaway context costs and database bloat, which is intentional. However, users may not realize old data is being dropped.

**Fix approach:** 
- Add user-configurable retention policies (`/sethistorylimit`, etc.)
- Log truncations to the database for auditing
- Provide a summary of discarded history before truncating (e.g., "oldest 3 messages dropped to stay within 20-message limit")

---

## Test Coverage Gaps

### Limited Integration Testing for Concurrent Access

**Issue:** The test suite (`tests/test_bot.py`) isolates each test with a fresh SQLite temp file and redirects `state.json` writes to a temp file. However, there are no tests for concurrent access patterns (e.g., bot.py and mcp_server.py writing simultaneously).

**Files:** `tests/test_bot.py` (test infrastructure)

**Impact:** 
- Race conditions in shared state access are not detected
- State consistency bugs may only appear in production under load

**Fix approach:** 
- Add multi-process tests that simulate concurrent bot and MCP server operations
- Test state.json write races by interleaving saves from two processes
- Verify no data loss occurs during simultaneous writes

---

### Missing Tool-Call Error Scenarios

**Issue:** The tool-call loop in `chat()` has error handling for API failures, but limited testing of edge cases like:
- Malformed tool arguments (JSON decode errors are silently caught at `bot.py:1812-1814`)
- Tool function raising an exception mid-execution
- Tool returning invalid JSON-serializable results

**Files:** `bot.py:1810-1821` (tool execution), `tests/test_bot.py` (tests)

**Impact:** 
- Silent failures in tool execution could mask bugs
- Malformed results might corrupt state.json or user data

**Fix approach:** 
- Add logging for tool execution failures with full stack traces
- Add tests for each tool with invalid/edge-case inputs
- Validate tool results before appending to messages

---

## Missing Critical Features

### No Admin Dashboard or Monitoring

**Issue:** Bot administrators have `/adminstats` for user counts and usage, but no real-time monitoring of:
- Job execution failures (missed check-ins, scheduled alerts)
- API rate limit usage and errors
- Database size and performance
- Memory usage or long-running operations

**Files:** `bot.py:3945` (admin_stats command)

**Impact:** 
- Operational issues (e.g., a user's jobs crashing) go undetected
- No visibility into which users are having problems
- Difficult to debug production issues

**Fix approach:** 
- Export Prometheus metrics for job success/failure, API calls, database queries
- Add a `/debug` admin command showing last N errors and job statuses
- Log all job executions with timestamp and status to a dedicated table

---

## Known Limitations

### No Automatic Timezone Update on Location Share

**Issue:** When a user shares a location via Telegram, `handle_location()` calls `get_timezone_from_location()` which is called via tool, but the timezone is only set if the user's current timezone is "UTC" or invalid. If they already have a timezone set, location sharing doesn't update it.

**Files:** `bot.py:3119-3124` (location handler includes logic to avoid overwriting existing timezone if valid)

**Impact:** 
- Users can't update timezone by sharing location after initial setup
- Requires manual `/settimezone` command for timezone changes

---

### No Duplicate Task Detection

**Issue:** Adding a task with `/addtask` or via tool call does not check for duplicates. A user can accidentally add the same task multiple times.

**Files:** `bot.py:1151-1170` (add_task tool), and handlers

**Impact:** 
- Task list can become cluttered with duplicates
- User experience is worse than a "task already exists" confirmation

---

### Limited Error Messages for Invalid Inputs

**Issue:** Many commands have minimal validation of user input. For example:
- `/extend <n> <days>` does not validate that `n` is a number until the string is converted
- Tracker names cannot contain special characters, but the error message is generic

**Files:** Multiple handlers throughout `bot.py`

**Impact:** 
- Poor UX when users make typos or syntax errors
- Error messages don't suggest the correct format

**Fix approach:** 
- Add schema validation for command arguments (e.g., using Pydantic or similar)
- Provide examples in error messages
- Accept multiple input formats (e.g., `/extend 3 days` and `/extend task 3` both work)

---

*Concerns audit: 2026-08-01*
