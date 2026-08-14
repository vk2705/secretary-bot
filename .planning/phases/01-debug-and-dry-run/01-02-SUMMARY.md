---
phase: 01-debug-and-dry-run
plan: 02
subsystem: infra
tags: [telegram-bot, debug-command, system-prompt, pytest]

# Dependency graph
requires: ["01-01"]
provides:
  - "_debug_prompt(update, context) — owner-only verbatim system-prompt dump, no LLM call, no disk write, no logging"
affects: [01-03, 01-04, 01-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "In-memory file delivery reused verbatim from export_data's BytesIO pattern: encode to utf-8, BytesIO, set .name, reply_document with a matching filename — nothing ever touches the filesystem."
    - "Threshold-boundary test technique: measure the fixed prompt length via a one-character probe note, solve for the padding length that lands exactly on the byte threshold, rather than hard-coding a magic padding size."

key-files:
  created:
    - ".planning/phases/01-debug-and-dry-run/01-02-SUMMARY.md"
  modified:
    - "bot.py — _debug_prompt handler, debug_cmd's prompt branch wired to it"
    - "tests/test_bot.py — TestDebugPrompt (19 tests)"

key-decisions:
  - "Padding for the 4000-char-threshold and long-prompt tests uses a large note, not a large journal entry — build_system_prompt truncates each journal line to r['entry'][:80], so only db_add_note (untruncated, notes[-10:]) can push the assembled prompt length deterministically past a target length."

requirements-completed: [DEBUG-03]

coverage:
  - id: D1
    description: "/debug prompt delivers build_system_prompt(user, chat_id) verbatim over text or document path depending on length, with no LLM call and no conversational side effect"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt (19 tests, includes debug_prompt_no_llm_call, debug_prompt_long)"
        status: pass
    human_judgment: false
  - id: D2
    description: "Empty-user case, non-ASCII (Cyrillic) and emoji fidelity, and the exact 4000-char text/document boundary are pinned"
    requirement: "DEBUG-03"
    verification:
      - kind: unit
        ref: "tests/test_bot.py#TestDebugPrompt::test_debug_prompt_empty_user_has_no_optional_sections, test_debug_prompt_emoji_and_cyrillic_note_round_trip, test_debug_prompt_threshold_boundary_text_vs_document"
        status: pass
    human_judgment: false

duration: n/a
completed: 2026-08-14
status: complete
---

# Phase 1 Plan 2: /debug prompt Summary

**Owner-gated `/debug prompt` returns the exact system prompt `build_system_prompt(user, chat_id)` produces, verbatim, over text or in-memory document delivery depending on length — with no LLM call, no disk write, and no logging of the payload.**

## Accomplishments
- Replaced the `prompt` stub branch in `debug_cmd` with `_debug_prompt(update, context)`: resolves the user through `get_user`, calls `build_system_prompt(user, chat_id)` unmodified, and delivers it via `reply_text` at or under 4000 characters or via a `BytesIO`-backed `reply_document` (`system_prompt.txt`) above that threshold — copying `export_data`'s existing in-memory delivery pattern exactly, so nothing is ever written to disk.
- Added `TestDebugPrompt` (19 tests) covering: full-fidelity round trip against a fully-seeded user, the no-LLM-call guarantee (patched `get_llm_client` never constructed or awaited), the long/short delivery-path split, history/activity-day non-mutation, non-owner rejection before prompt assembly, every optional prompt section individually absent/present, emoji + mixed Cyrillic/Latin round-trip through the document path, the exact 4000/4001-character text-vs-document boundary, and call-to-call determinism.
- Verified the three containment source assertions from the plan (no filesystem handle, no `logger.` call, exactly one `BytesIO` construction in the function body) all return the required counts.

## Files Modified
- `bot.py` — `_debug_prompt`, `debug_cmd`'s `prompt` branch
- `tests/test_bot.py` — `TestDebugPrompt`

## Verification
- `python -m pytest tests/test_bot.py -k debug_prompt -x -q` — 19 passed.
- `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"` — 186 passed.
- Source assertions (filesystem handle, logging, `BytesIO` count) all return the plan's required values.
- `git diff --stat requirements.txt` — empty.
- `git status --porcelain` — no untracked `.txt`/prompt-like file after the suite ran.

## Deviations from Plan
- Padding for the length-threshold and long-prompt tests uses `db_add_note` instead of `db_add_journal`, because `build_system_prompt` truncates each journal line to 80 characters (`r['entry'][:80]`) while notes are included in full — a journal-based pad could never deterministically reach the target length.

## Issues Encountered
None.

## Next Phase Readiness
- `/debug prompt` is fully wired; `/debug clock` remains a stub for plan 01-04.
- Plan 01-03 can proceed to extract the remaining job bodies and build the `DEBUG_JOBS` registry.
- No blockers.

---
*Phase: 01-debug-and-dry-run*
*Completed: 2026-08-14*
