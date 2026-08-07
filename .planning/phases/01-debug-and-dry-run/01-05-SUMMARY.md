---
phase: 01-debug-and-dry-run
plan: 05
subsystem: testing
tags: [debug-tooling, ambient-clock, zoneinfo, python-telegram-bot]

# Dependency graph
requires:
  - phase: 01-debug-and-dry-run (01-03)
    provides: /debug fire covering all seven scheduled behaviours via extracted _run_* runners
  - phase: 01-debug-and-dry-run (01-04)
    provides: debug_clock storage, _debug_now(user)/_now(tz,user)/_today(user)/_utcnow(user) helpers, /debug clock command
provides:
  - "_get_streak, _is_muted, _is_quiet_now routed through the override (no signature change, body substitution only)"
  - "_format_task_line(task, idx, user=None), _habit_streak(completions, user=None), _habit_summary_lines(habits, user=None) -- signature-changed, additive, exact no-op with user=None"
  - "Every existing caller of the three re-parameterised helpers updated to pass its already-held user dict (tool dispatcher, job runners, habit command, stats command, build_system_prompt, reflect_cmd, insights_cmd)"
  - "_run_checkin, _run_deadline_alert, _run_habit_reminder, _run_idle_nudge, _run_weekly_digest, the get_current_time tool branch, and time_cmd routed through the override"
  - "TestDebugClockAmbient (32 tests, all test_debug_clock_ambient_* prefixed) covering the ambient success criteria and the durable-record guard"
affects: []

# Actuals (#2632)
actuals:
  tokens: 9100
  tasks: 3
  commits: 3

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Additive optional-parameter refactor: a function gains one trailing `user=None` parameter whose default reproduces the exact pre-refactor return value, so an un-updated caller is safe rather than broken"
    - "Read/write clock boundary (D-P7): every compare-or-display site routes through the override; every durable-write site (habit completions, journal/note/tracker timestamps, activity_days, task due/completed_at, job-fire log, mute expiry) stays on the real wall clock unconditionally, verified by a dedicated durable-record guard rather than left as prose"

key-files:
  created: []
  modified:
    - bot.py
    - tests/test_bot.py

key-decisions:
  - "All ~16 Table A call sites routed through _now/_today/_utcnow exactly as enumerated; all Table B sites (durable writes, the two scheduling-arithmetic exclusions, today's-focus comparisons) left untouched and pinned by source-assertion checks"
  - "TestDebugClockAmbient's test methods are prefixed test_debug_clock_ambient_* (not just class-named) because pytest's -k substring match does not bridge PascalCase class names to the plan's required snake_case selector -- discovered when the literal acceptance-criteria selector returned 0 collected tests against the class-name-only version"
  - "The plan's Task 1 exhaustiveness check (`sed -n '/^def _is_muted/,/^def _tasks_for_prompt/p' bot.py | grep -c ...`) cannot literally return 0: its sed range accidentally spans the plan-01-04 _debug_now/_now/_today/_utcnow block (physically sandwiched between _is_muted and _format_task_line), whose real-clock fallback bodies and docstrings are the routing mechanism itself, not remaining call sites. Verified by re-scoping the check to the two named regions (_is_muted..._debug_now, and _format_task_line..._tasks_for_prompt) independently, both of which return 0 -- documented here per CLAUDE.md's Verify Completeness Claims rather than silently treated as passing"

patterns-established:
  - "Job-runner and tool-branch time reads always resolve through the user dict already in local scope (u/user_now/user), never a fresh get_user() call or a bare stdlib call -- established across all five _run_* runners and the two _execute_tool branches touched here"

requirements-completed: [DEBUG-02]

