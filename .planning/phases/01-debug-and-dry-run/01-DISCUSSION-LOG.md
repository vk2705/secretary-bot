# Phase 1: Debug and Dry-Run - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-01
**Phase:** 1-Debug and Dry-Run
**Areas discussed:** Simulated "now" scope & lifecycle (stopped early — 1 of 4 planned areas selected, discussion cut short after the first question at user's request)

---

## Simulated "now" scope & lifecycle

### Q1: Lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| Persistent ambient override (Recommended) | Set once with e.g. `/debug clock 2026-09-01`, then every normal command (`/tasks`, `/quiethours`, etc.) from you uses that simulated time until you reset it. Matches the roadmap's "watch time-dependent behaviour respond to it" framing. | ✓ |
| One-shot per debug action | Simulated time is passed as an argument only to explicit debug commands (e.g. `/debug fire morning_checkin --as-of 2026-09-01`). Normal commands always use real time. No persistent state, no expiry concerns. | |

**User's choice:** Persistent ambient override (Recommended)
**Notes:** No additional clarification given.

**Discussion stopped here.** The user issued `/gsd-plan-phase 1` mid-discussion; when asked whether to finish the remaining questions in this area (restart/expiry safety, real scheduler interaction) or stop and proceed to planning, the user chose to stop. The other three identified gray areas (debug command surface, prompt dump delivery, job-fire targeting for reminders) were presented as options but never selected for discussion.

---

## Claude's Discretion

The following were surfaced during phase analysis but not discussed with the user — left to the researcher/planner's judgment, with the open questions recorded in CONTEXT.md's `<decisions>` → `### Claude's Discretion` section:

- Breadth of the simulated-now override (which call sites consult it)
- Restart / expiry safety for the override
- Real scheduler interaction while a simulated "now" is active
- Debug command surface (single `/debug` with subcommands vs. separate commands)
- Prompt dump delivery format (message splitting vs. file attachment; never written to disk)
- Job-fire targeting syntax for "a specific reminder"

## Deferred Ideas

None — discussion stayed within phase scope.
