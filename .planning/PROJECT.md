# Secretary Bot

## What This Is

A personal accountability assistant on Telegram that works through relationship rather than metrics. Most productivity tools assume motivation already exists — they hand you a task list and a calendar, and when motivation is absent the list just becomes a record of things to feel guilty about. This bot is built on the opposite premise: it remembers *why* something mattered to you, asks about it with genuine curiosity, and adapts its tone and pressure to what you actually want from it.

Today it serves one user (the author). It may serve more later, but it is not being designed for scale.

## Core Value

**The bot must know you, and prove it at the moment it matters.** Warmth layered on an amnesiac memory is worse than no warmth at all — a friendly assistant that cannot recall what you told it last week is the specific failure that destroys trust.

## Requirements

### Validated

<!-- Shipped across 12 iterations and relied on daily. Inferred from the codebase map. -->

- ✓ Multi-user Telegram bot with open registration — existing
- ✓ Task management: due dates, recurrence, tags, prioritization, archive — existing
- ✓ Habits with streaks and per-habit statistics — existing
- ✓ Custom user-defined trackers with stats, history, charts — existing
- ✓ Journal entries and a quick-notes scratchpad — existing
- ✓ Reminders: daily, one-shot, and annual — existing
- ✓ Timezone-aware scheduling, quiet hours, and mute windows — existing
- ✓ LLM tool-calling — 26 tools dispatched through `_execute_tool()` — existing
- ✓ LLM routing: per-user encrypted API keys, Groq free tier, OpenAI fallback — existing
- ✓ Profile memory (permanent) and episodic memory (30-day TTL) — existing
- ✓ Activity streaks, achievement milestones, weekly digest, `/insights`, `/reflect`, `/suggest` — existing
- ✓ Data export and import as JSON — existing
- ✓ MCP server exposing the same data to Claude, over stdio and remote HTTP behind nginx — existing
- ✓ Rate limiting and job restore/catch-up across restarts — existing

### Active

<!-- Hypotheses until shipped and lived with. Two milestones. -->

**Milestone A — the bot that knows you**

- [ ] Debug mode: trigger any scheduled job on demand and inspect the assembled prompt, so behaviour can be iterated without waiting for the clock
- [ ] Topical retrieval across journal, notes, tasks and reminders — find by meaning, not substring
- [ ] Model of the user: periodically synthesised behavioural regularities, each carrying its supporting evidence and a confidence level
- [ ] The model of the user is visible and editable by the user
- [ ] Why-anchoring: record the reason a task or habit matters, and surface it when motivation dips rather than on a schedule
- [ ] Implementation intentions: probe for what exactly and when exactly, plus the likely obstacle, then form an if-then and replay it at the obstacle moment
- [ ] Task decomposition: help break a large or aversive task into concrete next steps, so that "too big to start" stops being the obstacle
- [ ] Configurable persona: tone, contact frequency, accountability pressure, and explicit never-do rules — all set by talking to the bot, not by a settings screen
- [ ] Variable reinforcement — acknowledgement that differs each time rather than uniform praise
- [ ] Identity-based framing alongside outcome framing
- [ ] Unprompted recall — occasionally resurface something meaningful from weeks ago
- [ ] Streak freeze: a permitted number of missed days that does not break a streak
- [ ] Remove every inline keyboard; all interaction happens in conversation
- [ ] Weekly contact is one substantive question, not a statistics report
- [ ] Ask permission before giving advice
- [ ] Graceful return after an absence — no accounting, no guilt, one small re-entry
- [ ] Off-topic conversation is welcome and never steered back to tasks

**Milestone B — MCP authentication**

- [ ] Google social login for the remote MCP server
- [ ] A real identity model mapping an authenticated account to a Telegram `chat_id`
- [ ] Per-user data scoping on every MCP tool

### Out of Scope

