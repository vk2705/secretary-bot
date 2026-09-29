# ff59279 — feat: nudge toward the smallest step on procrastinated tasks

**Date:** 2026-09-29
**Files:** `bot.py`, `tests/test_bot.py`

## What changed

Added a sixth per-user daily job, `_run_procrastination_nudge` (09:30
local, wired into `schedule_user_alerts`/`restore_all_jobs`/`DEBUG_JOBS`
the same way the other five are). It bumps a new per-task counter,
`task["stale_streak"]`, by one every day a task survives untouched
(`_task_stale_streak()` helper, mirroring `_task_text`/`_task_due` —
defaults to 0 for a bare-string task). Once the most-stuck task's streak
hits a multiple of `STALE_TASK_THRESHOLD_DAYS` (3), it starts an
LLM-driven conversation instead of a static message: the prompt tells the
model the task has sat untouched for N days, frames that as a likely sign
of avoidance rather than laziness, and asks it to (a) find out the
smallest possible next physical action — something under two minutes —
and (b) gently, curiously explore what's making it hard to start. If the
user reveals something meaningful, the model is told to persist it via
the existing `save_memory` tool (profile or episodic) so the same pattern
can be recognized next time.

## Why this shape

Discussed and agreed before writing any code (per the project's working
style). Three design forks were resolved with the user up front:

- **Detection**: a streak counter (days without movement), not a pure
  overdue check — it should catch tasks with no deadline at all, not only
  ones already late.
- **Where it fires**: its own scheduled job, not folded into the existing
  09:00 deadline alert, so it can run its own logic and threshold
  independently.
- **Memory**: save the discovered fear/reason automatically via
  `save_memory`, without asking the user's permission each time — same
  trust model the bot already uses for other profile/episodic facts.

The streak deliberately resets **only** via `complete_task` (a
non-recurring task disappears; a recurring one is replaced with a fresh
dict that has no `stale_streak` key). It does *not* reset on `/duedate`,
`/extend`, `/prioritize`, or `/swap` — pushing a deadline out or
reshuffling the list isn't evidence of real progress, and moving the
goalpost is often part of the avoidance pattern itself, not a fix for it.

The nudge goes through `chat()` with no `system` override (same pattern
as the missed-checkin catch-up in `restore_all_jobs`, not the static-text
`_run_idle_nudge`), so `build_system_prompt()` still injects the user's
full profile/episodic memory — the bot can reference a previously-found
fear from an earlier stuck task instead of starting cold each time.

To avoid nagging daily about the same stuck task, it only fires again
every further multiple of the threshold (3, 6, 9, ... days), not every
day past it.

## Verification

`python -m pytest tests -v -k "not sanity and not nl"` — 487 passed (10
new/updated: guard suppression for the new runner, no-tasks reason,
streak increments without firing below threshold, fires and resets streak
at the threshold, does not refire between multiples, refires at the next
multiple, converts a bare-string task to a dict on first increment, picks
the worst of several stuck tasks, and the `/debug fire
procrastination_nudge` full-dispatch case in the shared registry test).
