---
phase: 01-debug-and-dry-run
plan: 04
subsystem: testing
tags: [debug-tooling, sqlite, zoneinfo, python-telegram-bot]

# Dependency graph
requires:
  - phase: 01-debug-and-dry-run (01-01)
    provides: /debug command with owner gate, DEBUG_JOBS registry, /debug prompt
  - phase: 01-debug-and-dry-run (01-03)
    provides: /debug fire covering all seven scheduled behaviours via extracted _run_* runners
provides:
  - "db_delete_pref(chat_id, key) SQLite helper"
  - "debug_clock / debug_clock_expires user_prefs keys, overlaid onto the user dict by get_user() exactly as timezone is"
  - "_debug_now(user) / _now(tz, user=None) / _today(user=None) / _utcnow(user=None) time helpers, exact no-ops with no override set"
  - "/debug clock <ISO> | reset | (status) command"
affects: [01-05 (routes ~20 call sites through the new helpers)]

# Actuals (#2632)
actuals:
  tokens: 6621
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Ambient per-user override, dual-written and overlay-read: a debug-only value written to both the in-memory user dict and SQLite user_prefs, overlaid back onto the user dict on every get_user() call, mirroring the existing timezone precedent"
    - "Single guarded resolver + thin call-site wrappers: _debug_now is the only function that touches storage/expiry semantics; _now/_today/_utcnow are one-line conditionals over it"

key-files:
  created: []
  modified:
    - bot.py
    - tests/test_bot.py

key-decisions:
  - "Storage lives in user_prefs (SQLite), not state.json alone, so it survives a state.json overwrite/restore and is excluded from /export by construction (whitelist-based export_data)"
  - "Expiry is judged against the real datetime.utcnow(), never the override itself, so a simulated clock can never extend its own life"
  - "_today() stays server-local with no override active (no user-timezone conversion) -- correcting that pre-existing semantic is explicitly out of scope for this phase (D-P6)"
  - "The expiry instant is stored as a second in-memory user-dict key (debug_clock_expires) so _debug_now stays a pure function of the user dict and needs no database access of its own"

patterns-established:
  - "Every new debug-only per-user field must be added to reset_cmd's wipe list -- a simulated clock is emphatically not something an account wipe should preserve, unlike timezone"

requirements-completed: [DEBUG-02]

coverage:
  - id: D1
    description: "db_delete_pref(chat_id, key) removes one SQLite user_prefs row without touching other keys or other users"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_db_delete_pref_removes_only_named_key"
        status: pass
    human_judgment: false
  - id: D2
    description: "_debug_now/_now/_today/_utcnow are exact no-ops (match the stdlib call they replace) when no override is stored"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_no_override_now_matches_stdlib"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_no_override_today_matches_stdlib"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_no_override_utcnow_matches_stdlib"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_no_override_debug_now_is_none"
        status: pass
    human_judgment: false
  - id: D3
    description: "An override resolves to the correct aware instant, converts correctly across timezones, and a date-only override parses to local midnight"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_override_resolves_aware_local_time"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_now_converts_to_requested_tz"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_date_only_override_midnight_local"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_utcnow_override_naive_and_comparable"
        status: pass
    human_judgment: false
  - id: D4
    description: "An expired override, an unparseable override, an unparseable expiry, and a missing expiry are each treated as absent without raising"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_expired_override_treated_as_absent"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_unparseable_override_returns_none"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_unparseable_expiry_returns_none"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_missing_expiry_returns_none"
        status: pass
    human_judgment: false
  - id: D5
    description: "get_user() overlays debug_clock from SQLite onto the user dict, SQLite winning over a stale state.json value, matching the timezone precedent"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_get_user_overlays_debug_clock_sqlite_wins"
        status: pass
    human_judgment: false
  - id: D6
    description: "The two locals shadowing the new module-level _now (set_timezone tool branch, build_system_prompt) are renamed to _now_dt without behaviour change"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_set_timezone_tool_no_shadow_regression"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestNowHelper::test_now_helper_build_system_prompt_no_shadow_regression"
        status: pass
      - kind: other
        ref: "sed -n '/^async def _execute_tool/,/^def build_system_prompt/p' bot.py | grep -vE '^\\s*#' | grep -cE '_now\\s*=' -> 0; same check for build_system_prompt -> 0"
        status: pass
    human_judgment: false
  - id: D7
    description: "/debug clock <ISO> sets the override (dual-write to user dict + SQLite), echoes the instant and expiry, and takes effect immediately in the same process; a date-only ISO is accepted"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_set_echoes_instant_and_expiry"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_set_takes_effect_immediately_same_process"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_date_only_stored_as_midnight_local"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_expiry_is_twelve_hours_ahead"
        status: pass
    human_judgment: false
  - id: D8
    description: "/debug clock with no argument reports the active override and expiry, or that none is active"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_status_reports_active_override"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_status_reports_none_active"
        status: pass
    human_judgment: false
  - id: D9
    description: "/debug clock reset deletes both SQLite prefs and clears the user-dict keys, confirms in the reply, and is harmless when nothing is set"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_reset_clears_prefs_and_confirms"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_reset_when_nothing_set_is_harmless"
        status: pass
    human_judgment: false
  - id: D10
    description: "Malformed clock input (unparseable string, invalid calendar date, empty string) replies with usage guidance and stores nothing in either SQLite key"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_malformed_input_stores_nothing[notadate]"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_malformed_input_stores_nothing[2027-13-45]"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_malformed_input_stores_nothing[]"
        status: pass
    human_judgment: false
  - id: D11
    description: "The override survives a simulated restart (in-memory state wiped, re-read through get_user's SQLite overlay)"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_survives_simulated_restart"
        status: pass
    human_judgment: false
  - id: D12
    description: "/reset (the account wipe) also clears both debug clock prefs and in-memory keys, so a wipe cannot leave a live simulated clock behind"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_reset_via_account_wipe_clears_prefs"
        status: pass
    human_judgment: false
  - id: D13
    description: "A data export contains neither debug_clock nor debug_clock_expires"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_excluded_from_export"
        status: pass
    human_judgment: false
  - id: D14
    description: "A non-owner is rejected before /debug clock parses or stores anything"
    requirement: "DEBUG-02"
    verification:
      - kind: unit
        ref: "tests/test_bot.py::TestDebugClock::test_debug_clock_non_owner_rejected_before_parse_or_store"
        status: pass
    human_judgment: false

