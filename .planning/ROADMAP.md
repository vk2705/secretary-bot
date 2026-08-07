# Roadmap: Secretary Bot — Milestone A ("the bot that knows you")

## Overview

The bot already does 58 things. This milestone makes it do them like something that knows you. The journey runs foundation-first and then outward: build the ability to *see* what the bot is doing (fire any job now, freeze time, dump the prompt) so behaviour can be iterated in minutes rather than one attempt per day; replace substring matching with retrieval that actually finds things by meaning across Russian and English; rebuild prompt assembly once, alone, so that everything downstream has a stable place to attach; then layer the felt capabilities — a character the user sets by talking, an evidenced picture of the user that the user can correct, plans that survive contact with a bad morning, and a bot that remembers why something mattered and forgives a missed day.

Each phase is a vertical slice. Every one of them ends with something the user can talk to and feel, not a layer waiting for a later phase to make it real.

**Scope:** Milestone A only. `RECALL-01` (unprompted recall) is deferred to v2 — held until the model of the user has a track record worth trusting unsupervised. Milestone B (`AUTH-01/02/03`, MCP Google login) is a separate milestone and does not appear here.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Debug and Dry-Run** - Fire any scheduled job now, simulate a different "now", read the exact assembled prompt
- [ ] **Phase 2: Semantic Retrieval** - Find past entries by meaning across Russian and English, with dates attached
- [ ] **Phase 3: Conversation, Not Buttons** - Every message becomes ordinary conversation; prompt assembly rebuilt as a budgeted registry
- [ ] **Phase 4: Persona You Set by Talking** - Tone, contact frequency, pressure and never-do rules, set in conversation and held against drift
- [ ] **Phase 5: The Model of the User** - Evidenced, confidence-scored observations the user can see and correct — never announced
- [ ] **Phase 6: Plans That Survive Contact** - If-then plans, stall diagnosis, decomposition, and reflecting instead of arguing
- [ ] **Phase 7: Why It Mattered, and Forgiveness** - Surface the original reason when engagement drops; vary acknowledgement; let a missed day cost nothing

## Phase Details

### Phase 1: Debug and Dry-Run

**Goal**: Every scheduler-driven behaviour in this milestone can be triggered, time-shifted and inspected on demand, so tone and probing can be iterated in minutes instead of one attempt per day
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: DEBUG-01, DEBUG-02, DEBUG-03
**Success Criteria** (what must be TRUE):

  1. User can trigger any scheduled job by name — morning check-in, evening check-in, deadline alert, habit reminder, idle nudge, weekly digest, a specific reminder — and receives the real message it would have sent at its real time, with the same side effects
  2. User can set a simulated "now" for their own account and watch time-dependent behaviour respond to it: a deadline badge for a date that has not arrived, a quiet-hours window that is not currently active, an annual reminder months away
  3. User can ask for the exact assembled system prompt for their account and read it back verbatim — no LLM call made, no message sent, no guessing what the bot was actually told
  4. Debug output containing real journal content and debug commands themselves are owner-gated and never written anywhere tracked by git

**Plans:** 1/5 plans executed

Plans:

- [x] 01-01-PLAN.md — Tracer: owner-gated `/debug` command firing one real scheduled job end-to-end
- [ ] 01-02-PLAN.md — `/debug prompt`: verbatim system-prompt dump, no LLM call, no disk write
- [ ] 01-03-PLAN.md — `/debug fire` across all seven scheduled behaviours, guards reported by name
- [ ] 01-04-PLAN.md — `/debug clock`: persistent, bounded simulated "now" and the time helpers behind it
- [ ] 01-05-PLAN.md — Ambient breadth: route every compare-and-display site through the simulated clock

**Notes for planning:**

- This phase comes first because every behaviour later in the milestone is scheduler-driven (check-ins 08:00/21:00, deadline alerts 09:00, habit reminders 20:00, idle nudge 11:00, weekly digest Sunday 10:00). Iterating at one attempt per day is not viable, and no other phase depends on this being deferred.
- DEBUG-02 requires a one-time mechanical refactor: replace scattered `datetime.now(tz)` calls inside job closures with a single `_now(tz)` helper consulting a per-`chat_id` override. This touches every job closure once and **must not be revisited concurrently by another phase** — land it here, completely.
- DEBUG-03 dumps `build_system_prompt(user)`, whose signature and call sites survive the Phase 3 refactor unchanged. It therefore works today and keeps working after promptkit lands — no dependency inversion.

