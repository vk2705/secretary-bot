# aeaa78a — fix: stop the bot from inventing a version number when asked

**Date:** 2026-11-13
**Files:** `bot.py`

## What changed

Rule #7 in `build_system_prompt` now says: "No version number — say so,
don't invent one." (appended to the existing "correct mistakes naturally
and briefly" rule to avoid growing the rule count).

## Why

The user asked the live bot "какая у тебя версия" and it answered "3.1"
— confidently, with no basis. There is no `VERSION` constant,
`__version__`, or anything version-shaped anywhere in this codebase;
the model had nothing to ground that question in, so it filled the gap
with a plausible-sounding fabrication. Same failure mode `add_reminder`'s
`reason` field already guards against ("Omit rather than invent"),
just never generalized to "facts about yourself."

## Verification

Measured `build_system_prompt()`'s length for a brand-new user before
settling on the wording: the first draft (a full sentence) pushed the
base prompt to 4117 chars, over Telegram's real 4096-char inline-message
limit (`DEBUG_PROMPT_INLINE_MAX`) — the same budget the previous merge
(`dd882a0`) had just trimmed rules #3/#4 to fit under. Trimmed to the
one-clause version above: 4080 chars, 16 chars of margin.
`pytest -m "not sanity and not nl"` — 527 passed.
