---
phase: 01-debug-and-dry-run
plan: 03
subsystem: infra
tags: [telegram-bot, apscheduler, debug-command, pytest]

# Dependency graph
requires: ["01-01"]
provides:
  - "_run_checkin(context, chat_id, label), _run_habit_reminder(context, chat_id), _run_idle_nudge(context, chat_id), _run_weekly_digest(context, chat_id), _run_reminder(context, chat_id, reminder) — module-level runners, each returning a suppression-reason string or None"
  - "DEBUG_JOBS — registry mapping the six fixed job names to their runners"
  - "_debug_fire covering all seven fire targets (six fixed names + reminder <n>)"
affects: [01-04, 01-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Return-contract extension: every extracted runner returns a short lowercase reason string ('quiet hours', 'muted', 'not sunday', 'no recent inactivity', 'nothing due', 'nothing undone', 'no tasks or habits') when an existing guard suppresses it, or None when it did its normal work. run_daily discards callback return values, so the scheduled path is unaffected; the debug path inspects the value to report suppressions by name instead of silence."
    - "Telegram stub fix: InlineKeyboardMarkup/InlineKeyboardButton can no longer be stubbed as the bare MagicMock class in tests/test_bot.py — a lone positional list argument (the button-row list every real call site passes) is interpreted by MagicMock.__init__ as its `spec` parameter and crashes with 'unhashable type: list' deep inside mock internals. Replaced with plain lambda factories in the stub setup at the top of the test file."

key-files:
  created:
    - ".planning/phases/01-debug-and-dry-run/01-03-SUMMARY.md"
  modified:
    - "bot.py — _run_checkin, _run_habit_reminder, _run_idle_nudge, _run_weekly_digest, _run_reminder extracted; _run_deadline_alert given the same return contract; schedule_user_checkins/schedule_user_reminder/schedule_user_alerts reduced to thin run_daily wrappers; DEBUG_JOBS registry and rewritten _debug_fire/_debug_report_fire"
    - "tests/test_bot.py — TestDebugFireAllTargets (28 tests); InlineKeyboardMarkup/InlineKeyboardButton stub fix"

key-decisions:
  - "Extended _run_deadline_alert's return contract too (quiet hours/muted/'nothing due'), even though Task 1's <action> text scoped the extraction to the other five runners. The must_haves truths explicitly require 'nothing due' to be reported by name, and deadline_alert is the only job with that guard shape — the five-runner list in <action> and the seven-guard-string list are only reconcilable if deadline_alert is included. This is a minimal, additive change (new return value only, no side-effect change) and was verified not to break any 01-01/01-02 test."
  - "Rejected the option of mirroring remind_cmd's remove branch exactly for reminder-number bounds checking. remind_cmd's `.pop(idx)` silently accepts negative indices (Python list semantics), so '/remind remove 0' actually removes the *last* reminder rather than erroring — but this plan's own acceptance criteria requires 'reminder 0' to fire nothing. Added an explicit `idx < 0` guard before indexing so debug fire cannot alias the last reminder through negative-index wraparound, while keeping the same (IndexError, ValueError)-style user-facing message."
  - "DEBUG_JOBS is defined in the debug-handlers section immediately before _debug_fire (not physically adjacent to the six runner definitions, which are scattered across the scheduler section by necessity) — Python only requires the six names be defined earlier in module execution order, which they are."

requirements-completed: [DEBUG-01]

coverage:
  - id: D1
    description: "All seven scheduled behaviours can be fired by name through the same runner the scheduler calls, with guard suppressions reported by name instead of silence"
    requirement: "DEBUG-01"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugFireAllTargets (28 tests)"
        status: pass
      - kind: unit
        ref: "python -m pytest tests/test_bot.py -k 'debug_fire or debug_owner_gate' -x -q — 32 collected, all pass"
        status: pass
    human_judgment: true
  - id: D2
    description: "Owner confirms real job firing behaves identically to the scheduled jobs, against the live bot on Telegram"
    requirement: "DEBUG-01"
    verification:
      - kind: manual
        ref: "01-03-PLAN.md Task 3 checkpoint — NOT YET RUN"
        status: pending
    human_judgment: true

duration: n/a
completed: 2026-08-26
status: blocked-on-human-verification
---

# Phase 1 Plan 3: /debug fire — all seven targets Summary

**Tasks 1 and 2 are complete and fully verified by the automated suite: all five remaining job bodies are now module-level runners with a shared suppression-reason return contract, `DEBUG_JOBS` dispatches all six fixed targets, and `reminder <n>` fires by the same 1-based number `/remind list` shows. Task 3 — the plan's blocking manual-verification checkpoint against the live bot — has not been run and requires the owner.**

## Accomplishments
- Extracted `_run_checkin(context, chat_id, label)`, `_run_habit_reminder`, `_run_idle_nudge`, `_run_weekly_digest`, and `_run_reminder(context, chat_id, reminder)` out of their nested `schedule_user_*` closures, byte-identical in behavior, each now returning `None` on normal completion or a short reason string ("quiet hours", "muted", "not sunday", "no recent inactivity", "nothing due", "nothing undone", "no tasks or habits") when an existing guard suppresses it.
- `_run_checkin` absorbed the prompt-construction logic (variety instruction, morning/evening bodies, stale-tracker and unanswered-check-in appends) that previously lived in the `schedule_user_checkins` loop — the scheduler now only builds a job name/time and forwards to the runner.
- `schedule_user_checkins`, `schedule_user_reminder`, and `schedule_user_alerts` are now uniformly thin `run_daily` wrappers over the shared runners; no nested job closure remains in any of the three (pinned by source-assertion greps).
- Also extended `_run_deadline_alert` (from plan 01-01) with the same return contract, since "nothing due" — one of the plan's required suppression-reason strings — only applies to that job; this is a deviation from Task 1's literal file scope, documented above.
- Added the `DEBUG_JOBS` registry (six fixed names → runner) and rewrote `_debug_fire` to dispatch through it, plus a `reminder <n>` branch that resolves the 1-based index the same way `/remind list` numbers reminders — with an explicit `idx < 0` guard so "reminder 0" cannot alias the last reminder the way `/remind remove 0` currently can.
- Added `_debug_report_fire`, a shared reply helper: a runner's suppression-reason string produces a reply naming it; `None` produces a short "✅ Fired \<name\>." confirmation.
- Fixed a latent test-infrastructure bug: `InlineKeyboardMarkup`/`InlineKeyboardButton` were stubbed as the bare `MagicMock` class, which crashes with `unhashable type: 'list'` the moment any code calls them with their real single positional argument (a list of button rows) — no prior test exercised that path. Replaced with lambda factories in the stub setup.
- 28 new tests in `TestDebugFireAllTargets` cover every runner's guard paths, the `touch_activity=False`/`pending_checkin` contract for check-in and weekly digest, the registry's exact six-name shape, all dispatch paths, and reminder-number targeting including the invalid-input cases.

## Files Modified
- `bot.py` — five new runners, `_run_deadline_alert`'s extended return contract, three thin scheduling functions, `DEBUG_JOBS`, `_debug_report_fire`, rewritten `_debug_fire`
- `tests/test_bot.py` — `TestDebugFireAllTargets`, `InlineKeyboardMarkup`/`InlineKeyboardButton` stub fix

## Verification
- `python -m pytest tests/test_bot.py -k "debug_fire or debug_owner_gate" -x -q` — 32 passed (plan required ≥18).
- `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"` — 212 passed, 0 failed.
- Source assertions: 5 new `_run_*` functions; 0 leftover nested `_job`/`_*_job` closures in `schedule_user_checkins`, `schedule_user_reminder`, and `schedule_user_alerts`; `_catchup` still present exactly once (untouched, as required); `DEBUG_JOBS` defined exactly once.
- `python -c "import ast; ast.parse(open('bot.py').read())"` — parses.
- `git diff --stat requirements.txt` — empty.

## Deviations from Plan
- `_run_deadline_alert`'s return contract was extended beyond Task 1's literal five-function scope — see Key Decisions above.
- The reminder-index bounds check deliberately does not mirror `remind_cmd`'s remove-branch negative-index behavior — see Key Decisions above.

## Issues Encountered
- The `InlineKeyboardMarkup`/`InlineKeyboardButton` test stub bug (see Accomplishments) blocked every new check-in and reminder test until fixed; it was a pre-existing gap in the harness, not a regression from this plan's production code.

## Task 3: NOT RUN — requires the owner

Task 3 is a blocking `checkpoint:human-verify` gate that needs a live deploy and real Telegram interaction from the owner's account:
1. Restart the `secretary-bot` systemd service.
2. `/debug fire deadline_alert`, `/debug fire habit_reminder` — confirm wording matches the scheduled jobs (or a suppression reason if nothing is due/undone).
3. `/streak` before and after `/debug fire checkin_morning` — confirm the number is unchanged.
4. `/remind list` then `/debug fire reminder <n>` — confirm the exact reminder text comes back.
5. `/debug fire weekly_digest` on a non-Sunday — confirm a "not sunday" message, not silence.
6. `/debug fire nonsense` — confirm the recognised-target list, no message sent.
7. From a second Telegram account, confirm "Admin only." and nothing reaches the owner's chat.
8. `git status --porcelain` — confirm no new untracked file.

Full steps are in `01-03-PLAN.md` Task 3. **DEBUG-01 is not complete until this runs and the owner replies "approved" (or reports a deviation).**

## Next Phase Readiness
- Plans 01-04 (`/debug clock`) and 01-05 can proceed on the code side; DEBUG-01 itself stays open until Task 3's manual verification completes.
- No blockers to further automated work; Task 3 blocks the *requirement*, not other plans' code.

---
*Phase: 01-debug-and-dry-run*
*Completed (Tasks 1-2): 2026-08-26 — Task 3 pending*