coverage:
  - id: D1
    description: "_format_task_line/_habit_streak/_habit_summary_lines gain an optional user=None parameter that is an exact no-op of the pre-refactor function for every existing caller"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_format_task_line_no_override_matches_fixed_expectation"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_get_streak_no_override_matches_fixed_expectation"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_habit_streak_no_override_matches_fixed_expectation"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_habit_summary_lines_no_override_matches_fixed_expectation"
        status: pass
      - kind: other
        ref: "grep -cE '^def (_format_task_line|_habit_streak|_habit_summary_lines)\\(.*user=None\\)' bot.py -> 3"
        status: pass
    human_judgment: false
  - id: D2
    description: "A simulated clock makes the deadline badge, quiet-hours window, mute evaluation, activity/habit streaks, /time, /mystats, the current-time and habits tools, and the dumped system prompt agree on the simulated date"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_format_task_line_override_moves_due_today_badge"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_is_quiet_now_override_activates_inactive_window"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_is_muted_override_before_and_after_expiry"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_get_current_time_tool_reports_simulated_date"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_time_cmd_reports_simulated_date"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_habit_cmd_stats_last_seven_days_uses_simulated_date"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_my_stats_seven_day_chart_uses_simulated_date"
        status: pass
    human_judgment: false
  - id: D3
    description: "Every job runner (checkin, deadline alert, habit reminder, idle nudge, weekly digest) reads the simulated date and behaves accordingly -- annual reminder matching, overdue-by-N, Sunday gate, idle threshold, undone-habit listing"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_weekly_digest_runner_sunday_override_does_normal_work"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_weekly_digest_runner_non_sunday_override_reports_gate"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_deadline_alert_runner_matches_annual_reminder_months_away"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_deadline_alert_runner_reports_overdue_by_simulated_week"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_idle_nudge_runner_four_days_past_does_normal_work"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_idle_nudge_runner_one_day_past_reports_not_idle_enough"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_habit_reminder_runner_lists_undone_habit_on_simulated_date"
        status: pass
    human_judgment: false
  - id: D4
    description: "No user who never sets a clock experiences any behaviour change: every refactored site is an exact no-op with no override, paired no-override controls back every simulated-clock assertion, and the full pre-existing suite passes unchanged"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_no_override_job_runners_and_commands_unchanged"
        status: pass
      - kind: unit
        ref: "python -m pytest tests/test_bot.py -v -k 'not sanity and not nl' -> 297 passed"
        status: pass
    human_judgment: false
  - id: D5
    description: "No simulated date reaches a durable record (habit completions, journal/note/tracker timestamps, activity_days, task due/completed_at, job-fire log, mute expiry) even with an active year-ahead clock, across both the tool-dispatcher and command-handler write paths where both exist"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_complete_habit_records_real_date_both_paths"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_journal_note_tracker_record_real_timestamp"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_chat_turn_touches_activity_with_real_date"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_task_add_complete_extend_compute_from_real_date"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_job_fire_writes_real_timestamp_to_job_log"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_mute_cmd_stores_real_hour_ahead_expiry"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_durable_writes_identical_with_and_without_override"
        status: pass
    human_judgment: false
  - id: D6
    description: "No simulated date reaches real scheduler arithmetic: the one-shot reminder delay and the startup catch-up window are unchanged by an active override, and the two excluded regions are provably untouched"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_once_delay_arithmetic_unaffected_by_active_override"
        status: pass
      - kind: other
        ref: "sed -n '/^def _parse_once_delay/,/^async def remind_cmd/p' bot.py | grep -c 'datetime.now(tz)' -> 1"
        status: pass
      - kind: other
        ref: "sed -n '/^def restore_all_jobs/,/^def _days_ago_iso/p' bot.py | grep -cE 'datetime\\.utcnow\\(\\)|datetime\\.now\\(tz\\)' -> 2"
        status: pass
    human_judgment: false
  - id: D7
    description: "Clearing the clock restores real time on every exercised surface within the same session (set-use-reset round trip)"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClockAmbient::test_debug_clock_ambient_set_use_reset_round_trip_restores_real_time_everywhere"
        status: pass
    human_judgment: false

duration: ~20min
completed: 2026-08-07
status: complete
---

# Phase 1 Plan 05: Ambient Debug Clock Summary

