# Deferred Items — Phase 1: Debug and Dry-Run

Out-of-scope discoveries logged during plan execution, per the executor's
scope-boundary rule (fix only what the current task's changes directly touch).

## From plan 01-03 (Task 2: /debug fire reminder targeting)

**Pre-existing bug in `remind_cmd`'s `remove` branch (`bot.py`, `/remind remove <n>`):**
`idx = int(args[1]) - 1` followed by `user.get("reminders", []).pop(idx)` does not
guard against `n < 1`. `/remind remove 0` computes `idx = -1`, which Python
resolves as the **last** element of the list rather than raising `IndexError` —
so `/remind remove 0` silently removes the owner's most recent reminder instead
of rejecting the input as invalid.

This is the same class of bug the plan's threat model calls out as T-1-10 for
`/debug fire reminder <n>`, and 01-03's `_debug_fire` reminder branch was
written with an explicit `if n < 1: raise ValueError` guard to avoid it.
`remind_cmd`'s `remove` branch itself was not touched — it's a separate,
pre-existing code path, out of scope for this plan's `files_modified`
boundary (the plan only names the new debug dispatch, not `remind_cmd`).

**Suggested fix (future work):** add the same `if n < 1: raise ValueError`
guard to `remind_cmd`'s `remove` branch, mirroring `_debug_fire`'s reminder
targeting exactly.