### Phase 2: Semantic Retrieval

**Goal**: The bot finds the right past thing by meaning rather than substring, across both languages the journal is written in, and never presents a superseded fact as current
**Mode:** mvp
**Depends on**: Phase 1 (for iteration speed and prompt inspection; not for correctness)
**Requirements**: RETR-01, RETR-02, RETR-03, RETR-04
**Success Criteria** (what must be TRUE):

  1. User can search for a past journal entry, note, task or reminder using words that appear nowhere in the original, and the right entry comes back
  2. A Russian query for «погода» returns the entry that says «жаркий день», and an English query for "weather" returns it too — the project's own reproduced failure, now passing as a named regression test
  3. Every retrieved item reaches the bot with its date attached and in a clearly separated block; when a fact and its later contradiction both match, the bot presents the change rather than asserting the stale version as current
  4. Changing the embedding model cannot silently corrupt search — every stored vector records the model that produced it, and a full re-embed from retained source text is possible and verifiable

**Plans**: TBD

**Notes for planning:**

- **RESEARCH GATE — hard, not a checkbox.** Cross-lingual retrieval degrades 30-50% even with multilingual embedding models. Validate against 20-30 real Russian↔English query/result pairs drawn from the actual journal *before* declaring retrieval done. If precision degrades more than ~10% versus same-language pairs, escalate and consider the fallback (`multilingual-e5-base`, or translation-normalisation for the similarity comparison only).
- Retrieval is a **tool the model calls**, never pre-injected context. Journal and notes are unbounded; only bounded sources belong in every turn. Cap results at 3-5 for precision.
- Chunk by natural entry boundary, not a fixed token window — journal entries are short and a generic RAG default would merge or split them wrongly.
- Index-alongside-persist at the ~8 existing write call sites, mirroring the established timezone dual-write pattern. Known accepted gap: `mcp_server.py` writes bypass these hooks this milestone.

### Phase 3: Conversation, Not Buttons

**Goal**: Every message the bot sends is ordinary conversation with no closed vocabulary anywhere, and the system prompt behind it is assembled by priority and budget instead of string concatenation
**Mode:** mvp
**Depends on**: Phase 1. Independent of Phase 2 for correctness — sequenced after it only because retrieval is the higher-value foundation. **SOLO, BLOCKING: no phase that touches prompt assembly may start until this has merged.**
**Requirements**: CONV-01, CONV-02, CONV-03, CONV-04, CONV-05
**Success Criteria** (what must be TRUE):

  1. No message the bot sends carries a button; the check-in arrives as an open question, and a one-word, hedged, sarcastic or oblique reply produces a short clarifying follow-up rather than a wrong tool call or silence
  2. The weekly contact is one substantive question about the person's week — no counts, no percentages, no streak totals
  3. Before offering advice the bot asks whether the user wants advice or just wants to talk; and the user can talk about something entirely unrelated to tasks for several turns and is never steered back
  4. Returning after weeks of silence, the bot's first message contains no accounting of what was missed and no guilt — it offers one small, easy way back in
  5. The prompt shown by `/debug prompt` keeps its behavioural directives intact no matter how much task, habit and tracker context exists — nothing load-bearing is lost to length

**Plans**: TBD

**Notes for planning:**

- This is the single bottleneck phase. `build_system_prompt()` is about to receive persona rules, retrieved memories, observations and why-anchors from four different later phases. Rebuilding it as a priority-ordered, token-budgeted registry (`promptkit.py`) here — once, alone — is what allows Phases 4 and 5 to run in parallel against a 4,240-line single file without colliding inside one function body.
- Precedence: persona directives priority 0 (never truncated), identity/time priority 1, existing situational state priority 2 ported unchanged, observations and why-anchors priority 3 with a character cap.
- Button removal pushes all disambiguation onto intent inference. The clarification fallback is part of *this* phase, not a later bug fix. Test with "idk", a one-word answer, a sarcastic reply, and a full off-topic tangent before this ships — Phase 1 makes that testable in minutes.

### Phase 4: Persona You Set by Talking

