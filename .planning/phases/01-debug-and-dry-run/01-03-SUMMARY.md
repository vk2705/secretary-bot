---
phase: 01-debug-and-dry-run
plan: 03
subsystem: infra
tags: [telegram-bot, debug-surface, scheduler, closure-extraction, pytest]

# Dependency graph
requires:
  - phase: 01-01
    provides: "debug_cmd owner gate, fire/clock/prompt subcommand dispatch skeleton, _run_deadline_alert extraction shape, _debug_update/_debug_context/as_owner test helpers"
provides:
  - "_run_checkin(context, chat_id, label), _run_habit_reminder(context, chat_id), _run_idle_nudge(context, chat_id), _run_weekly_digest(context, chat_id), _run_reminder(context, chat_id, reminder) — module-level runners for the five remaining scheduled behaviours"
  - "DEBUG_JOBS — job-name to runner registry"
  - "/debug fire covering all seven scheduled targets: checkin_morning, checkin_evening, deadline_alert, habit_reminder, idle_nudge, weekly_digest, reminder <n>"
  - "Shared suppression-reason return contract (None on normal completion, a short reason string when a guard self-suppresses) across all six runners"
affects: [01-04, 01-05]

# Actuals (#2632)
actuals:
  tokens: 88000
  tasks: 3
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Closure-to-module-level extraction (established in 01-01) applied five more times: each schedule_user_* function keeps only a thin run_daily wrapper capturing chat_id as a default argument; the body moves out unchanged."
    - "Suppression-reason return contract: a runner returns None on normal completion or a short lowercase reason string (quiet hours, muted, not sunday, no recent inactivity, nothing due, nothing undone, no tasks or habits) when an existing guard self-suppresses it. run_daily discards return values, so the scheduled path is unaffected; the debug path surfaces the reason instead of silence."
    - "Reminder targeting mirrors /remind list's 1-based numbering exactly (D-P3), never the UUID; out-of-range, zero, and non-numeric input all fire nothing."

key-files:
  created:
    - ".planning/phases/01-debug-and-dry-run/deferred-items.md — logs a pre-existing bug found but not fixed (see Deferred Findings below)"
  modified:
    - "bot.py — five new module-level _run_* functions, DEBUG_JOBS registry, _debug_fire expanded to all seven targets"
    - "tests/test_bot.py — TestDebugFire expanded with suppression-reason, activity-isolation, and reminder-targeting coverage"

key-decisions:
  - "Guard suppressions are reported to the owner by name rather than silently swallowed, so a debug fire that hits quiet hours/mute/Sunday-gate/idle-threshold is never mistaken for a broken command."
  - "The _catchup closure inside restore_all_jobs was deliberately left untouched (explicit out-of-scope note in the plan) — it has distinct missed-check-in wording and is a startup-only path, not a fireable debug target."
  - "A pre-existing bug in remind_cmd's remove branch (/remind remove 0 silently removes the last reminder due to Python's negative-index wraparound, T-1-10-class) was found but not fixed — out of this plan's files_modified boundary. Logged in deferred-items.md for future work; _debug_fire's own reminder targeting was written with an explicit n < 1 guard to avoid the same bug."

patterns-established:
  - "checkpoint:human-verify tasks that require live Telegram interaction cannot be completed inside an isolated worktree — the orchestrator restarts the real systemd service from the merged main tree and relays the owner's verification back into this SUMMARY."

requirements-completed: [DEBUG-01]

coverage:
  - id: D1
    description: "All seven scheduled behaviours can be fired by name through /debug fire and produce the real message with the real side effects"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugFire (expanded to all seven targets)"
        status: pass
      - kind: manual
        ref: "Task 3 steps 2, 3, 5 — live /debug fire deadline_alert, habit_reminder, reminder <n> against the deployed bot"
        status: pass
    human_judgment: true
  - id: D2
    description: "No job body exists in two places; every schedule_user_* function is a thin wrapper calling the same _run_* function the debug path calls"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "source-assertion greps: 5 new _run_* functions, 0 leftover nested job closures in any scheduler, _catchup untouched"
        status: pass
    human_judgment: false
  - id: D3
    description: "A debug-fired check-in or weekly digest does not mark the day active and does not clear pending_checkin"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py — touch_activity=False assertion on the patched chat() call for _run_checkin and _run_weekly_digest"
        status: pass
      - kind: manual
        ref: "Task 3 step 4 — /streak unchanged across a live /debug fire checkin_morning"
        status: pass
    human_judgment: true
  - id: D4
    description: "Reminders are targeted by the same 1-based number /remind list shows, never the UUID; invalid input fires nothing"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py — reminder 0/99/abc/bare all await no runner; reminder 2 fires index 1"
        status: pass
      - kind: manual
        ref: "Task 3 step 5 — live /remind list then /debug fire reminder <n> returns that exact reminder"
        status: pass
    human_judgment: true
  - id: D5
    description: "Guard suppressions (quiet hours, mute, not-Sunday, not-idle-long-enough, nothing-due, nothing-undone) are reported to the owner by name instead of silence"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py — reason-string-to-reply-text assertions per runner"
        status: pass
      - kind: manual
        ref: "Task 3 step 6 — live /debug fire weekly_digest on a non-Sunday names the Sunday gate"
        status: pass
    human_judgment: true
  - id: D6
    description: "Firing an unknown job name lists the recognised names and fires nothing"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py — unrecognised name awaits no runner, reply contains every registry key"
        status: pass
      - kind: manual
        ref: "Task 3 step 7 — live /debug fire nonsense"
        status: pass
    human_judgment: true
  - id: D7
    description: "The owner gate rejects a non-owner for the fire branch, live, on the real bot"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugOwnerGate (inherited from 01-01, parameterised over fire)"
        status: pass
      - kind: manual
        ref: "Task 3 step 8 — second Telegram account gets 'Admin only.', nothing sent to owner"
        status: pass
    human_judgment: true

