# 3bc2b89 — merge: require confirmed timezone before scheduling by clock time

**Date:** 2026-08-28
**Merged branch:** `claude/mcp-oidc-auth-qme7b4` (a separate/cloud Claude Code
session working off this repo's own `PLAN.md` backlog — despite the branch
name, its content is unrelated to MCP OIDC; it implements `PLAN.md` #61)
**Files:** `CLAUDE.md`, `PLAN.md`, `bot.py`, `tests/test_bot.py`

## What changed

Adds a `timezone_confirmed` flag (SQLite `user_prefs`, same durability
pattern as `timezone` itself — survives a `state.json` overwrite). Until a
user has explicitly given a timezone (`set_timezone` tool, `/settimezone`,
or shared location), anything that would schedule at an **absolute clock
time** is now blocked and the user is asked for their timezone first,
instead of silently defaulting to UTC:

- `add_reminder`'s absolute-time branches (once + daily) and `set_checkins`
  when enabling — gated via a new `_timezone_gate()` helper, returning a
  tool error the LLM turns into "what timezone are you in?"
- `/remind add`, `/remind once` (only its `HH:MM` form), `/remind annual`,
  `/setcheckin`, `/quiethours` (only when actually setting a window) —
  gated directly with a user-facing message
- Relative delays (`delay_minutes`, `/remind once 30m`/`2h`) are exempt —
  they don't depend on timezone at all

`build_system_prompt()`'s timezone section now states confirmation status
explicitly and instructs the model to ask, call `set_timezone`, then confirm
the resolved time back with the zone named (e.g. "6:00 AM Moscow time").

## Why

Previously a user who never explicitly set a timezone got everything
scheduled against UTC with zero confirmation — a reminder set for "6am"
could silently land at the wrong local hour. This closes that gap at the
scheduling boundary rather than relying on the model to think to ask.

## Verification

Merged cleanly onto `master` (no conflicts) — the branch's base was already
this repo's `master` HEAD (`17e7fdf`), so it applied as a straightforward
fast-moving branch, not a divergent rewrite. Full unit suite:
**400 passed** (395 before this merge + 5 new gate tests), 28 sanity/NL
tests deselected (require real API calls).

## Note on branch provenance

Not authored in this session — a separate Claude Code session (cloud,
working autonomously off `PLAN.md`'s open items #59-#62) produced this
branch and pushed it directly to GitHub. Verified its base commit matched
our `master` HEAD exactly before merging, so nothing from prior work was at
risk of being clobbered. `claude/continue-ogebyo`, a different pre-existing
remote branch, was checked at the same time and found to be stale (based on
a commit from before this session's OIDC/Jeeves/console.py work) — left
unmerged.