**Goal**: The user shapes the bot's tone, contact frequency and accountability pressure by describing it in conversation, and the bot holds that character instead of drifting agreeable
**Mode:** mvp
**Depends on**: Phase 3 (fully merged). Can run in parallel with Phase 5 — disjoint new file, one-line registration each
**Requirements**: PERSONA-01, PERSONA-02, PERSONA-03, PERSONA-04
**Success Criteria** (what must be TRUE):

  1. User can say "be gentler about deadlines" or "push me harder" in ordinary conversation, with no settings screen, and the change persists across restarts and new conversations
  2. User can say "don't say good morning" and the bot honours it indefinitely — including in scheduled messages it sends days later
  3. User can ask what the bot is currently set to and get its tone, contact frequency, accountability pressure and never-do rules read back
  4. After 50+ turns in a single conversation, including turns where the user brushes off a nudge, the pressure setting and never-do rules still hold — the bot has not quietly become agreeable because pushback was easier than holding a position

**Plans**: TBD

**Notes for planning:**

- Design this phase *jointly* with Phase 5 even though the two are built in parallel: never-do rules are the mechanism that makes the observation data in Phase 5 safe to hold, including per-topic opt-outs for high-sensitivity domains (sleep, weight, mental health, relationships).
- Persona parameters are explicit stored state changed only by a recognised intent to change a setting. "Change my accountability pressure to gentle" persists; "ugh don't remind me about this again" mid-argument does not. Adjustability is the user turning a dial; sycophancy is the model turning it because pushback felt easier.
- Re-inject the full persona every turn at priority 0. Persona drift is measured, not hypothetical — most models diverge from an assigned persona within ~100 turns.

### Phase 5: The Model of the User

**Goal**: The bot holds evidenced, confidence-scored observations about the user's behavioural regularities, shows them on request, accepts correction, and never announces them
**Mode:** mvp
**Depends on**: Phase 2 (retrieval, for evidence gathering) and Phase 3 (fully merged). Can run in parallel with Phase 4
**Requirements**: MODEL-01, MODEL-02, MODEL-03, MODEL-04, MODEL-05
**Success Criteria** (what must be TRUE):

  1. User can ask what the bot has noticed and get the full current list of observations, each showing the specific dated occasions supporting it and a confidence level
  2. No observation is ever formed from a single incident — every one that surfaces rests on multiple occasions spread across at least a week, with those thresholds tuned against the real journal rather than guessed
  3. User can correct or delete any observation, the correction visibly registers, and later synthesis runs never silently re-assert what the user removed
  4. The bot never opens a conversation or a check-in by naming a psychological pattern; observations change the questions it asks and the tone it uses, and are not announced

**Plans**: TBD

**Notes for planning:**

- **RESEARCH GATE.** Confidence thresholds (occasion count, time window, consistency percentage) must be calibrated empirically against the real journal with manual false-positive review — not guessed. Published memory systems resolve stale-versus-current conflicts correctly only 7-28% of the time, and a small personal corpus is noisier still.
- Confidence is **mechanically computed** from evidence count, consistency and spread. Never the LLM's self-reported confidence — that is exactly as unreliable as the claim it describes.
- Retrieve evidence first with a structured query, then generate narrative only from what was retrieved, then verify every claim traces back to a retrieved item before storing. Never one LLM call doing both.
- Observations are append-and-mark-status, never hard-deleted (mirrors `reminder_log` and archived tasks). A user edit freezes the row from further automatic invalidation — user correction outranks synthesis.
- An incorrect observation about your own life is *harder* to reject than an incorrect fact about the world. This is the highest-consequence pitfall in the milestone.
- Periodic synthesis is a new recurring cost shape distinct from reactive chat. Budget it against the 30-calls/hour ceiling explicitly and keep the cadence coarse.

### Phase 6: Plans That Survive Contact

**Goal**: Vague intentions become concrete if-then plans that come back at the right moment, stalls get diagnosed rather than re-planned, and pushback gets reflected rather than argued with
**Mode:** mvp
**Depends on**: Phase 2 (recalling stored plans by relevance) and Phase 3 (fully merged)
**Requirements**: MOTIV-01, MOTIV-02, MOTIV-03, MOTIV-04, MOTIV-05, MOTIV-06, MOTIV-08, MOTIV-09
**Success Criteria** (what must be TRUE):

  1. When the user says "I'll get to the report sometime", the bot asks what exactly and when exactly instead of accepting it, and asks what is likely to get in the way
  2. The conversation ends with a stated "if X, then Y", and the bot brings that plan back at the moment it becomes relevant without the user mentioning it first
  3. On a stalled task the bot names the feeling before offering to solve the logistics, then asks whether the user doesn't know the first move or knows it and finds it hard — offering decomposition only for the first, capped at a handful of concrete steps
  4. When a task has been re-planned repeatedly with no attempted step, the bot names that gently instead of asking the same planning question again
  5. When the user pushes back on or disengages from a suggestion, the bot reflects and asks rather than arguing, correcting, or repeating the advice

