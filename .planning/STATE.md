---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 1
current_phase_name: Debug and Dry-Run
status: executing
stopped_at: Completed 01-01-PLAN.md
last_updated: "2026-08-07T19:57:30.711Z"
last_activity: 2026-08-02
last_activity_desc: "Completed quick task 260802-4rx: Create a start script for bot.py and register it as a systemd service so it survives reboots"
progress:
  total_phases: 1
  completed_phases: 0
  total_plans: 5
  completed_plans: 1
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-01)

**Core value:** The bot must know you, and prove it at the moment it matters.
**Current focus:** Phase 1 — Debug and Dry-Run

## Current Position

Phase: 1 of 7 (Debug and Dry-Run)
Plan: 1 of 5 in current phase
Status: Ready to execute
Last activity: 2026-08-02 - Completed quick task 260802-4rx: Create a start script for bot.py and register it as a systemd service so it survives reboots

Progress: [██░░░░░░░░] 20%

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: —
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 12min | 2 tasks | 2 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Debug tooling is Phase 1 — every behaviour in this milestone is scheduler-driven; one attempt per day is not a viable iteration loop
- [Roadmap]: Phase 3 (promptkit refactor + conversation rework) is SOLO and BLOCKING — no prompt-touching phase may start until it merges
- [Roadmap]: Phases 4 (persona) and 5 (model of the user) may run in parallel after Phase 3, but must be *designed* jointly — never-do rules are what make observation data safe
- [Roadmap]: Retrieval is a tool the model calls, never pre-injected — journal and notes are unbounded corpora
- [Roadmap]: Data-at-rest hardening deliberately deferred this milestone; extend the gitignore/handling discipline to debug-mode prompt dumps, which contain real journal content
- [Phase ?]: 01-01: renamed the schedule_user_alerts wrapper to _deadline_alert_wrapper (not _deadline_job) so the plan's leftover-closure grep returns clean 0
- [Phase ?]: 01-01: debug_cmd makes no get_user() call at all, so the gate-precedes-lookup acceptance check is satisfied trivially
- [Phase ?]: 01-01: TestDebugOwnerGate parametrizes 5 argument shapes x 3 MY_CHAT_ID configurations for exhaustive owner-gate coverage

### Pending Todos

[From .planning/todos/pending/ — ideas captured during sessions]

None yet.

### Blockers/Concerns

- **Phase 2 research gate (cross-lingual retrieval):** 30-50% degradation is documented even with multilingual models. Must validate against 20-30 real Russian↔English pairs from the actual journal before retrieval is called done. Escalate if precision degrades >10%.
- **Phase 5 research gate (confidence calibration):** Observation thresholds (occasion count, time window, consistency) must be tuned against the real journal with manual false-positive review, not guessed.
- **Deploy fragility (resolved 2026-08-02 for `bot.py`):** `bot.py` now runs under systemd (`secretary-bot.service`, enabled + `Restart=on-failure`), matching the existing `secretary-mcp.service` pattern — see quick task `260802-4rx`. Any *future* background process (e.g. Phase 5 synthesis job) still needs its own supervision if it's not simply scheduled inside `bot.py`'s existing APScheduler jobs.
- **Known accepted gap:** `mcp_server.py` writes bypass the retrieval index hooks. Documented, not fixed this milestone.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260802-4rx | Create a start script for bot.py and register it as a systemd service so it survives reboots | 2026-08-02 | 11eb9f5 | [260802-4rx-create-a-start-script-for-bot-py-and-reg](./quick/260802-4rx-create-a-start-script-for-bot-py-and-reg/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Requirement | RECALL-01 — unprompted recall | v2 | 2026-08-01 |
| Milestone | AUTH-01/02/03 — MCP Google login | Milestone B | 2026-08-01 |
| Security | Data-at-rest hardening for journal + inferred profile | Consciously accepted | 2026-08-01 |

## Session Continuity

Last session: 2026-08-07T19:57:30.688Z
Stopped at: Completed 01-01-PLAN.md
Resume file: None
