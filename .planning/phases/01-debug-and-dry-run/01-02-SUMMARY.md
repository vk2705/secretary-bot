---
phase: 01-debug-and-dry-run
plan: 02
subsystem: infra
tags: [telegram-bot, debug-surface, prompt-fidelity, in-memory-delivery, pytest]

# Dependency graph
requires:
  - phase: 01-01
    provides: "debug_cmd owner gate, fire/clock/prompt subcommand dispatch skeleton, _debug_update/_debug_context/as_owner test helpers"
provides:
  - "_debug_prompt(update, context) — owner-only verbatim dump of build_system_prompt(user, chat_id), no LLM call, no disk write"
  - "/debug prompt wired into debug_cmd's dispatch (replaces the plan 01-01 stub)"
  - "_no_llm_client() and _delivered_text(update) reusable test helpers for plans 01-03 through 01-05"
affects: [01-03, 01-04, 01-05]

# Actuals (#2632)
actuals:
  tokens: 4200
  tasks: 2
  commits: 2

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "In-memory document delivery for long text (export_data's pattern reused verbatim): encode UTF-8, wrap in BytesIO with .name set, reply_document(document=bio, filename=..., caption=...) — never a filesystem handle."
    - "Text-vs-document threshold: 4000-char cutoff (headroom under Telegram's 4096 hard limit) decides reply_text vs reply_document; the same string is delivered whole either way, never truncated or split."
    - "No-LLM-call assertion helper: MagicMock client with .chat.completions.create as an AsyncMock, patched over bot.get_llm_client, then .assert_not_awaited() — first precedent for this assertion shape in the suite."

key-files:
  created: []
  modified:
    - "bot.py — _debug_prompt(update, context) handler; debug_cmd's prompt branch now delegates to it instead of the 01-01 placeholder"
    - "tests/test_bot.py — TestDebugPrompt (26 tests total across both tasks); _no_llm_client() and _delivered_text() reusable helpers"

key-decisions:
  - "Task 1's tests were committed together with the _debug_prompt implementation in a single feat commit, following the precedent 01-01 already established and documented for this phase's tdd=\"true\" tasks (the plan frames the tests as this task's 'red phase' but does not mandate a standalone test(...) commit ahead of the feat(...) commit; the plan's single <done> criterion per task points at one commit)."
  - "Section markers used for Task 2's absent-then-present coverage were read directly from build_system_prompt's source strings (e.g. \"Today's habits:\", \"Recent observations (last 30 days):\") rather than re-derived, so each test pins the literal text the function emits."
  - "Threshold-boundary test computes its padding from a measured probe (add a 1-char note, diff the resulting prompt length against the empty-user baseline) rather than hard-coding the notes-section's fixed overhead, so it stays correct if Phase 3 reshapes prompt assembly."

patterns-established:
  - "Debug-surface tests that need a delivered payload regardless of which of two mutually-exclusive Telegram reply paths fired should read through a small path-agnostic helper (_delivered_text) rather than duplicating the branch logic in every assertion."

requirements-completed: [DEBUG-03]

coverage:
  - id: D1
    description: "Owner sends /debug prompt for a user with tasks, habits, trackers, notes, journal entries and profile memory: delivered text equals build_system_prompt(user, chat_id) character for character"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_matches_build_system_prompt_rich_user"
        status: pass
    human_judgment: false
  - id: D2
    description: "A brand-new user with nothing stored still returns the base prompt, no exception, no LLM call"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_empty_user_returns_base_prompt_no_crash"
        status: pass
    human_judgment: false
  - id: D3
    description: "No LLM client is constructed and no completion is requested when dumping the prompt"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_no_llm_call"
        status: pass
    human_judgment: false
  - id: D4
    description: "A prompt over 4000 chars is delivered whole as an in-memory document, decoded payload equals build_system_prompt exactly, including Cyrillic content"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_long"
        status: pass
    human_judgment: false
  - id: D5
    description: "The dump is not a conversation turn: history length and activity_days are unchanged across the call, and a non-owner is rejected before any prompt is assembled"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_no_history_or_activity_mutation"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_non_owner_rejected_before_assembly"
        status: pass
    human_judgment: false
  - id: D6
    description: "Every optional prompt section (context, trackers, habits, today's focus, notes, profile memory, episodic memory, journal, language) is individually absent for an empty user and present once seeded, matching build_system_prompt exactly in both states"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_section_absent_then_present (parametrized, 9 cases)"
        status: pass
    human_judgment: false
  - id: D7
    description: "Emoji and mixed Cyrillic/Latin note content round-trips unchanged through the document path; a prompt landing exactly on the 4000-char threshold takes the text path and one character more takes the document path; two consecutive calls against unchanged state deliver identical output"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_emoji_and_mixed_script_notes_round_trip"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_threshold_boundary_text_vs_document"
        status: pass
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_deterministic_across_consecutive_calls"
        status: pass
    human_judgment: false

