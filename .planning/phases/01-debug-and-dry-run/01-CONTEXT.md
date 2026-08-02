# Phase 1: Debug and Dry-Run - Context

**Gathered:** 2026-08-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Owner-only debug and observability tooling for a single-user Telegram bot: (1) fire any scheduled job on demand, with its real side effects, without waiting for its scheduled time; (2) simulate a per-account "now" so time-dependent behavior (deadline badges, quiet hours, annual reminders) can be exercised on demand; (3) dump the exact assembled system prompt verbatim, with no LLM call and no message sent. This is developer/debugging infrastructure, not a new user-facing product feature, and is gated to the bot owner (`MY_CHAT_ID`).

</domain>

<decisions>
## Implementation Decisions

### Simulated "now" lifecycle
- **D-01:** The simulated "now" is a persistent, ambient per-account override — set once (e.g. `/debug clock 2026-09-01`), then every normal command/message from the owner (e.g. `/tasks` deadline badges, quiet-hours checks, annual-reminder distance) uses the simulated time until explicitly reset (`/debug clock reset`). This matches the roadmap's success criterion "watch time-dependent behaviour respond to it" — an ambient effect on ordinary interaction, not a one-shot argument scoped to a single debug action. — **Reversibility:** reversible — a stored per-`chat_id` field, cheap to add or remove.

### Claude's Discretion

Discussion was stopped early at the user's request (before the remaining sub-questions on this same area, and before the other three gray areas identified during analysis were discussed at all). The researcher and planner should use their own judgment on the items below, informed by the notes captured here:

- **Breadth of the override.** Given D-01 (ambient, persistent), does it need to be consulted by every `datetime.now(tz)` call site touching that `chat_id` (task badges, quiet-hours check, annual-reminder distance, job closures), or only the subset the ROADMAP explicitly names (deadline badge, quiet hours, annual reminder)? ROADMAP.md's "Notes for planning" describes the `_now(tz)` refactor as covering "every job closure" specifically — the planner should reconcile that scoping note with an ambient override that must also be visible to non-job-closure read paths like `/tasks`.
- **Restart / expiry safety.** Should the override survive a bot restart (the bot runs under `nohup` with no process supervisor, so restarts happen)? Should it auto-expire after some bound, given a forgotten simulated clock could silently skew the owner's own real usage (deadline badges, quiet hours) for an unbounded time? Options raised but not decided: survive restart + auto-expiry after N hours; survive restart with no expiry; in-memory only (cleared on restart).
- **Real scheduler interaction.** While a simulated "now" is active, should the real APScheduler jobs (which fire on the actual wall clock) keep running normally for that user, or be suppressed to avoid a real job landing mid-test? No decision made.
- **Debug command surface** (not discussed): a single `/debug` command with subcommands (`fire`, `clock`, `prompt`) vs. separate top-level commands. Existing owner-gate pattern to reuse either way: `if str(chat_id) != MY_CHAT_ID` (see `bot.py:2933`, `bot.py:3600`).
- **Prompt dump delivery** (not discussed): the assembled system prompt can exceed Telegram's 4096-char message limit — split across multiple messages vs. a file attachment. Must never be written to any tracked or untracked file on disk (CLAUDE.md privacy constraint — debug output can contain real journal/note content).
- **Job-fire targeting for reminders** (not discussed): "a specific reminder" (DEBUG-01) needs to be identified somehow when firing on demand — by 1-based list number (matching the `/remind list` convention), by UUID, or by text match.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No phase-specific canonical refs beyond ROADMAP.md's Phase 1 section and REQUIREMENTS.md (DEBUG-01/02/03) — both already read by downstream agents by default. No ADRs or external specs exist for this phase.

Root-level `IDEAS`, `P1`, and `PLAN.md` are general milestone-origin notes, not phase-specific specs, and are not canonical refs for this phase. `P1` in particular contains a real personal Telegram transcript and must never be quoted into planning artifacts, per the privacy constraint in CLAUDE.md.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Owner-gate pattern:** `if str(update.effective_chat.id) != MY_CHAT_ID: return` — used identically by `/adminstats` (`bot.py:3599-3600`) and `/broadcast` (`bot.py:2931-2933`). Debug commands should reuse this exact check.
- **`chat()`'s `touch_activity=False` parameter** (already used by proactive check-ins/digest) is the existing precedent for "do a real thing without it counting as the user's own activity" — relevant if firing a job on demand should avoid double-counting activity/streaks.
- **`build_system_prompt(user)`** (`bot.py:1647`) already takes just `user`, no Telegram/network dependency, so dumping it verbatim needs no new plumbing.
- **Job scheduling functions** (`schedule_user_checkins`, `schedule_user_alerts`, `schedule_user_reminder`, `restore_all_jobs`, `bot.py:1857-2215`) define the per-job-type closures debug-fire needs to invoke directly.

### Established Patterns
- Scattered `datetime.now(tz)` calls inside job closures (flagged in ROADMAP.md as needing a one-time `_now(tz)` helper refactor consulting a per-`chat_id` override — this is the mechanism D-01's ambient override depends on).
- All tool/command handlers persist via `save_state(state)` atomically (tempfile + `os.replace`); any new debug-clock override field should follow this same write path.

### Integration Points
- New debug commands register the same way `/adminstats` does today (`CommandHandler` in `main()`), immediately after the owner-gate check.

</code_context>

<specifics>
## Specific Ideas

No specific UI/wording examples given — discussion was stopped before further detail was captured.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope (no scope-creep items came up in the single question answered).

</deferred>

---

*Phase: 1-Debug and Dry-Run*
*Context gathered: 2026-08-01*
