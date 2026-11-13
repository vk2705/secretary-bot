# 757531e — feat: give the bot a real version instead of "no version"

**Date:** 2026-11-13
**Files:** `bot.py`

## What changed

Added `BOT_VERSION = "2026-11-13.1"` near the other module-level
constants, format `YYYY-MM-DD.N` — date of the last meaningful change,
`N` incrementing for a same-day repeat. Rule #7 in `build_system_prompt`
now states it (`f"7. ... Version: {BOT_VERSION} — state if asked.\n"`)
instead of the "no version number" line from `aeaa78a`.

## Why

Direct follow-up ask after `aeaa78a`: having the bot admit it has no
version was correct over inventing one, but the user actually wanted a
real version it could report — "дата и номер" (date and number).

## Why manual, not derived from git

No build step exists in this project (single-file `bot.py`, no CI) and
nothing else here is auto-generated — `docs/commits/` itself is a
hand-written log, not a changelog tool. A manually bumped date+counter
fits that pattern; deriving it from `git log` at import time would add a
subprocess call and a new failure mode (no `.git`, detached checkout)
for a cosmetic fact nothing else depends on.

## Verification

Re-measured `build_system_prompt()`'s base length while wording this,
same budget concern as `aeaa78a` and the `dd882a0` merge before it:
first draft (full sentence, "Your version is X — state it if asked,
never a different one") came to 4107 chars, over the 4096 Telegram
inline-message limit; trimmed to the one-clause version above: 4074
chars. `pytest -m "not sanity and not nl"` — 527 passed.
