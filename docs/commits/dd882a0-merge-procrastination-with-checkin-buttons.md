# dd882a0 — Merge origin/master: reconcile procrastination nudge with check-in buttons

**Date:** 2026-11-13
**Files:** `bot.py`, `tests/test_bot.py`, `CLAUDE.md`

## What changed

Merged the `claude/google-earth-game-idea-ywvfqx` branch (`ff59279`
procrastination nudge, `c0363a1` sharper check-in rules) into `master`,
which had moved on in the meantime: a separate session had added
outcome buttons on check-ins/reminders, per-channel notification
switches, and a user-chosen communication style (`6a1fc49`), plus
conditional tasks and a credit-exhaustion fallback (`8725d04`). Both
sides had touched the same ground — the task-dict helper functions
right after `_task_due`, the evening check-in prompt, and
`schedule_user_alerts` — so this needed real reconciliation, not a
mechanical merge.

## Why this surfaced at all

The user's original complaint ("one button for all tasks, then generic
comfort") turned out to describe code from the *other* session's commit
on `master`, not anything in this branch — this branch had never been
deployed, since the live bot only auto-restarts on commits to `master`,
and these were all on a feature branch. Discovered this while checking
whether the bot had actually restarted after `c0363a1`.

## Resolution, piece by piece

- **`_task_stale_streak` / `_task_condition`** — both newly added at the
  same spot (right after `_task_due`). Kept both; unrelated features.
- **Evening check-in opener** — kept master's version (reference one
  concrete task, ask one concise question, never invent praise). This
  branch's "ask by name, one at a time, don't accept a vague excuse"
  text doesn't fit *before* an outcome is known — it belongs after the
  user has reported one, via the buttons. Decided with the user:
  **keep the buttons, weave curiosity into the follow-up** rather than
  reverting to free-text check-ins.
- **"Not started" button follow-up** — was "No lecture. What should
  happen to the plan?", jumping straight to mechanical triage
  (reschedule/smaller/drop) without ever asking why. Changed to "No
  lecture — what got in the way?", same triage buttons attached. A
  typed answer now reaches the model as a normal turn, where the
  sharpened rule #4 (below) takes over.
- **Rules #3/#4 in `build_system_prompt`** — kept this branch's sharper
  intent (ask by name one at a time; don't accept a vague excuse before
  asking why) but trimmed hard. The first merged draft pushed a
  brand-new user's base system prompt to 4140 chars, over Telegram's
  real 4096-char message limit (`DEBUG_PROMPT_INLINE_MAX`) and well past
  master's own 4023-char baseline — this prompt is sent on *every* chat
  turn, so the verbose wording was a real, avoidable cost, not just a
  test nuisance. Final wording: "ask about open tasks one at a time, by
  name — not all at once" / "don't accept a vague excuse — ask why
  before offering comfort". New base: 4050 chars.
- **`_run_procrastination_nudge`** — updated to master's new
  `store_history=False` + `_remember_bot_message(...)` convention so its
  internal instruction prompt doesn't leak into the user's conversation
  history the way check-ins/reminders/digest/reengagement were just
  fixed to avoid.
- **Notifications** — added `"procrastination"` as a seventh channel
  (default on), gated the 09:30 job on it in `schedule_user_alerts`, and
  wired it into `/notifications`'s usage string, alias table, and list
  output, plus `CLAUDE.md`'s job table, schema example, and command
  reference.
- **Test conflict** — two independent, non-overlapping blocks of new
  tests (this branch's procrastination-nudge tests; master's
  reengagement and callback tests) landed at the same insertion point.
  Kept both.
- **`test_notification_switches_schedule_only_enabled_channels`** — this
  test hardcodes a 6-key `notifications` override dict built before the
  7th channel existed. Once `schedule_user_alerts` started checking
  `notifications.get("procrastination", True)`, the missing key
  defaulted to on and scheduled an unexpected job, breaking the test's
  exact-set assertion. Added `"procrastination": False` to the fixture.

## Verification

`pytest -m "not sanity and not nl"` — 527 passed. Deliberately used the
**marker** expression, not the `-k "not sanity and not nl"` string from
CLAUDE.md's own documented test commands: that `-k` expression also
substring-matches `"only"` as containing `"nl"`, which was silently
excluding this exact test (among several others) from every prior run
in this session — the failure was real but invisible under the
documented command. Confirmed by running the fixed test by exact name:
failed before the fix, passed after.