duration: 15min
completed: 2026-08-07
status: complete
---

# Phase 1 Plan 04: Debug Clock Storage, Helpers and Command Summary

**A persistent, SQLite-backed, per-account simulated "now" — `/debug clock <ISO> | reset | (status)` — resolved by a single guarded helper (`_debug_now`) and consumed through three exact-no-op wrappers (`_now`/`_today`/`_utcnow`), bounded to a real-wall-clock 12-hour expiry.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-08-07T20:55:00Z (approx.)
- **Completed:** 2026-08-07T21:04:12Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- `db_delete_pref(chat_id, key)` added alongside `db_set_pref`/`db_get_pref`/`db_get_all_prefs`, following the same connection and coercion shape.
- `debug_clock` and `debug_clock_expires` added to `_new_user()` and overlaid from SQLite by `get_user()` exactly as `timezone` is (SQLite wins, survives a `state.json` overwrite/restore).
- Four new time helpers colocated with `_is_muted`/`_is_quiet_now`: `_debug_now(user)` (the single point of storage/expiry resolution, swallowing missing/unparseable/expired overrides without raising), `_now(tz, user=None)`, `_today(user=None)`, `_utcnow(user=None)` — each an exact no-op against the stdlib call it replaces when no override is set.
- The two locals shadowing the new module-level `_now` (inside `_execute_tool`'s `set_timezone` branch and inside `build_system_prompt`) renamed to `_now_dt`, behaviour-neutral, unblocking plan 01-05.
- `/debug clock <ISO> | reset | (status)` implemented in `_debug_clock`, replacing 01-01's placeholder branch — dual-writes to the user dict and SQLite exactly as `set_timezone` does, echoes the instant and a 12-hour expiry, and both properties (real jobs keep firing; auto-expiry) are stated in the reply text rather than left to be discovered.
- `reset_cmd` now clears both debug-clock prefs and in-memory keys alongside its existing wipe, closing the "override outlives an account wipe" leak (T-1-15).
- `export_data`'s existing whitelist was confirmed by test to exclude both keys (T-1-14) — no code change needed there, only a regression-guard test.

## Task Commits

Each task was committed atomically:

1. **Task 1: Storage, overlay and the four time helpers** - `4c8a592` (feat)
2. **Task 2: /debug clock set, status and reset** - `caceaad` (feat)

_Note: both tasks were TDD-flagged; tests were written and run alongside the implementation in the same commit per task rather than as separate RED/GREEN commits, since the plan's `<action>` blocks specify tests and implementation as one coherent unit per task rather than a strict RED-then-GREEN gate. All new tests failed before the corresponding helpers/handler existed and passed once written._

**Plan metadata:** (this commit)

## Files Created/Modified
- `bot.py` - `db_delete_pref`, `debug_clock`/`debug_clock_expires` in `_new_user()` and `get_user()`'s overlay, `_debug_now`/`_now`/`_today`/`_utcnow` helpers, two `_now`→`_now_dt` renames, `_debug_clock` command handler wired into `debug_cmd`, `reset_cmd` wipe extended to the two debug-clock prefs
- `tests/test_bot.py` - `TestNowHelper` (17 tests) and `TestDebugClock` (15 tests), plus a small `_reset_context()` helper for mocking `reset_cmd`'s job-queue calls

## Decisions Made
- Carrier is the user dict overlay (SQLite → `get_user()`), not a threaded `chat_id` parameter — matches D-P6's resolution in the plan frontmatter, keeping every existing call site that already receives a `user` dict signature-unchanged.
- `_today()` deliberately stays server-local with no override active; correcting `date.today()`'s existing semantics to be timezone-aware is out of scope for this phase per D-P6.
- The expiry is stored as a second overlay key (`debug_clock_expires`) on the user dict rather than requiring `_debug_now` to take a `chat_id` and query SQLite directly — keeps `_debug_now` a pure function of its one argument.

## Deviations from Plan

None — plan executed exactly as written. Both tasks' `<action>` and `<behavior>` blocks were implemented as specified; all acceptance criteria and threat-model mitigations (T-1-01 through T-1-15, T-1-SC) were verified by test or by the source-level `grep`/`sed` assertions from the plan.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- The helper contract (`_debug_now`/`_now`/`_today`/`_utcnow`) is settled and fully tested with no override set proven to be a no-op — plan 01-05 can now route the roughly twenty existing call sites through these helpers without behaviour risk.
- `/debug clock` is fully functional end-to-end (set/status/reset), durable across restarts, bounded to 12 hours, excluded from export, and cleared by `/reset` — no follow-up work required within this plan's scope.
- No blockers for 01-05.

---
*Phase: 01-debug-and-dry-run*
*Completed: 2026-08-07*

## Self-Check: PASSED

- FOUND: bot.py
- FOUND: tests/test_bot.py
- FOUND: .planning/phases/01-debug-and-dry-run/01-04-SUMMARY.md
- FOUND commit: 4c8a592
- FOUND commit: caceaad