**Every remaining compare-and-display call site in `bot.py` -- deadline badges, quiet hours, mute evaluation, streaks, habit summaries, `/time`, `/mystats`, job runners, the current-time/habits tools -- now reads `/debug clock` instead of the real wall clock, while every durable-write path (habit completions, journal/note/tracker timestamps, activity_days, task dates, the job-fire log, mute expiry) is proven by test to stay on real time.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-08-07T21:05:00Z (approx.)
- **Completed:** 2026-08-07T21:23:09Z
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments
- `_get_streak`, `_is_muted`, `_is_quiet_now` now resolve through `_today(user=user)`/`_utcnow(user)`/`_now(tz, user=user)` respectively -- body substitution only, no signature change, since all three already receive the user dict.
- `_format_task_line`, `_habit_streak`, `_habit_summary_lines` each gained an optional trailing `user=None` parameter (additive, exact no-op with no argument) and now compute against the simulated date when a user's override is active. `_habit_summary_lines` forwards its `user` into its internal `_habit_streak` call so the ✓/○ mark and the streak count beside it never disagree about what day it is.
- Every existing caller of the three re-parameterised helpers now passes the user dict it already holds: `show_tasks` (both call sites), the tool dispatcher's `get_habits`/`complete_habit`/`get_current_time` branches, `build_system_prompt`, `reflect_cmd`, `insights_cmd`, `habit_cmd` (list/done/stats branches), and `my_stats`.
- All five extracted job runners (`_run_checkin`, `_run_deadline_alert`, `_run_habit_reminder`, `_run_idle_nudge`, `_run_weekly_digest`) and `time_cmd` route their date/time reads through the override, so `/debug fire` and the real scheduled path can never diverge in what they believe the date is.
- The two scheduling-arithmetic exclusions (`_parse_once_delay`'s one-shot delay, `restore_all_jobs`'s startup catch-up window) are untouched and pinned by both a source-assertion (region-scoped `sed`/`grep`) and a same-value-with/without-override behaviour test.
- `TestDebugClockAmbient` (32 tests) covers every `<behavior>` case across all three tasks, each simulated-clock assertion paired with a no-override control, plus a dedicated durable-record guard proving the clock never reaches habit completions, journal/note/tracker timestamps, `activity_days`, task due/completed dates, the job-fire log, or mute expiry -- and a set-use-reset round trip through the real `/debug clock` command.

## Task Commits

Each task was committed atomically:

1. **Task 1: Route the shared helpers through the override** - `45c3860` (feat)
2. **Task 2: Route the job runners, tool branches and command handlers through the override** - `643dce9` (feat)
3. **Task 3: Prove the clock stays out of the user's real history** - `660366c` (test)

_Note: all three tasks were TDD-flagged; per the plan's own `<action>` blocks, tests and implementation were written and verified together as one coherent unit per task rather than as strict separate RED-then-GREEN commits (matching plan 01-04's precedent). All new assertions were confirmed to exercise the intended before/after behaviour by construction (paired no-override controls) rather than by a separate pre-implementation failing run._

**Plan metadata:** (this commit)

## Files Created/Modified
- `bot.py` - `_get_streak`/`_is_muted`/`_is_quiet_now` body substitutions; `_format_task_line`/`_habit_streak`/`_habit_summary_lines` signature changes plus all callers; the `get_current_time`/`get_habits` tool branches; all five `_run_*` job runners; `time_cmd`, `habit_cmd`'s stats branch, `my_stats`'s seven-day chart
- `tests/test_bot.py` - `TestDebugClockAmbient` (32 tests, `test_debug_clock_ambient_*` prefixed)

