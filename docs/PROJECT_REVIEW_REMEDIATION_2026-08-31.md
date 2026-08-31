# Project Review Remediation - 2026-08-31

## Purpose

This document records the implementation work performed after the full-project
review at commit `bb91c44`. The review covered the Telegram runtime, JSON and
SQLite persistence, reminders and scheduled jobs, MCP OAuth and user isolation,
administration, backups, the local console, dependencies, documentation, and
both test modules.

The work prioritized data loss, privacy, authentication, and user-visible
correctness. It intentionally did not attempt an unbounded rewrite of the
single-file application.

## Outcome Summary

| Review area | Disposition |
|---|---|
| Cross-process JSON writes | Partially mitigated; full single-writer migration deferred |
| WAL-safe backups | Fixed |
| Account reset privacy | Fixed |
| Reminder lifecycle and export/import | Fixed |
| OAuth code and refresh replay race | Fixed |
| AI rate accounting and `/limit` | Fixed |
| Sensitive tool/key logging | Fixed |
| Direct task index `0` | Fixed |
| Timezone confirmation on `/subscribe` | Fixed |
| User-local calendar dates | Deferred as a dedicated migration |
| Snooze token ownership | Fixed |
| Service restart failure reporting | Fixed |
| Console sandbox isolation | Fixed |
| Dependencies and operational docs | Fixed |

## Implemented Changes

### 1. Task command index validation

Direct task commands previously converted a displayed task number with
`int(value) - 1`. Number `0` therefore became Python index `-1`, mutating the
last task.

A shared `_task_index()` helper now validates the complete 1-based range before
returning an index. It is used by:

- `/removetask`
- `/donetask`
- `/prioritize`
- `/duedate`
- `/extend`
- `/swap`

Parameterized regression coverage proves that `0` leaves the task list
unchanged for all six commands.

### 2. Reminder lifecycle consolidation

One-shot reminders from different entry points had incompatible durability.
The LLM tool persisted clock-time reminders, while direct commands and relative
tool reminders existed only in APScheduler memory.

The new `_persist_one_shot_reminder()` path gives all one-shots:

- A stable UUID
- An absolute UTC `fire_at`
- Persistence in `state.json`
- Reminder history in SQLite
- Scheduling through `schedule_user_reminder()`

One-shots are removed only after `_run_reminder()` confirms successful
delivery. Quiet-hours suppression, mute, provider failure, and Telegram failure
retain the reminder and retry it after 30 minutes. A reminder overdue after a
restart is scheduled for immediate delivery instead of being silently deleted.

Annual reminders now receive their own daily scheduler entry at their requested
local time. The callback checks `MM-DD` before delivery. The old fixed 09:00
deadline-alert delivery was removed to prevent duplicate sends.

Exports now preserve the complete reminder object, including `id`, `once`,
`annual`, `date`, `fire_at`, and `reason`. Imports validate one-shot `fire_at`,
cancel jobs for reminders being replaced, mark old reminder history removed,
and schedule every imported reminder through the canonical scheduler.

### 3. Account reset privacy

`/reset` previously cleared JSON state while leaving notes, journals, profile
memory, and episodic memory in SQLite. Those records could still be searched or
included in prompts.

`db_reset_user_data()` now deletes the user's rows from personal-data,
scheduling, rate, snooze, and MCP session tables in one SQLite transaction. It
retains only:

- The encrypted API key
- `timezone`
- `timezone_confirmed`

This matches the reset command's stated retention policy. MCP access tokens and
identity bindings are removed, so a reset also revokes linked remote access.

### 4. AI call accounting and privacy-safe logs

Rate limiting now occurs immediately before every
`client.chat.completions.create()` request, including later tool rounds and the
fallback retry without tools. Scheduled AI work passes through the same
boundary. Legacy handler-level reservations were removed to avoid counting one
request twice.

`/limit` now reads the SQLite `rate_log` table through `rate_limit_status()`;
the removed `_rate_log` in-memory variable is no longer referenced.

Tool logging no longer writes arguments or results to journald. Logs contain
only the tool name and success/error status. When `MASTER_KEY` is absent, the
development fallback still warns that the key is ephemeral but never prints
the generated key material.

### 5. OAuth credential consumption

Authorization codes and refresh tokens were previously loaded and deleted in
separate transactions. Concurrent exchanges could both load the same value and
mint separate token pairs.

The provider now performs a conditional `DELETE` inside `BEGIN IMMEDIATE` and
requires exactly one affected row. A losing replay receives OAuth
`invalid_grant`. The condition also verifies authorization-code client binding
and expiry. Tests cover second-use rejection for both authorization codes and
rotating refresh tokens.

### 6. Snooze ownership

Snooze rows already stored `chat_id`, but retrieval ignored it and scheduled
against the callback sender. `db_take_snooze()` now requires the authenticated
Telegram user ID in both its lookup and deletion. Another user cannot consume
the token, and the obsolete unbound in-memory fallback is no longer used.

### 7. WAL-safe backup and push retry

Plainly copying a live SQLite main file is not a valid backup in WAL mode.
Committed rows may exist only in the WAL at copy time.