duration: ~35min (2 automated tasks) + owner verification session
completed: 2026-08-07
status: complete
---

# Phase 1 Plan 3: Debug Fire — All Seven Scheduled Behaviours Summary

**`/debug fire` now covers all seven scheduled behaviours through the exact module-level runner each `schedule_user_*` job calls, with guard suppressions reported by name instead of silence, reminder targeting matching `/remind list`'s 1-based numbering, and the owner has confirmed all nine live-verification steps against the deployed bot.**

## Performance

- **Duration:** ~35 min automated (Tasks 1-2) + a live verification session (Task 3)
- **Tasks:** 3 (2 automated, 1 checkpoint:human-verify)
- **Files modified:** bot.py, tests/test_bot.py; deferred-items.md created

## Accomplishments
- Extracted five remaining nested job closures (`schedule_user_checkins`'s `_job`, `schedule_user_reminder`'s `_job`, and the habit/idle/weekly-digest closures inside `schedule_user_alerts`) to module-level `_run_checkin`, `_run_habit_reminder`, `_run_idle_nudge`, `_run_weekly_digest`, `_run_reminder`, matching plan 01-01's extraction shape exactly.
- Gave every runner a shared suppression-reason return contract: `None` on normal completion, a short reason string (quiet hours, muted, not sunday, no recent inactivity, nothing due, nothing undone, no tasks or habits) when an existing guard self-suppresses — `run_daily` discards the return value so the scheduled path is unaffected, while the debug path surfaces the reason.
- Added `DEBUG_JOBS`, a name→runner registry for the six fixed targets, and expanded `_debug_fire` to dispatch all seven (`reminder <n>` handled separately, following `/remind list`'s 1-based numbering and `remind_cmd`'s remove-branch error handling, D-P3).
- Preserved `touch_activity=False` through the move (RESEARCH.md Pitfall 3) — pinned by a test asserting the keyword received by a patched `chat()`, then re-confirmed live in Task 3 step 4 (`/streak` unchanged across a debug-fired morning check-in).
- Deliberately left `restore_all_jobs`'s `_catchup` closure untouched — distinct wording, startup-only path, explicitly out of scope per the plan.
- **DEBUG-01 is now complete in code and live-verified.**

## Task Commits

1. **Task 1: Extract the remaining five job bodies to module-level runners** — `08f72de` (feat)
2. **Task 2: Complete /debug fire across all seven targets** — `c09fa0d` (feat, includes deferred-items.md)
3. **Task 3: Owner verifies real job firing against the live bot** — no code commit; verification-only, recorded in this SUMMARY (see below)

## Files Created/Modified
- `bot.py` — five new `_run_*` functions, `DEBUG_JOBS` registry, `_debug_fire` expanded to all seven targets
- `tests/test_bot.py` — `TestDebugFire` expanded to 67 total tests across `debug_fire`/`debug_owner_gate` selectors
- `.planning/phases/01-debug-and-dry-run/deferred-items.md` — created, logs a pre-existing bug found out of scope

## Decisions Made
- Suppression reasons are surfaced to the owner by name (not swallowed) so a guarded debug fire is never mistaken for a broken command — this is the plan's one genuinely new design element beyond replicating 01-01's extraction shape.
- `_debug_fire`'s reminder branch was written with an explicit `if n < 1: raise ValueError` guard, avoiding a bug found (but not fixed, out of scope) in `remind_cmd`'s existing `remove` branch — see Deferred Findings.

## Deviations from Plan
None in Tasks 1-2 — executed as written. Task 3's completion mechanics deviated procedurally, not substantively: the executor agent that reached the `checkpoint:human-verify` gate was running in an isolated git worktree; the orchestrator merged Tasks 1-2 to `master` via fast-forward, deleted the now-stale worktree, and therefore closed out Task 3 directly as the orchestrator (restarting the live `secretary-bot` systemd service and relaying the owner's step-by-step verification) rather than resuming the original executor. No plan content, task ordering, or acceptance criteria were changed.

## Issues Encountered
None during automated execution. See Deferred Findings for a pre-existing bug discovered incidentally.

## Deferred Findings
A pre-existing bug in `remind_cmd`'s `remove` branch: `/remind remove 0` computes a negative Python index and silently removes the **last** reminder instead of rejecting the input (same bug class as this plan's T-1-10 threat, which `_debug_fire`'s reminder targeting explicitly guards against). Not fixed here — out of this plan's `files_modified` boundary. Logged in `.planning/phases/01-debug-and-dry-run/deferred-items.md` for future work.

## User Setup Required
None beyond the live verification already performed (owner restarted `secretary-bot` via `sudo systemctl restart secretary-bot` and ran the nine-step check on Telegram; all steps confirmed as expected — approved 2026-08-07).

## Next Phase Readiness
- All six `_run_*` runners, `DEBUG_JOBS`, and the completed `_debug_fire` are available for plan 01-04 (`/debug clock`) and plan 01-05 (ambient clock breadth) to build on.
- `debug_cmd`'s `clock` branch remains a stub ("not implemented yet") — plan 01-04 replaces it.
- DEBUG-01 requirement fully closed; no blockers for 01-04/01-05.

---
*Phase: 01-debug-and-dry-run*
*Completed: 2026-08-07*

## Self-Check: PASSED

- FOUND: bot.py
- FOUND: tests/test_bot.py
- FOUND: .planning/phases/01-debug-and-dry-run/deferred-items.md
- FOUND: commit 08f72de (Task 1)
- FOUND: commit c09fa0d (Task 2)
- FOUND: Task 3 owner approval recorded above (verification-only, no code commit)
