# c0363a1 — fix: make check-ins probe each unfinished task, not one blanket question

**Date:** 2026-11-12
**Files:** `bot.py`

## What changed

Sharpened two spots that, between them, produced the exact behavior the
user complained about: a single lumped "how did it go?" covering every
open task, followed by generic, scripted comfort regardless of the actual
reason.

- `build_system_prompt` rules #3/#4 said only "ask about specific tasks"
  and "don't accept vague excuses without gentle pushback" — true in
  spirit but vague enough that the model defaulted to the path of least
  resistance: one blanket question, then reassurance as soon as *any*
  excuse was offered.
- The evening check-in prompt in `_run_checkin` said "ask... specifically
  about progress on their tasks. If they didn't do much, gently push back
  and encourage them to do at least one small thing" — same shape of
  vagueness, same shortcut available to the model.

Both now explicitly require: go through open tasks by name, one at a
time (not one question covering all of them); for anything unfinished,
don't accept a vague excuse and don't jump to encouragement/comfort
first — draw out the *specific* reason with a genuinely curious
follow-up, then respond to that reason.

## Why

User's framing: "по сравнению с Клодом он туповат и не любознателен" —
despite several rounds of improvement this session (procrastination
nudge, `why` discussion), the bot still felt mechanically dumber than
plain Claude on the one interaction that matters most: the evening
check-in. Tracing the actual prompt text showed why — nothing forbade
the shortcut, so the model (same underlying model as "plain Claude")
took it. This is a prompt-precision fix, not a capability gap: the data
(open task list) was already in `build_system_prompt` via
`_tasks_for_prompt`; only the instructions governing how to *use* it were
too loose.

Deliberately did not touch the procrastination-nudge job itself (already
does per-task, curious-about-the-reason framing on its own 3-day-streak
trigger) — this generalizes the same posture to the much more frequent
evening check-in path, and to rules #3/#4 so it also governs ordinary
follow-up turns after the opening check-in message, not just the opener.

## Verification

`python -m pytest tests -v -k "not sanity and not nl"` — 487 passed, no
regressions (no test asserted the old prompt wording verbatim). The `nl`
tests that exercise real LLM behavior need live API keys not present in
this sandbox — not run here; worth a manual pass against the live bot's
next evening check-in.