- **Inline keyboards and button grids** — a closed vocabulary bolted onto an open question. Four canned options pre-empt exactly the information the product depends on, and a tap discharges the obligation without teaching the bot anything. Removed, not merely avoided.
- **Metric-first weekly reports** — percentages signal that the bot is watching the metric rather than the person.
- **Uniform praise** — predictable reinforcement is weaker than variable, and reads as automated.
- **The "you didn't do X" register** — a missed day is data, never a failure. This is a tone constraint with no exceptions.
- **Quantitative efficacy measurement and A/B testing** — with a single user there is nothing to compare. Success is judged qualitatively.
- **Web or mobile UI** — Telegram is the surface; Claude via MCP is the secondary one.
- **Scale engineering** — no work premised on large user counts.
- **Adding more features for their own sake** — the stated goal is a bot that is smarter, not bigger.

## Context

**Maturity.** The bot has shipped 12 iterations and roughly 58 features. `bot.py` is ~4,240 lines in a deliberate single-file architecture. This is a brownfield project where the *capability* surface is already broad — the work ahead is depth, not breadth.

**The retrieval finding.** Retrieval across journal, notes, tasks and reminders is pure substring matching (`... WHERE lower(entry) LIKE '%q%'`), and there are no embeddings anywhere in the codebase. This was confirmed against a real logged failure: asking which journal entries were about the weather returned nothing, because the entries said *«жаркий день»* and contained no substring matching "погода". Nearly every Active requirement in Milestone A — unprompted recall, why-anchoring, the model of the user, asking about a project with genuine interest — reduces to the same retrieval problem. This is why retrieval is foundational rather than incidental.

A related bug from the same transcript, journal entries returned without dates, has already been fixed (`get_journal` now returns a timezone-local date per entry).

**Why debug mode comes early.** Every behaviour being changed is scheduler-driven: morning check-in 08:00, evening 21:00, deadline alerts 09:00, habit reminders 20:00, idle nudge 11:00, weekly digest Sunday 10:00. Iterating on tone and probing with one attempt per day is not viable.

**The check-in keyboard is a relic.** The 2×2 grid was added in iteration 4, before the bot could do tool-calling. It predates the version of the product that can hold an actual conversation.

**Prior thinking.** `IDEAS`, `PLAN.md` and `P1` at the repo root hold the informal backlog, the original framing of this direction, and the transcript that exposed the retrieval failure. They are not formal specs but they are the origin of this milestone.

## Constraints

- **Tech stack**: Python, single-file `bot.py`, `python-telegram-bot` with APScheduler, hybrid storage across `state.json` and SQLite — new work should follow the existing patterns rather than introduce a parallel architecture.
- **Cost**: keyless users run on Groq's free tier under 30 AI calls/hour/user. Periodic per-user synthesis for the model of the user is a new and different cost shape, and must be designed with that in mind.
- **Deployment**: the bot runs under `nohup` with no process supervisor; only the MCP server has a systemd unit. Anything requiring reliable background execution inherits this fragility.
- **Scale**: one user today. Do not engineer for more.
- **Working style**: ideas are discussed and agreed before any code is written.
- **Privacy**: the bot holds a personal journal and a behavioural model of a real person. Live secrets and user data (`env`, `mcp_remote.env`, `state.json`, `bot_memory.db`) are gitignored and must never enter planning artifacts.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Split into two milestones | The warmth/intelligence work and the MCP auth work share almost nothing — different skills, different risk | — Pending |
| Retrieval is the foundation phase | Nearly every other requirement reduces to finding the right past thing; warmth over amnesia is worse than nothing | — Pending |
| Remove all inline keyboards, including snooze | Buttons are a closed vocabulary; the product depends on open answers. Also deletes code rather than adding it | — Pending |
| Check-in becomes an open question plus a probing follow-up | Implementation intentions need *what exactly* and *when exactly*; a button cannot carry that | — Pending |
| Accountability pressure is a user-set dial, not a fixed stance | Consistent with the autonomy argument the product is built on, rather than a compromise between two designs | — Pending |
| Success is judged qualitatively | With one user there is nothing to A/B; inventing metrics would contradict the anti-metric thesis | — Pending |
| Debug mode is built early, not last | Scheduler-driven behaviour cannot be iterated at one attempt per day | — Pending |
| Psychology research gets a dedicated researcher | A list derived from ideas we already had can only confirm them; a separate remit is needed to surface mechanisms neither of us proposed | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-01 after initialization*