**Plans**: TBD

**Notes for planning:**

- Plain implementation intentions only — not the full WOOP wish/outcome script. The if-then carries most of the measured effect (d≈0.65) and the extra steps add little (g≈0.34).
- The bot elicits and reflects the user's own plan; it never authors the goal.
- Decomposition cures ambiguity, not difficulty. The one-question diagnostic — *do you know what the first move looks like?* — routes to different behaviour. Cap at 3-5 chunks; exhaustive decomposition overshoots into padding.
- Procrasti-planning (repeated re-planning with no attempted step) is the failure mode where decomposition itself becomes the avoidance. Detection is mechanical — count re-plans against attempted steps — not an LLM judgement call.
- Test multi-feature interactions from this phase onward, not single behaviours in isolation.

### Phase 7: Why It Mattered, and Forgiveness

**Goal**: The bot surfaces the original reason something mattered at the moment engagement drops, acknowledges progress differently each time, and lets a missed day cost nothing
**Mode:** mvp
**Depends on**: Phase 2 (recalling the stated reason), Phase 5 (identity framing must be grounded in a real observation)
**Requirements**: MOTIV-07, REINF-01, REINF-02, REINF-03
**Success Criteria** (what must be TRUE):

  1. When the user recorded why a task or habit mattered and then goes quiet on it, the bot brings that reason back at the moment engagement drops — not on a schedule and not as a recap
  2. Acknowledgement of a completion differs in form and warmth from one time to the next rather than repeating one template
  3. The user can miss a bounded number of days and the streak survives — silently, retroactively, and with no "you didn't do X" framing anywhere in the message
  4. Identity framing appears rarely and only when it can point to something the bot actually observed; it is never a templated response to a completion

**Plans**: TBD

**Notes for planning:**

- Why-anchoring needs the reason captured at the moment a task or habit is created, so a capture path is part of this phase, not an assumption about existing data.
- Streak freeze must be bounded — Duolingo's principle is bounded, silent, retroactive forgiveness. Uncapped freezes stop functioning as a safety net.
- Identity framing is garnish, deliberately scoped down: the flagship 2011 field study behind it failed replication in 2016. Never templated, never load-bearing.

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

**Parallelization:** Phases 4 and 5 may run concurrently once Phase 3 has merged — they touch disjoint new files and add one registration line each. Phase 3 must be solo. Nothing else in this milestone is safe to parallelize across phases.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Debug and Dry-Run | 1/5 | In Progress|  |
| 2. Semantic Retrieval | 0/TBD | Not started | - |
| 3. Conversation, Not Buttons | 0/TBD | Not started | - |
| 4. Persona You Set by Talking | 0/TBD | Not started | - |
| 5. The Model of the User | 0/TBD | Not started | - |
| 6. Plans That Survive Contact | 0/TBD | Not started | - |
| 7. Why It Mattered, and Forgiveness | 0/TBD | Not started | - |

## Requirement Coverage

All 33 v1 requirements mapped to exactly one phase. No orphans, no duplicates.

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1. Debug and Dry-Run | DEBUG-01, DEBUG-02, DEBUG-03 | 3 |
| 2. Semantic Retrieval | RETR-01, RETR-02, RETR-03, RETR-04 | 4 |
| 3. Conversation, Not Buttons | CONV-01, CONV-02, CONV-03, CONV-04, CONV-05 | 5 |
| 4. Persona You Set by Talking | PERSONA-01, PERSONA-02, PERSONA-03, PERSONA-04 | 4 |
| 5. The Model of the User | MODEL-01, MODEL-02, MODEL-03, MODEL-04, MODEL-05 | 5 |
| 6. Plans That Survive Contact | MOTIV-01, MOTIV-02, MOTIV-03, MOTIV-04, MOTIV-05, MOTIV-06, MOTIV-08, MOTIV-09 | 8 |
| 7. Why It Mattered, and Forgiveness | MOTIV-07, REINF-01, REINF-02, REINF-03 | 4 |
| **Total** | | **33** |

**Deferred (not in this roadmap):** RECALL-01 (v2), AUTH-01, AUTH-02, AUTH-03 (Milestone B).

---
*Roadmap created: 2026-08-01*