duration: 10min
completed: 2026-08-07
status: complete
---

# Phase 1 Plan 2: Debug Prompt Dump Summary

**Owner-only `/debug prompt` dumps `build_system_prompt(user, chat_id)` byte-for-byte to Telegram over text or an in-memory `BytesIO` document, with zero LLM calls, zero disk writes, and zero conversational side effects — pinned by 26 tests including exact 4000/4001-char threshold and Cyrillic/emoji fidelity.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-08-07T19:58:29Z
- **Completed:** 2026-08-07T20:05:34Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Replaced plan 01-01's `/debug prompt` placeholder with `_debug_prompt(update, context)`: resolves `chat_id`, loads `get_user(chat_id)` (so the dump reflects the SQLite timezone overlay exactly like a live turn), calls `build_system_prompt(user, chat_id)` unmodified, and delivers it verbatim — text at or under 4000 chars, `export_data`'s exact `BytesIO`/`reply_document` pattern above it.
- Proved containment by construction: no `open(`/`tempfile`/`NamedTemporary` and no `logger.` call anywhere in `_debug_prompt`'s body (enforced by the plan's own `sed`-scoped grep acceptance criteria), and exactly one `BytesIO` use, matching `export_data`'s existing pattern rather than inventing a new one.
- Built the first "no LLM call was made" assertion precedent in the suite (`_no_llm_client()` — a `MagicMock` client whose `.chat.completions.create` is an `AsyncMock`, patched over `bot.get_llm_client`) and a path-agnostic `_delivered_text(update)` helper that reads whichever of `reply_text`/`reply_document` actually fired — both reusable by plans 01-03 through 01-05.
- Pinned both truths carried in the plan's `must_haves`: the empty-user case (`test_debug_prompt_empty_user_returns_base_prompt_no_crash`) and byte-for-byte equality including Cyrillic (`test_debug_prompt_matches_build_system_prompt_rich_user`, `test_debug_prompt_long`).
- Pinned every optional `build_system_prompt` section (context, trackers, habits, today's focus, notes, profile memory, episodic memory, journal, language) as individually absent-then-present, the exact 4000/4001-char text-vs-document boundary (padding computed from a measured probe, not hard-coded), emoji/mixed-script round-tripping, and call-to-call determinism.

## Task Commits

Each task was committed atomically:

1. **Task 1: /debug prompt — verbatim dump with no LLM call and no disk write** - `f275220` (feat)
2. **Task 2: Pin the empty-state and content-fidelity edges** - `5e4a8d5` (test)

_Note: Task 1 was tdd="true"; tests were written alongside the implementation and committed together in one commit, following the precedent 01-01 already established for this phase (see Decisions Made). Task 2 is test-only per its own `<action>` — no production code changed, `git diff --name-only` for that commit lists `tests/test_bot.py` only._

## Files Created/Modified
- `bot.py` - `_debug_prompt(update, context)` handler (placed directly beneath the existing `_debug_fire`); `debug_cmd`'s `prompt` branch now delegates to it
- `tests/test_bot.py` - `TestDebugPrompt` (26 tests: 7 from Task 1, 19 from Task 2 including 9 parametrized section cases); `_no_llm_client()` and `_delivered_text()` module-level helpers

## Decisions Made
- Task 1's test+implementation committed together (not a separate RED-only `test(...)` commit ahead of `feat(...)`) — matches 01-01's already-documented rationale for this phase's `tdd="true"` tasks.
- Section markers for Task 2's absent-then-present coverage are the literal strings `build_system_prompt` emits (e.g. `"Today's habits:"`, `"Recent observations (last 30 days):"`), read directly from the function rather than re-derived, so each test pins exactly what the function outputs today.
- The threshold-boundary test measures a 1-char-note probe to derive the notes-section's fixed overhead, then computes the exact padding needed to land at 4000 and 4001 chars — deliberately avoiding a hard-coded note size so the test doesn't go stale when Phase 3 rewrites prompt assembly.

## Deviations from Plan

None — plan executed exactly as written. One naming/precedent choice (single commit per `tdd="true"` task rather than separate RED/GREEN commits) is documented above under Decisions Made as inherited from 01-01, not filed as a deviation since it changes no behavior or scope.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `_debug_prompt`, `_no_llm_client()`, and `_delivered_text()` are available for plans 01-03/01-04/01-05 to reuse or extend.
- `debug_cmd`'s `clock` branch remains a stub ("not implemented yet") — plan 01-04 replaces it; no other plan should touch it concurrently per ROADMAP.md's single-pass constraint on this file.
- No blockers.

---
*Phase: 01-debug-and-dry-run*
*Completed: 2026-08-07*

## Self-Check: PASSED

- FOUND: bot.py
- FOUND: tests/test_bot.py
- FOUND: .planning/phases/01-debug-and-dry-run/01-02-SUMMARY.md
- FOUND: commit f275220 (Task 1)
- FOUND: commit 5e4a8d5 (Task 2)