The new `sqlite_snapshot.py` utility uses Python's SQLite online backup API,
runs `PRAGMA integrity_check` against the snapshot, and atomically replaces the
destination only after verification. `backup.sh` invokes this utility instead
of copying `bot_memory.db`.

The script now checks whether the backup branch is ahead of `origin/main` even
when no source data changed. A commit whose previous push failed is therefore
retried on the next timer run.

### 8. Console and service operations

`bot.py` now honors `BOT_STATE_FILE` and `BOT_DB_FILE`. The console sets those
variables before importing `bot`, so production files are not opened during
module initialization.

Console database seeding uses SQLite online backup rather than copying the live
main file. Sandbox directories are forced to mode `0700`; symlinks are refused;
arbitrary directories require a marker before recursive deletion. The known
default sandbox remains backward compatible.

`admin.py` now exits nonzero if systemd accepts `start` but the bot is not active
after the startup check. Post-commit automation can therefore detect a failed
deployment.

### 9. Dependency and documentation hygiene

- MCP is constrained to the tested 1.x API line.
- Direct Starlette and Uvicorn runtime dependencies are declared.
- Pytest's `sanity` and `nl` markers are registered.
- README production instructions use systemd rather than `nohup`.
- README remote MCP instructions describe `/link` plus OAuth rather than the
  removed query-string token.
- README security notes describe Fernet-encrypted SQLite API keys.
- Test commands include both bot and MCP tests.
- Backup documentation describes online SQLite snapshots and integrity checks.

## Validation Evidence

The following checks were executed after implementation:

```text
Focused task index tests:             6 passed
Focused reminder lifecycle tests:     9 passed
Reminder export/import tests:         8 passed
Privacy and rate tests:               8 passed
OAuth storage/exchange tests:         16 passed
Snooze ownership tests:               5 passed
Complete MCP suite:                   56 passed
Complete offline project suite:       477 passed, 31 deselected
```

The 31 deselected tests are explicitly marked `sanity` or `nl` and make real
provider API calls. They were not run to avoid unnecessary external calls and
cost. The suite still emits `datetime.utcnow()` deprecation warnings; these are
tracked below.

Additional validation:

- A WAL-active temporary database was snapshotted.
- The snapshot returned `PRAGMA integrity_check = ok`.
- A row committed while WAL mode was active was present in the snapshot.
- `bash -n` passed for `backup.sh`, `start_bot.sh`, and the post-commit hook.
- `git diff --check` passed.
- VS Code diagnostics reported no errors in all modified Python files.

## Deferred Work

### A. Single writer for mutable user state

MCP JSON saves are now atomic, so readers cannot observe a partially written
file. This does **not** solve stale-snapshot overwrite: the bot keeps a
process-long in-memory state object while MCP performs independent read-modify-
write operations. Two valid atomic writes can still overwrite one another.

File locking alone is insufficient because it serializes writes but does not
merge stale same-user objects. The recommended durable solution is:

1. Move tasks, reminders, habits, trackers, and remaining mutable preferences
   into SQLite.
2. Make SQLite transactions the only mutation boundary.
3. Have Telegram, MCP, and admin paths call the same domain functions.
4. Keep `state.json` temporarily as a migration/export compatibility format.
5. Add a concurrency test where bot and MCP mutate the same user at once.

Until that migration lands, avoid simultaneous MCP and Telegram mutations of
JSON-backed data. This is the highest-priority remaining risk.

### B. User-local calendar migration

Timezone confirmation is now required by `/subscribe`, closing the immediate
UTC scheduling error. However, several durable calendar records still derive
their date from server-local `date.today()` or UTC, including activity days,
habit completions, recurring task dates, and daily focus.

Changing those semantics touches historical records, debug-clock guarantees,
MCP parity, and many tests. It should be handled as one explicit migration:

1. Define a canonical `user_today(user)` helper using `ZoneInfo`.
2. Route all user-facing calendar writes through it in both bot and MCP paths.
3. Keep stored event timestamps in UTC.
4. Decide whether existing date-only records need migration or only future
   writes change semantics.
5. Test positive and negative UTC offsets across local midnight and DST.

### C. `datetime.utcnow()` deprecations

The project still stores many UTC-naive ISO timestamps. A piecemeal replacement
could break comparisons between aware and naive values. Migrate each timestamp
family deliberately to aware UTC, with compatibility parsing for existing rows
and JSON values.

### D. Identity relinking and revoke-all flow

Account reset now revokes the user's MCP credentials. A dedicated unlink or
relink operation should additionally revoke all credentials attached to the
old identity binding in the same transaction and expose that action without
requiring a full account reset.

## Deployment Notes

These changes modify `bot.py`, `mcp_server.py`, dependencies, and backup
behavior. Deployment should therefore:

1. Install the updated requirements in the existing virtual environment.
2. Restart both `secretary-bot.service` and `secretary-mcp.service`.
3. Run `backup.sh` once manually and confirm the private backup repository is
   not ahead of its remote afterward.
4. Confirm `secretary-bot-backup.timer` remains active.
5. Exercise one disposable reminder through create, restart, delivery, and
   snooze before relying on the changed retry behavior.

No live state, journal content, credentials, or private database values were
read into or written to this document.