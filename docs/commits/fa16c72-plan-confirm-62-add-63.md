# fa16c72 — docs(plan): confirm #62 with live example, add #63

**Date:** 2026-08-28
**Files:** `PLAN.md`

## What changed

While looking up a specific user's timezone in live data (at the user's
request), found a concrete real-world instance of `PLAN.md` #62
("clarifying follow-up treated as a duplicate action, not an edit"): user
`5838336004` has three near-duplicate daily reminders, all `06:00`, all
themed "утренняя пробежка" (morning run) — `"Напоминание о пробежке"`,
`"Утренняя пробежка! 🏃‍♂️✨"`, `"🏃‍♀️ Утренняя пробежка!"`. Added this as
confirmation under #62.

Also found the same user's timezone was never confirmed — `timezone: "UTC"`
(default, never set), `timezone_confirmed` absent — even after #61 (the
timezone-confirmation gate merged earlier today, `3bc2b89`) landed. Added
**#63**: #61 only guards *new* scheduling calls going forward; a user
registered before `timezone_confirmed` existed has no retroactive prompt,
so their existing absolute-time reminders keep firing at whatever UTC-based
time they were created with.

## Why

No code changed — this is planning/backlog bookkeeping, prompted by a live
data lookup that happened to surface two real instances of open, previously
theoretical backlog items. Recording it against the live user rather than
letting the observation evaporate.

## Not done here

Neither #62 nor #63 is implemented by this commit — both remain 🔲 proposed
in `PLAN.md`, with open design questions each. The specific user's
timezone fix and reminder de-duplication follow as separate, immediate
next steps (see chat).
