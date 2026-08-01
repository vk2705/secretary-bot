# Project Research Summary

**Project:** Secretary Bot — Milestone A ("the bot that knows you")
**Domain:** Personal AI accountability/companion bot with long-term memory
**Researched:** 2026-08-01
**Confidence:** MEDIUM

> **Provenance note.** This file was persisted by the orchestrator after the synthesizer agent hit the known #222 false-refusal — it fabricated a write restriction and returned the complete document inline instead of writing it. Content is the synthesizer's; structure was mapped onto the template. No findings were altered.

## Executive Summary

This is a brownfield *depth* project. The bot already has 58 features across 4,240 lines; the work ahead is memory, retrieval and modelling, not new capabilities. Five research streams converge on three engineering decisions: use brute-force numpy cosine rather than a vector database, rebuild system-prompt assembly as a budgeted registry rather than string concatenation, and synthesise behavioural patterns with explicit evidence counts rather than LLM self-report.

Three evidence corrections directly contradict PROJECT.md. The flagship identity-framing study failed replication (Gerber et al. 2016 against Bryan et al. 2011); WOOP's added value over plain implementation intentions is small (g≈0.34 versus d≈0.65); and the widely-cited "95% accountability success rate" is fabricated. Identity framing and WOOP should be scoped down rather than built at full ambition. The highest-value mechanisms turn out to be the cheap ones — motivational-interviewing framing, affect labelling, fresh-start awareness and task decomposition are all phrasing and prompt-construction work.

Two risks are critical and both are preventable by decisions made now. The technical one is cross-lingual retrieval: 30–50% degradation on Russian↔English even with dedicated multilingual models, against a project that has a concrete reproduced failure case. The product one is false-pattern assertion in behavioural synthesis — published memory systems resolve stale-versus-current conflicts correctly only 7–28% of the time, and a small personal corpus is noisier still.

## Key Findings

### Recommended Stack

Brute-force numpy cosine over a SQLite BLOB column beats FAISS, ChromaDB and pgvector at this corpus size — hundreds to low thousands of short texts is sub-millisecond arithmetic, and a vector database adds operational surface for a problem that does not exist. This also matches the existing dual-store architecture rather than introducing a parallel one.

**Core technologies:**
- **numpy brute-force cosine**: vector search — sub-millisecond at this scale; anything heavier is scale-engineering
- **OpenAI `text-embedding-3-small`**: embeddings — reuses the existing `OPENAI_API_KEY` and SDK, no new secrets, ~$0.02/1M tokens. MIRACL 44.0 is a verified step up from ada-002's 31.4
- **SQLite BLOB column** in `bot_memory.db`: vector persistence — matches the existing storage pattern
- **Fallback**: `sentence-transformers` + `multilingual-e5-base` if the cross-lingual gate fails; `rank-bm25` if hybrid search is ever needed

**Conflict, resolved:** ARCHITECTURE.md suggested `sqlite-vec` at MEDIUM confidence. STACK.md argues brute-force numpy at HIGH confidence and supersedes it. Recording this openly rather than averaging the two.

**Ruled out:** LaBSE — a bitext/translation-alignment model, not a retrieval model, and likely to fail the exact topical-inference case this project cares about.

### Expected Features

**Must have (table stakes):**
- Implementation-intentions elicitation — the best-evidenced mechanism in the set (d≈0.65, 94 studies, N>8,000)
- Why-anchoring — surfacing the stated reason when motivation dips
- Bounded streak freeze — Duolingo's principle is bounded, silent, retroactive forgiveness; uncapped freezes stop functioning as a safety net
- Ask-permission-before-advice default
- Graceful return message after an absence
- Keyboard removal

**Should have (competitive):**
- Configurable persona with no settings screen — market-validated; Stoic ships Coach/Stoic/Sage personas
- Variable reinforcement
- Task decomposition with an ambiguity-versus-difficulty diagnostic

**Defer:**
- Unprompted recall — highest payoff but highest failure cost, and a wrong recall directly violates the stated Core Value
- Identity framing — demote to garnish, not load-bearing (failed replication)
- Habit stacking and temptation bundling — strong evidence, poor fit; no environmental cue source and no enforcement mechanism in a text medium

### Psychology of Task Initiation and Decomposition

Commissioned separately at the user's request; carries equal weight to the feature landscape.