## Decisions Made
- Test methods are named `test_debug_clock_ambient_*` rather than relying on the `TestDebugClockAmbient` class name alone. The plan's required selector (`-k debug_clock_ambient`) is a literal substring match against pytest's full node id; a PascalCase class name doesn't contain that snake_case substring, so a first pass using only the class name collected **zero** tests under the mandated selector. Confirmed against the existing `TestDebugFire` class, which already follows the `test_debug_fire_*` method-prefix convention for the same reason.
- Table A's `get_habits` tool-branch `date.today()` substitution (nominally a Task 2 line item) landed in Task 1's commit instead, since it was touched in the same `_execute_tool` region while updating that branch's `_habit_streak` caller. No functional difference; only affects which commit contains a one-line diff.
- The `_is_quiet_now` no-override/override test picks a quiet-hours window exactly 12 hours from the real current UTC hour (rather than a fixed clock-time window) to eliminate any chance of real-time-vs-window overlap flakiness, instead of the `patch("bot.datetime")` approach the pre-existing `TestQuietHours` test used (which would have broken `_debug_now`'s own `datetime.fromisoformat`/`datetime.utcnow()` calls, since they share the same module-level `datetime` reference).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Renamed all 32 `TestDebugClockAmbient` test methods to a `test_debug_clock_ambient_*` prefix**
- **Found during:** Task 3, while confirming the plan's required `python -m pytest tests/test_bot.py -k debug_clock_ambient -x -q` selector
- **Issue:** With methods named for what they exercise (e.g. `test_format_task_line_override_moves_due_today_badge`) and only the class named `TestDebugClockAmbient`, the mandated `-k debug_clock_ambient` selector matched **zero** tests -- pytest's `-k` is a literal substring match against the full node id, and `TestDebugClockAmbient` (PascalCase, no underscores) does not contain the substring `debug_clock_ambient`. This would have made every task's own acceptance criterion (`-k debug_clock_ambient -x -q` passes / collects >=20 / >=30) fail, despite every individual test passing under its own name.
- **Fix:** Renamed all 32 methods across Tasks 1-3 to a `test_debug_clock_ambient_*` prefix via a scripted rename scoped to the class body, matching the pre-existing `TestDebugFire` class's `test_debug_fire_*` convention. Re-ran both the targeted selector and the full non-LLM suite to confirm zero behavioural change from the rename.
- **Files modified:** `tests/test_bot.py`
- **Verification:** `pytest -k debug_clock_ambient -x -q` now collects and passes all 32; full suite (297) unaffected.
- **Committed in:** `660366c` (Task 3 commit)

**2. [Rule 3 - Blocking] Documented, not silently accepted, a stale acceptance-criteria check in Task 1**
- **Found during:** Task 1, running the literal exhaustiveness command from the plan's acceptance criteria: `sed -n '/^def _is_muted/,/^def _tasks_for_prompt/p' bot.py | grep -vE '^\s*#' | grep -cE 'date\.today\(\)|datetime\.now\(|datetime\.utcnow\(\)'`
- **Issue:** This returns **8**, not the specified 0. The `sed` range's end anchor (`^def _tasks_for_prompt`) is correct, but the plan was written before line numbers were re-verified against the actual file, and the range as written also spans the entire `_debug_now`/`_now`/`_today`/`_utcnow` block that plan 01-04 placed directly between `_is_muted`/`_is_quiet_now` and `_format_task_line`. Those four functions' real-clock fallback bodies (`return datetime.now(tz)`, `return date.today()`, `return datetime.utcnow()`) and their docstrings describing that fallback are the routing mechanism itself -- not remaining call sites that should have been converted.
- **Fix:** No code change (none was warranted -- the six actual Task-1 helper functions are provably clean). Re-scoped the check into the check's own two named regions independently -- `_is_muted`..`_debug_now` (quiet hours) and `_format_task_line`..`_tasks_for_prompt` (task helper) -- both of which return **0**, confirming the six target functions are fully converted. Recorded the evidence here per CLAUDE.md's "Verify Completeness Claims" rather than treating a non-zero literal result as silently acceptable or quietly adjusting the check without comment.
- **Files modified:** None (documentation-only finding)
- **Verification:** `sed -n '/^def _is_muted/,/^def _debug_now/p' bot.py | grep -vE '^\s*#' | grep -cE '...' -> 0`; `sed -n '/^def _format_task_line/,/^def _tasks_for_prompt/p' bot.py | grep -vE '^\s*#' | grep -cE '...' -> 0`
- **Committed in:** `45c3860` (Task 1 commit; no code changed by this finding)

---

**Total deviations:** 2 auto-fixed (both Rule 3 - blocking, both test-infrastructure/verification issues, zero production-code impact)
**Impact on plan:** No scope creep. Both deviations were necessary to make the plan's own acceptance criteria actually verifiable as written.

## Issues Encountered
None beyond the two deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- DEBUG-02 is complete. All three of Phase 1's requirements (DEBUG-01, DEBUG-02, DEBUG-03) are now closed across plans 01-01 through 01-05.
- The ambient scope holds end to end: `/debug clock <ISO>` changes what `/tasks`, quiet hours, mute, streaks, `/time`, `/mystats`, the dumped system prompt, and every `/debug fire` target report, and `/debug clock reset` restores real time on all of them in the same session.
- The durable-record guard (Task 3) is now the automated backstop for this plan's two prohibitions; no phase after this one should need to re-litigate whether a simulated clock can leak into stored data.
- Remaining before `/gsd-verify-work` per this plan's `<verification>` section: the manual UAT rows in `01-VALIDATION.md` (the real job fire from plan 01-03 Task 3, and the ambient clock walkthrough) -- both are human-judgment items this plan's automated suite does not and should not substitute for.
- No blockers for phase completion.

---
*Phase: 01-debug-and-dry-run*
*Completed: 2026-08-07*

## Self-Check: PASSED

- FOUND: bot.py
- FOUND: tests/test_bot.py
- FOUND: .planning/phases/01-debug-and-dry-run/01-05-SUMMARY.md
- FOUND commit: 45c3860
- FOUND commit: 643dce9
- FOUND commit: 660366c
