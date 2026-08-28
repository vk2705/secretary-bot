# a864735 — fix: duplicate reminders/tasks, forgotten name, prompt crowding

**Date:** 2026-08-28
**Files:** `bot.py`, `tests/test_bot.py`

## What changed

Three bugs reported from a live `console.py` sandbox session, plus two
regressions this fix introduced and then corrected.

1. **A self-introduction didn't register.** "я кот леопольд" left the bot
   asking how to address the user a turn later — after already using the
   name. The `set_honorific` description only covered the explicit "call me
   X" form. It now treats a plain introduction as the answer, and the tool
   returns a note cancelling the ask for the rest of the turn (the system
   prompt is built *before* the tool runs, so the model was thanking the user
   by name and asking what to call them in the same breath).

2. **A clarification produced a duplicate instead of an edit** — `PLAN.md`
   #62, confirmed in live data (three 06:00 "morning run" reminders on one
   account). `add_task`/`add_reminder` now detect near-duplicates and
   **refuse**, rather than creating and warning afterwards.

3. **No conflict surfaced** when two things landed at the same time.

## Why refusal, not a warning

The first attempt created the entry and reported the collision in the result.
That doesn't work: a clarification often arrives as *several tool calls in one
turn*, so by the time the model reads the warning both duplicates already
exist — verified, the model made two `add_reminder` calls back to back.
Refusing is also recoverable: the error explains how to edit the original, and
`confirm_duplicate=true` overrides it when the user genuinely wants both.

## The limits of word overlap, stated honestly

`_similarity()` uses stemmed word overlap plus containment. It cannot catch
every case: "Напомни про зарядку" vs "Напоминание о зарядке физкультурой"
share one content word and score **0.2** — below any threshold that would
still reject "buy milk"/"buy bread". Those two shared a *time*, so same-slot
plus any shared topic word also counts as a duplicate. Unrelated reminders at
the same time remain allowed, with a clash note.

Result: the two `PLAN.md` #62 tests went from **0-1/3** passing to **4/4**.

## Two regressions I caused and fixed

Both were caught by the existing NL suite — which is exactly what it's for.

- Widening rule 12 to "a meeting or task at a given hour" made the model
  demand a timezone for *"Add a task: visit the doctor today"*, which names no
  clock time: `add_task`/`set_today_focus` dropped to **0/3**.
- The strengthened ask-for-honorific instruction crowded out the actual work
  for new users — the model spent its turn on the social question instead of
  calling any tool. Isolated by re-running with the honorific pre-set (3/3 vs
  0/3). The instruction is now explicitly subordinate: do the request first,
  ask at the end. Back to **3/3**.

## Prompt size

The system prompt had grown to 4179 chars, past the 4000 inline limit, so even
an empty user got `/debug prompt` as a document. Trimmed to **3673** — smaller
than the 3870 it started the session at, despite carrying more instruction.

## Verification

464 unit tests pass. On the `nl` selection this commit fails 1 (a known
ordering artifact that passes 5/5 alone) where the previous commit fails 5.