- **Decomposition versus the planning fallacy**: Kruger & Evans' unpacking effect *increases* estimates because it corrects omitted steps — decomposition improves accuracy rather than biasing it. The real caveat is that *exhaustive* decomposition overshoots into padding; 3–5 chunks does not.
- **Zeigarnik is dead**: the "unfinished tasks nag at memory" effect failed a 2025 meta-analysis. The separate Ovsiankina resumption tendency survives — actionable as "leave a concrete next micro-step," not "deliberately leave things open."
- **Ambiguity and difficulty are distinct barriers**, and decomposition only cures ambiguity. A one-question diagnostic — *do you know what the first move looks like?* — should route to different bot behaviour.
- **Procrasti-planning**: repeated re-planning with no attempted step is the failure mode where decomposition itself becomes the avoidance. Needs detection.
- **Debunked**: the "95% success from an accountability appointment" (ASTD) statistic is fabricated. The underlying Gail Matthews work is real but unpublished and unreplicated.
- **Highest-ranked novel mechanisms**, all cheap: motivational-interviewing discipline (reflect and evoke, suppress the righting reflex), affect labelling as the first move on a stalled task, fresh-start effect for re-engagement, SDT competence and relatedness, episodic future thinking for delayed-payoff tasks.

### Architecture Approach

Each new capability lives in its own new file, touching `bot.py` only at existing append-only seams — this is what makes parallel plans safe against a single 4,240-line file. The one exception is the prompt-assembly refactor, which is a solo blocking phase.

**Major components:**
1. `promptkit.py` — sectioned, budgeted registry replacing string concatenation; priority-0 (persona) never truncated, priority-3+ (observations, why-anchors) soft-truncated
2. `retrieval.py` — semantic search via numpy + OpenAI; index write-hooks at the ~8 existing write call sites; **stores a model identifier per vector** to prevent silent corruption on model swap
3. `observations.py` — behavioural synthesis with an explicit evidence schema: literal pointers, mechanically computed confidence, minimum threshold of 3+ occasions across 1+ week
4. `persona.py` — explicit stored state (tone, pressure, never-do rules), re-injected every turn to resist drift
5. `debug_tools.py` — fire jobs on demand, simulate a different "now", dump the assembled prompt

Retrieval should be a **tool the model calls**, not pre-injected context — journal and notes are unbounded; only bounded, cheap sources belong in every turn.

**Known accepted gap:** MCP-server writes will not sync into the retrieval index this milestone.

### Critical Pitfalls

1. **False-pattern assertion (CRITICAL)** — published systems resolve memory conflicts correctly only 7–28% of the time; LLMs generate confident narratives from thin evidence. Prevention: evidence schema, mechanical confidence scoring, minimum thresholds — never LLM self-report.
2. **Cross-lingual retrieval failure (CRITICAL — test gate)** — 30–50% degradation even with multilingual models. Prevention: validate against 20–30 real Russian↔English query/result pairs using the project's own reproduced failure case. Escalate if precision degrades >10%.
3. **Creepy delivery (HIGH)** — harm concentrates in *unprompted* pattern-naming, not in accurate recall. Prevention: never open with a pattern; offer permission first; separate factual from psychological observations.
4. **Memory contradictions (HIGH)** — naive retrieval surfaces stale facts. Prevention: recency-aware ranking, timestamp-labelled context blocks, chronological presentation.
5. **Embedding-model corruption (HIGH — schema-level)** — different models produce incompatible vector spaces. Prevention: store the model identifier with every vector; re-embed on change.
6. **Persona drift (HIGH)** — measured phenomenon; most models drift from an assigned persona by ~100 turns. Prevention: persisted explicit state re-injected each turn.

## Implications for Roadmap

### Phase 1: Debug tooling
**Rationale:** every behaviour in this milestone is scheduler-driven (check-ins at 08:00/21:00, alerts 09:00, habits 20:00, nudge 11:00, digest Sunday 10:00). One attempt per day makes iteration impossible. Zero dependencies on the other capabilities.
**Delivers:** on-demand job firing, simulated "now", assembled-prompt dump.
**Avoids:** the slowest possible feedback loop on every later phase.

### Phase 2: Semantic retrieval **[RESEARCH GATE]**
**Rationale:** nearly every other requirement reduces to finding the right past thing. Warmth over amnesia is worse than nothing.
**Delivers:** embeddings, index write-hooks, upgraded search tool.
**Uses:** numpy + `text-embedding-3-small` + SQLite BLOB.
**Avoids:** pitfalls 2, 4 and 5 together — cross-lingual validation, timestamp-aware ranking and model-identifier tracking are all retrieval-layer design decisions, not later fixes.

### Phase 3: Prompt-assembly refactor (SOLO, BLOCKING)
**Rationale:** `build_system_prompt()` is string concatenation and is about to receive persona rules, retrieved memories, observations and why-anchors. This is the one piece of work that cannot proceed concurrently with anything that touches prompts.
**Delivers:** `promptkit.py` — priority-ordered, token-budgeted registry.
**Blocks:** phases 4A and 4B must not start until this merges.

### Phase 4A: Persona (parallel with 4B)
**Rationale:** disjoint file, one-line registration after Phase 3.
**Delivers:** explicit stored tone/pressure/never-do state, re-injected every turn.
**Avoids:** pitfall 6 (drift and sycophancy).

