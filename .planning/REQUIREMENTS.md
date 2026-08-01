# Requirements: Secretary Bot

**Defined:** 2026-08-01
**Core Value:** The bot must know you, and prove it at the moment it matters.

## v1 Requirements

Requirements for Milestone A ("the bot that knows you"). Each maps to roadmap phases.

### Debug & Observability

- [ ] **DEBUG-01**: User can trigger any scheduled job on demand, without waiting for its scheduled time
- [ ] **DEBUG-02**: User can simulate a different "now" to test time-dependent behavior (deadlines, quiet hours, annual reminders)
- [ ] **DEBUG-03**: User can inspect the exact assembled system prompt for a given context, without sending a real message

### Retrieval

- [ ] **RETR-01**: User can find a past journal entry, note, task, or reminder using different words than the original entry (topical match, not exact substring)
- [ ] **RETR-02**: Retrieval correctly matches a Russian-language query to an English-language entry and vice versa — validated against the project's own reproduced failure case (a query for "weather" must find an entry saying «жаркий день»)
- [ ] **RETR-03**: Every stored embedding is tagged with the model that produced it, so a model change cannot silently corrupt search results
- [ ] **RETR-04**: Retrieved memories carry enough date context that the bot never treats a stale fact as current

### Model of the User

- [ ] **MODEL-01**: The bot periodically synthesizes observations about the user's behavioral patterns, each with supporting evidence (specific dated occasions) and a confidence level
- [ ] **MODEL-02**: An observation requires a minimum evidence bar (multiple occasions across at least a week) — never formed from a single incident
- [ ] **MODEL-03**: User can view the full list of current observations the bot holds
- [ ] **MODEL-04**: User can correct or delete any observation
- [ ] **MODEL-05**: The bot never opens a conversation by unprompted-naming a psychological pattern; observations inform tone and questions, they are not announced

### Motivation & Planning

- [ ] **MOTIV-01**: When discussing a goal or task, the bot asks what exactly the user plans to do and when exactly, rather than accepting a vague plan
- [ ] **MOTIV-02**: The bot asks what is likely to get in the way, and helps form a concrete "if X, then Y" plan (plain implementation intention, not the full WOOP wish/outcome script)
- [ ] **MOTIV-03**: The bot recalls a previously formed if-then plan at a relevant moment, not only if the user brings it up first
- [ ] **MOTIV-04**: When a task seems stalled, the bot distinguishes whether the user doesn't know the first step (ambiguity) or knows it but finds it hard (difficulty), and responds differently to each
- [ ] **MOTIV-05**: For a large or vague task, the bot helps break it into a small, capped number of concrete next steps — enough to avoid over-decomposition
- [ ] **MOTIV-06**: The bot notices when a task has been re-planned multiple times with no attempted step, and names this gently rather than repeating the same planning question
- [ ] **MOTIV-07**: When a task or habit's original motivation was stated, the bot can recall and surface that reason when engagement drops
- [ ] **MOTIV-08**: On a stalled or resisted task, the bot leads by naming the feeling before offering to solve the logistics
- [ ] **MOTIV-09**: The bot reflects and asks rather than arguing or correcting when the user pushes back or disengages from a suggestion

### Persona Configuration

- [ ] **PERSONA-01**: User can set the bot's tone, contact frequency, and accountability pressure by describing it in conversation, without a settings menu
- [ ] **PERSONA-02**: User can state an explicit "never do X" rule (e.g. "don't say good morning") and the bot durably honors it going forward
- [ ] **PERSONA-03**: Persona settings are re-applied to every generated message, not just remembered as a fact, so the bot's character does not drift over long conversations
- [ ] **PERSONA-04**: User can ask the bot what its current persona settings are

### Reinforcement & Framing

- [ ] **REINF-01**: The bot's acknowledgement of progress varies in form and warmth rather than following one fixed template every time
- [ ] **REINF-02**: The user can miss a bounded number of days on a streak without the streak resetting
- [ ] **REINF-03**: The bot can use identity-based framing ("you're the kind of person who...") only when grounded in something it actually observed, and only occasionally — never as a templated response to every completion

### Conversation & UX

- [ ] **CONV-01**: No part of the bot's interaction uses inline keyboard buttons; all input and output happens as ordinary conversation
- [ ] **CONV-02**: The weekly contact from the bot is a single substantive question about the person's week, not a statistics report
- [ ] **CONV-03**: Before offering advice, the bot asks whether the user wants advice or just wants to talk
- [ ] **CONV-04**: After a period of inactivity, the bot's first message on return contains no accounting of what was missed and no guilt; it offers one small, easy way back in
- [ ] **CONV-05**: The user can talk to the bot about anything unrelated to tasks or habits, and the bot does not redirect back to accountability topics

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Recall

