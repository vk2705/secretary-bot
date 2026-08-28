# 3d19441 — fix: eight bugs from the code review

**Date:** 2026-08-28
**Files:** `bot.py`, `admin.py`, `tests/test_bot.py`

## What changed

Fixes all eight bugs found in the review requested that day. Each was
reproduced by direct execution against the real code *before* being fixed —
not inferred from reading — and each now has regression tests (+33 tests,
suite 406 → 439).

| # | Bug | Impact |
|---|-----|--------|
| 1 | `/import` wrote unvalidated JSON into state | **Bricked the account** — `build_system_prompt()` then raised on every message, so the user couldn't talk to the bot to undo it |
| 2 | One-shot clock reminders never persisted | Silently lost on any restart, after the bot had confirmed them |
| 3 | `handle_location` missed the annual guard | Sharing a location turned a yearly reminder into a daily one |
| 4 | `content=None` became `""` | Telegram rejects empty messages; also poisoned history |
| 5 | `admin.py remove-reminder` skipped `db_mark_reminder_removed()` | Deleted reminders still read as active in history — *my own bug from this session* |
| 6 | Overdue recurring tasks rolled from the stale due date | A task 30 days overdue needed 30 completions to catch up |
| 7 | Snooze tokens: monotonic clock mod 10M, in-memory only | Collisions every ~2.8h; every restart killed every button |
| 8 | No error handler registered | Every crash above looked like the bot ignoring the user |

## Why these mattered more than they looked

Bugs 2 and 7 both got materially worse *earlier the same day*: the new
`post-commit` hook restarts the bot on every `bot.py` commit, so "lost on
restart" went from rare to routine. That's a good example of a change being
safe in isolation and harmful in combination.

## Found along the way

- `/remind remove 0` hit `pop(-1)` and silently deleted the **last** reminder.
- `set_timezone` let the model report one timezone while storing another: a
  sandbox user who typed "Мухосранск" (a non-existent town) got
  `Europe/Moscow`, announced as "Muhosransk time". Root cause was two-part —
  the tool description's only example was `'Moscow' → 'Europe/Moscow'`, and
  rule 12 literally contained *"Setting that for 6:00 AM Moscow time"*, which
  the model copied verbatim as fact. Inferring a timezone from the language
  the user writes in also defeated the whole `timezone_confirmed` gate, since
  the flag then meant "the model guessed" rather than "the user said".
- Two magic numbers became constants (`TRACKER_LOG_CAP`,
  `DEBUG_PROMPT_INLINE_MAX`).