### Phase 4B: Model-of-user synthesis (parallel with 4A) **[RESEARCH GATE]**
**Rationale:** disjoint file; depends only on retrieval and promptkit.
**Delivers:** evidence schema, mechanical confidence scoring, minimum thresholds, user-visible and editable observations.
**Avoids:** pitfalls 1 and 3 — designed jointly with persona, because delivery is what makes the underlying data safe.

### Phase 5: Implementation intentions and task decomposition
**Delivers:** conversational elicitation, ambiguity-versus-difficulty diagnostic, affect labelling, procrasti-planning detection.

### Phase 6: Why-anchoring, variable reinforcement, streak freeze
**Delivers:** lower-complexity behaviours; all prerequisites ready by this point.

### Phase Ordering Rationale

- Debug tooling first because it is a force multiplier on every subsequent phase's iteration speed.
- Retrieval second because the dependency analysis shows nearly every requirement reduces to it.
- The promptkit refactor is the single bottleneck; identifying it as solo/blocking is what allows everything else to parallelize safely against one large file.
- Persona and observations are designed jointly even though built in parallel, because delivery mechanism is the mitigation for the psychological-data risk.

### Research Flags

Phases needing validation during planning:
- **Phase 2 (Retrieval):** cross-lingual embedding test is a hard gate, not a checkbox. 20–30 Russian↔English pairs from the real journal.
- **Phase 4B (Observations):** confidence thresholds (3+ occasions, 1+ week, consistency %) must be calibrated empirically against real journal data, with manual false-positive review.

Phases with standard patterns (skip research):
- **Phases 1, 3, 4A, 5, 6** — architectural and conversational design, no external-dependency complexity.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM | Tooling is low-risk; the cross-lingual gate is a real validation dependency |
| Features | MEDIUM | Mixed — implementation intentions HIGH; identity framing WEAK (failed replication); WOOP WEAK (small added value) |
| Architecture | HIGH | Codebase-grounded; sequencing verified against real append-only seams |
| Pitfalls | MEDIUM | All are design-preventable rather than luck-dependent; thresholds need calibration |
| Psychology | MEDIUM | Sourcing via web search; underlying evidence ranges STRONG to debunked, labelled per claim |

**Overall confidence:** MEDIUM

**Sourcing limitation, stated plainly:** all five researchers ran with web search only — no Exa, Tavily, Brave, Firecrawl or Context7 was configured this session. Figures were cross-checked across independent results rather than fetched from vendor docs or primary papers. Downstream planning should not over-trust specific numbers. Two product claims (a Woebot shutdown, Stoic's persona feature) were flagged as needing a primary source before anything load-bearing rests on them.

### Gaps to Address

- **Evidence thresholds** (3+ versus 5+ occasions): tune empirically in Phase 4B.
- **Cross-lingual performance on the real corpus**: empirical test in Phase 2 — benchmarks are not sufficient.
- **Observable "motivation dip" signal**: validate in Phase 4B against subjective check-in reports.
- **Persona drift persistence**: test across 100+ turn simulations in Phase 4A.
- **Feature interaction effects**: test multi-feature scenarios from Phase 5 onward, not single features in isolation.
- **Data-at-rest threat model**: PITFALLS.md surfaces but deliberately does not resolve whether to formally accept the current posture as a documented Key Decision or invest in stronger key handling. A personal journal plus an inferred psychological profile sit on one unsupervised host.
- **`sqlite3` loadable-extension support** on Amazon Linux 2023: a non-issue under the primary recommendation, but needs a one-line check before FTS5+Snowball or `sqlite-vec` is ever reconsidered.

## Sources

### Primary (HIGH confidence)
- The project's own codebase and `.planning/codebase/` map — architecture, sequencing and collision analysis
- Official MCP authorization spec (2025-06-18 / 2025-11-25) — Milestone B account-linking guidance

### Secondary (MEDIUM confidence)
- Gollwitzer & Sheeran (2006) meta-analysis — implementation intentions, d≈0.65
- Gerber et al. (2016) versus Bryan et al. (2011) — identity-framing replication failure
- Steel (2007) procrastination meta-analysis; Locke & Latham goal-setting; Kruger & Evans unpacking effect
- 2025 meta-analysis on Zeigarnik versus Ovsiankina
- Mem0 / Letta / Zep memory-conflict benchmarks — 7–28% correct conflict resolution
- NeurIPS 2025 persona-drift finding; Anthropic persona-vector publication
- CHI 2025 companion-app harm taxonomy — unprompted pattern-naming

### Tertiary (LOW confidence — needs validation)
- 2026 preprint on AI-authored goals undermining motivation — single non-peer-reviewed source
- Woebot shutdown and Stoic persona claims — single-source, spot-check before citing
- Button-removal UX and solo-dev YAGNI reasoning — general software commentary, not domain-verified

---
*Research completed: 2026-08-01*
*Ready for roadmap: yes*