- **RECALL-01**: The bot occasionally resurfaces something meaningful from weeks past, unprompted — highest payoff but highest failure cost; held until the model of the user has enough of a track record to trust unsupervised

### MCP Authentication (Milestone B)

- **AUTH-01**: A user can authenticate to the remote MCP server via Google social login
- **AUTH-02**: An authenticated Google account is mapped to exactly one Telegram `chat_id`, verified rather than trusted from an unverified claim
- **AUTH-03**: Every MCP tool call is scoped to the authenticated user's own data only

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Inline keyboards / button grids | Closed vocabulary pre-empts the open answers this product depends on; the existing check-in grid and snooze button are being removed, not kept |
| Metric-first weekly reports | Signals the bot watches the metric, not the person |
| Uniform/templated praise every time | Predictable reinforcement is weaker than variable and reads as automated |
| "You didn't do X" phrasing | A missed day is data, never a failure — zero exceptions |
| Quantitative efficacy measurement / A/B testing | Single user; nothing to compare; would contradict the anti-metric thesis |
| Web or mobile UI | Telegram is the primary surface, Claude via MCP the secondary one |
| Scale engineering (vector databases, job queues, etc.) | Brute-force numpy cosine and the existing SQLite/APScheduler patterns are sufficient at this scale — anything heavier is premature |
| Full WOOP wish/outcome/obstacle/plan script | Plain implementation intentions carry most of the measured effect (d≈0.65 vs g≈0.34 added by the full script) |
| Bot-authored goals or plans | A 2026 preprint found AI-authored goals undermine motivation; the bot elicits and reflects the user's own plan, it does not propose one |
| Habit stacking / temptation bundling | Well-evidenced techniques with no fit here — no environmental/location cue source, no enforcement mechanism in a text medium |
| Data-at-rest hardening (encryption at rest, key-management overhaul) | Deliberately accepted as proportionate to a single-user personal server for this milestone; revisit if the user base grows |

## Traceability

Populated during roadmap creation (2026-08-01).

| Requirement | Phase | Status |
|-------------|-------|--------|
| DEBUG-01 | Phase 1 | Pending |
| DEBUG-02 | Phase 1 | Pending |
| DEBUG-03 | Phase 1 | Pending |
| RETR-01 | Phase 2 | Pending |
| RETR-02 | Phase 2 | Pending |
| RETR-03 | Phase 2 | Pending |
| RETR-04 | Phase 2 | Pending |
| MODEL-01 | Phase 5 | Pending |
| MODEL-02 | Phase 5 | Pending |
| MODEL-03 | Phase 5 | Pending |
| MODEL-04 | Phase 5 | Pending |
| MODEL-05 | Phase 5 | Pending |
| MOTIV-01 | Phase 6 | Pending |
| MOTIV-02 | Phase 6 | Pending |
| MOTIV-03 | Phase 6 | Pending |
| MOTIV-04 | Phase 6 | Pending |
| MOTIV-05 | Phase 6 | Pending |
| MOTIV-06 | Phase 6 | Pending |
| MOTIV-07 | Phase 7 | Pending |
| MOTIV-08 | Phase 6 | Pending |
| MOTIV-09 | Phase 6 | Pending |
| PERSONA-01 | Phase 4 | Pending |
| PERSONA-02 | Phase 4 | Pending |
| PERSONA-03 | Phase 4 | Pending |
| PERSONA-04 | Phase 4 | Pending |
| REINF-01 | Phase 7 | Pending |
| REINF-02 | Phase 7 | Pending |
| REINF-03 | Phase 7 | Pending |
| CONV-01 | Phase 3 | Pending |
| CONV-02 | Phase 3 | Pending |
| CONV-03 | Phase 3 | Pending |
| CONV-04 | Phase 3 | Pending |
| CONV-05 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 33 total
- Mapped to phases: 33 ✓
- Unmapped: 0

**By phase:**

| Phase | Name | Requirements |
|-------|------|--------------|
| 1 | Debug and Dry-Run | 3 |
| 2 | Semantic Retrieval | 4 |
| 3 | Conversation, Not Buttons | 5 |
| 4 | Persona You Set by Talking | 4 |
| 5 | The Model of the User | 5 |
| 6 | Plans That Survive Contact | 8 |
| 7 | Why It Mattered, and Forgiveness | 4 |

**Not mapped (deliberately out of this roadmap):** RECALL-01 (v2), AUTH-01/02/03 (Milestone B).

---
*Requirements defined: 2026-08-01*
*Last updated: 2026-08-01 after roadmap creation*
