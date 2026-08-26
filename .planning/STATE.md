---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 01
current_phase_name: debug-and-dry-run
status: executing
stopped_at: Plan 01-03 Tasks 1-2 complete (code + automated tests); Task 3 blocking human-verify checkpoint not yet run
last_updated: "2026-08-26T00:00:00.000Z"
last_activity: 2026-08-26
last_activity_desc: Plan 01-03 Tasks 1-2 (extract remaining job bodies, DEBUG_JOBS registry, all seven /debug fire targets) executed and verified; DEBUG-01 not yet complete pending owner's live-bot verification
progress:
  total_phases: 1
  completed_phases: 0
  total_plans: 5
  completed_plans: 2
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-01)

**Core value:** The bot must know you, and prove it at the moment it matters.
**Current focus:** Phase 01 — debug-and-dry-run

## Current Position

Phase: 01 (debug-and-dry-run) — EXECUTING
Plan: 2 of 5 complete (01-01 tracer slice, 01-02 /debug prompt); 01-03 code+tests done, awaiting owner's manual verification (Task 3)
Status: Executing Phase 01
Last activity: 2026-08-26 — Plan 01-03 Tasks 1-2 executed and verified; Task 3 (live-bot checkpoint) needs the owner

Progress: [████░░░░░░] 40% (01-03 will complete once Task 3 is confirmed)

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

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Debug tooling is Phase 1 — every behaviour in this milestone is scheduler-driven; one attempt per day is not a viable iteration loop
- [Roadmap]: Phase 3 (promptkit refactor + conversation rework) is SOLO and BLOCKING — no prompt-touching phase may start until it merges
- [Roadmap]: Phases 4 (persona) and 5 (model of the user) may run in parallel after Phase 3, but must be *designed* jointly — never-do rules are what make observation data safe
- [Roadmap]: Retrieval is a tool the model calls, never pre-injected — journal and notes are unbounded corpora
- [Roadmap]: Data-at-rest hardening deliberately deferred this milestone; extend the gitignore/handling discipline to debug-mode prompt dumps, which contain real journal content

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

Last session: 2026-08-26T00:00:00.000Z
Stopped at: Plan 01-03 Tasks 1-2 (extraction + DEBUG_JOBS + all seven fire targets) complete and green; Task 3 is a blocking checkpoint:human-verify step that needs the owner to deploy and test against the live bot on Telegram (see 01-03-SUMMARY.md "Task 3: NOT RUN")
Resume file: .planning/phases/01-debug-and-dry-run/01-03-PLAN.md (Task 3), then 01-04-PLAN.md
