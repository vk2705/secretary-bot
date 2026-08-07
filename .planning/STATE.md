---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 01
current_phase_name: debug-and-dry-run
status: executing
stopped_at: Completed 01-05-PLAN.md
last_updated: "2026-08-07T21:26:03.833Z"
last_activity: 2026-08-07
last_activity_desc: Phase 01 execution started
progress:
  total_phases: 1
  completed_phases: 1
  total_plans: 5
  completed_plans: 5
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-01)

**Core value:** The bot must know you, and prove it at the moment it matters.
**Current focus:** Phase 01 — debug-and-dry-run

## Current Position

Phase: 01 (debug-and-dry-run) — EXECUTING
Plan: 5 of 5
Status: Ready to execute
Last activity: 2026-08-07 — Phase 01 execution started

Progress: [██████████] 100%

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
| Phase 01-debug-and-dry-run P02 | 10min | 2 tasks | 2 files |
| Phase 01 P04 | 15min | 2 tasks | 2 files |
| Phase 01 P05 | 20min | 3 tasks | 2 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Debug tooling is Phase 1 — every behaviour in this milestone is scheduler-driven; one attempt per day is not a viable iteration loop
- [Roadmap]: Phase 3 (promptkit refactor + conversation rework) is SOLO and BLOCKING — no prompt-touching phase may start until it merges
- [Roadmap]: Phases 4 (persona) and 5 (model of the user) may run in parallel after Phase 3, but must be *designed* jointly — never-do rules are what make observation data safe
- [Roadmap]: Retrieval is a tool the model calls, never pre-injected — journal and notes are unbounded corpora
- [Roadmap]: Data-at-rest hardening deliberately deferred this milestone; extend the gitignore/handling discipline to debug-mode prompt dumps, which contain real journal content
- [Phase ?]: 01-02: /debug prompt delivers build_system_prompt verbatim via reply_text (<=4000 chars) or in-memory BytesIO reply_document (>4000 chars), never touching disk or the LLM.
- [Phase ?]: Debug clock storage lives in SQLite user_prefs, overlaid onto the user dict by get_user() exactly as timezone is, so it survives a state.json overwrite/restore and is excluded from /export by construction (01-04)
- [Phase ?]: Debug clock expiry is judged against the real datetime.utcnow(), never the override itself, so a simulated clock can never extend its own life; bounded to 12 hours (01-04)
- [Phase ?]: TestDebugClockAmbient test methods prefixed test_debug_clock_ambient_* (not just class-named) so the plan's -k debug_clock_ambient selector actually collects them
- [Phase ?]: Read/write clock boundary (D-P7) enforced: compare-and-display sites route through the override, durable-write sites stay on the real wall clock, verified by a dedicated durable-record guard

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

Last session: 2026-08-07T21:26:03.810Z
Stopped at: Completed 01-05-PLAN.md
Resume file: None
