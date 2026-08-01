# Feature Research

**Domain:** Personal AI accountability/companion bot — interaction-quality features (not task/habit CRUD, which is already built)
**Researched:** 2026-08-01
**Confidence:** MEDIUM overall (web-search-only sourcing, no paid research APIs available this run; individual claims tagged below — several are cross-checked across multiple independent sources and behave as MEDIUM, several are single-source blog material and are marked LOW)

This file deliberately does **not** re-cover table-stakes CRUD (tasks, habits, trackers, journal, reminders) — PROJECT.md confirms those already ship. It focuses on the six interaction-quality mechanisms named in the milestone: implementation intentions/WOOP, streak forgiveness, variable reinforcement, identity framing, self-compassion/lapse recovery, and comparable-product patterns for configurable persona and open-conversation interfaces.

## Feature Landscape

### Table Stakes (Users Expect These, Given the Milestone's Own Framing)

These are not "does this exist in the market" table stakes — the market barely has consumer instances of this. They are "does this need to exist for the milestone's premise to hold together" table stakes.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| A single elicitation flow that produces an if-then plan (what exactly, when exactly, obstacle, then-response) | This is the entire evidential core of the milestone — Gollwitzer & Sheeran's 2006 meta-analysis (94 studies, N>8,000) found d=0.65 on goal attainment for implementation intentions, one of the more robust non-shrinking effects in social psychology (MEDIUM confidence, cross-checked across independent sources) | MEDIUM | Must be a genuine conversational elicitation, not a form. Effect is larger when the plan is rehearsed at least once — so the plan should be replayed back to the user, not just stored silently |
| Storing the "why" per task/habit, separate from the task text itself | Motivational anchoring requires a retrievable reason, not just a title. This is a data-model requirement, not a UI one | LOW | A single `reason` field per task/habit is enough; the hard part is surfacing it at the right moment, not storing it |
| A bounded streak-forgiveness mechanic | Every comparable product (Duolingo, most habit trackers) treats an all-or-nothing streak as broken UX at this point; going forward with hard zero-reset streaks after building for interaction depth would be internally inconsistent with "a missed day is data, never a failure" (already a stated project value) | LOW–MEDIUM | Must be *bounded* (see Anti-Features) — an uncapped freeze stops being a safety net and becomes a substitute for the behavior itself |
| A returning-user message after any gap that does not open with an accounting of what was missed | Directly required by PROJECT.md ("no accounting, no guilt, one small re-entry"). Re-engagement/win-back research broadly agrees: backward-looking "you used to do X, why did you stop" framing triggers guilt/avoidance; forward-looking "here's what's next" framing triggers approach (MEDIUM — cross-industry marketing research, not habit-specific, but consistent with the psychology literature on the abstinence violation effect below) | LOW | This is a prompt-engineering constraint more than a feature — the risk is regression, not build cost |
| A user-set dial for tone / contact frequency / accountability pressure, reachable by conversation | Stoic (a real, shipping journaling app) ships exactly this today as named companion personas (The Stoic, The Coach, The Sage), each reading the same entry through a different lens — proof the concept is buildable and already validated in a comparable product (MEDIUM, single strong comparable but not academically studied) | MEDIUM | PROJECT.md already specifies this must be conversational, not a settings screen — consistent with removing buttons |

### Differentiators (Where This Product Can Actually Compete)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Motivational anchoring keyed to detected motivation dips, not a schedule | Almost no consumer habit app does this — most either nag on a fixed schedule or not at all. Requires the retrieval/model-of-user work already scoped as foundational in PROJECT.md. This is the single most differentiated feature on the list because it depends on infrastructure (semantic retrieval, behavioral model) competitors without an LLM-native architecture cannot cheaply build | HIGH | Depends on: topical retrieval (already an Active requirement) + model of user (already an Active requirement). Do not build before those land |
| Identity-based framing used *alongside* outcome framing, applied sparingly | Differentiator specifically because most productivity tools never attempt it, and the ones that do (self-help content, not software) do it as a blanket slogan rather than tailored to observed behavior. But treat this as a garnish, not a load-bearing mechanism — see evidence caveat below | LOW | Cheap to build (prompt-level), high risk of feeling like flattery if overused — pair with the variable-reinforcement finding that predictability weakens effect |
| Variable acknowledgement (non-uniform praise) driven by an actual policy, not randomness for its own sake | Genuinely differentiates from every existing habit tracker, which uses fixed congratulatory copy or fixed streak badges. The evidence for variable-ratio reinforcement increasing engagement is strong in the operant-conditioning literature and consistently observed in shipped consumer products (Instagram, TikTok, Duolingo notifications) (MEDIUM — well-established general mechanism, applied by analogy rather than directly RCT'd in this exact context) | MEDIUM | The line the author wants to avoid crossing (see Anti-Features) is "engagement as the end in itself." A workable operational test: variability should track something real (magnitude of the accomplishment, distance since last comparable praise, detected user state) rather than a random-number generator with no signal behind it |
| Ask-permission-before-advice as the standing default | Directly maps to Motivational Interviewing's elicit-provide-elicit technique — decades of clinical literature, strong practitioner consensus (MEDIUM: robust in clinical/health-counseling contexts, not validated in consumer chatbot contexts specifically) | LOW | This is almost pure prompt-design; the "PROJECT.md: ask permission before giving advice" requirement is already this technique by name, just needs the elicit-provide-elicit structure spelled out in the system prompt |
| Unprompted recall of something meaningful from weeks ago | No mainstream habit/productivity app does this reliably (most that try, like Replika, do it inconsistently and it is one of Replika's most-cited "creepy" or "delightful" swing points depending on execution) | HIGH | High payoff, high failure cost — a wrong or stale recall reads as the amnesia failure PROJECT.md explicitly names as worse than no warmth. Should not ship before retrieval accuracy is solid |

### Anti-Features (Already Ruled Out by the Author — Here's Why the Evidence Agrees or Disagrees)

PROJECT.md's Out of Scope section already rules out inline keyboards, metric-first reports, uniform praise, and "you didn't do X" phrasing. The research below either reinforces those calls or adds new ones the milestone framing implies but doesn't state outright.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| Uncapped or unlimited streak forgiveness | Feels kinder; "why not just never break a streak" | An uncapped safety net stops functioning as a safety net and becomes a substitute for the behavior it was meant to protect — this is the explicit design rationale behind Duolingo bounding freezes to a small monthly count rather than making them infinite | Bound the number of forgiveness tokens per period; consider silent/retroactive application (Duolingo applies freezes without a popup — the user discovers protection after the fact, avoiding a moment of negotiation) |
| Reward/praise triggered purely by a random-number generator with no signal behind it | "Variable reinforcement is proven to work, so randomize everything" | This is exactly where variable-ratio reinforcement crosses from engagement mechanism into the dark-pattern territory the author wants to avoid — the ethical line found consistently across sources is whether the user genuinely benefits from the interaction or whether unpredictability exists purely to manufacture anticipation for its own sake (the same mechanism that powers slot machines and infinite-scroll feeds) | Variability should be driven by something real — magnitude, novelty, elapsed time since a similar acknowledgement — so it reads as attentiveness rather than a slot machine |
| AI-generated goals/plans presented to the user rather than elicited from them | Faster, feels more "intelligent," lets the bot appear proactive | A 2026 preprint ("Optimized but Unowned: How AI-Authored Goals Undermine the Motivation They Are Meant to Drive") argues goals authored by the AI system, even when objectively better-optimized, lack the psychological ownership that drives sustained commitment — user-elicited goals outperform system-generated ones regardless of optimization quality (LOW-MEDIUM confidence — single preprint, not yet peer-reviewed, but consistent with mainstream self-determination-theory literature on autonomy and goal ownership) | Always elicit the plan from the user's own words during the if-then conversation; the bot's role is to probe and reflect the plan back, never to author it and present it as a fait accompli |
| Identity-framing delivered as a scripted phrase template ("You're the kind of person who...") applied mechanically | Cheap, on-brand, easy to implement as a canned string | The flagship applied evidence for this exact mechanism — the Bryan/Walton/Rogers/Dweck 2011 "voter" vs "voting" field experiments, originally showing an 11-14 point turnout effect — failed to replicate in a large follow-up (Gerber et al., PNAS 2016). The underlying theory (Bem's self-perception theory) is solid, but the specific "magic phrasing" version of it is not as reliable as the popular Atomic Habits treatment implies | Use identity framing sparingly, grounded in something specific and true the bot has actually observed (from the model-of-user work), not as a templated affirmation phrase issued on schedule |
| A gamified "gap accounting" or "days missed" display, even a soft one | Feels like honest transparency, "just showing the facts" | Directly recreates the abstinence-violation-effect mechanism from the relapse literature: the guilt/shame/internal-attribution response to a lapse is what predicts *progression* to a full relapse, not the lapse itself. Neff's self-compassion research is the documented antidote, and it works by *not* dwelling on the count | Already ruled out in PROJECT.md as "no accounting" on return — the research affirms this is not merely a tone preference, it is preventing a documented failure mode |
| Escalating urgency mechanics (countdown timers, "your streak dies in 3 hours!", animated flame icons) | Proven to spike short-term engagement (Duolingo does exactly this) | This is the part of Duolingo's design that the loss-aversion research itself flags as monetizing the anxiety it creates — precisely the "feels like a supervisor" territory the author wants to avoid, and orthogonal to a single-user tool with no monetization incentive to manufacture urgency | None needed — a private accountability tool for one user has no engagement-funnel pressure to replicate this from a consumer growth-hacking context |
| Structured decision-tree/scripted branching conversation (the Woebot/Wysa model) | Predictable, clinically validated, easy to QA | Directly conflicts with "remove every inline keyboard, open conversation only" — decision trees are buttons wearing a text costume. Woebot's clinical rigor came at the cost of flexibility, and the product has since shut down its consumer arm and pivoted to enterprise, suggesting the scripted model has real ceiling problems as a sole interaction mode | Keep the LLM tool-calling architecture already in place; use elicitation *technique* (elicit-provide-elicit, WOOP structure) as a conversational skeleton the model follows, not a literal branching script |

## Feature Dependencies

```
Topical retrieval (Active, already scoped)
    └──requires──> Motivational anchoring (surfacing "why" on motivation dip)
    └──requires──> Unprompted recall
    └──requires──> Model of the user

Model of the user (Active, already scoped)
    └──requires──> Motivational anchoring (needs to *detect* a motivation dip)
    └──enhances──> Identity-based framing (grounds it in real observed behavior, not template phrases)
    └──enhances──> Variable reinforcement (lets variability track real signal instead of randomness)

Why-anchoring (storing the reason per task/habit)
    └──requires──> a `reason` field in the data model (LOW cost, no dependency on retrieval)
    └──feeds──> Motivational anchoring

Implementation-intentions elicitation flow
    └──requires──> Configurable persona / accountability pressure (the probing style must match the user's tolerance for pressure, or elicitation reads as interrogation)
    └──enhances──> Why-anchoring (the "obstacle" naturally surfaces motivational context)

Streak freeze / forgiveness mechanic
    └──conflicts with──> Escalating urgency mechanics (the two pull in opposite emotional directions; do not build both)

Configurable persona (tone/frequency/pressure)
    └──requires──> Removing inline keyboards (Active, already scoped) — a persona set via a menu is a contradiction of "no settings screen"

Ask-permission-before-advice
    └──enhances──> Configurable persona (a user with pressure set to low should be asked permission more, not less, often)
```

### Dependency Notes

- **Motivational anchoring requires topical retrieval and the model of the user:** both are already scoped as foundational Active requirements per PROJECT.md, and this file's research confirms *why* — implementation intentions and identity framing are only as good as the bot's ability to retrieve the right past reason at the right moment. Do not attempt motivational anchoring before retrieval ships; it will silently degrade into scheduled generic messages, which is the exact anti-pattern (metric/schedule-first) the milestone is trying to escape.
- **Implementation-intentions elicitation requires configurable persona:** the research is explicit that elicitation "should never feel like an interrogation," and the difference between a good WOOP conversation and an annoying one is largely pacing and directness — which is exactly what the accountability-pressure dial controls. Sequence the persona dial before or alongside the elicitation flow, not after.
- **Streak freeze conflicts with escalating urgency:** both are legitimate design tools in the general literature, but they are emotionally contradictory in the same product — one tells the user "it's fine, you're covered," the other tells them "hurry, you're about to lose everything." Given the project's explicit anti-supervisor stance, urgency mechanics should be dropped entirely rather than reconciled.

## MVP Definition

### Launch With (v1 of this milestone)

- [ ] Implementation-intentions elicitation flow (what exactly, when exactly, obstacle, if-then), triggered conversationally rather than as a form — this is the best-evidenced mechanism in the whole set (d=0.65) and the one PROJECT.md names first
- [ ] Why-anchoring data field + surfacing logic tied to detected motivation dips (depends on retrieval landing first, per PROJECT.md's own sequencing)
- [ ] Bounded streak-freeze mechanic, applied without a negotiation moment (silently, retroactively visible)
- [ ] Ask-permission-before-advice as a system-prompt-level default
- [ ] Graceful, non-accounting return message after any gap
- [ ] Full inline-keyboard removal (already committed in PROJECT.md)

### Add After Validation (v1.x)

- [ ] Configurable persona (tone/frequency/pressure) reachable by conversation — add once the elicitation flow and retrieval are stable enough that a pressure dial has something real to modulate
- [ ] Variable, signal-driven acknowledgement — add once there is a real signal to key variability off (magnitude, elapsed time, novelty) rather than shipping a fake-random version first
- [ ] Identity-based framing — add last of the "soft" features; it is the one with the weakest and most fragile evidence base (failed replication on the flagship study), and it is most likely to read as flattery if the model of the user isn't mature enough to ground it in something specific

### Future Consideration (v2+)

- [ ] Unprompted recall of something from weeks ago — highest payoff, highest failure cost; defer until retrieval accuracy is validated in daily use, since a wrong recall actively damages trust (this is the "amnesia is worse than no warmth" failure mode named in PROJECT.md's Core Value)
- [ ] Any UI-level persona customization (settings menu) — deliberately never; conversational-only per the project's own constraint

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Implementation-intentions elicitation | HIGH | MEDIUM | P1 |
| Why-anchoring (data + surfacing) | HIGH | MEDIUM (depends on retrieval) | P1 |
| Bounded streak freeze | MEDIUM | LOW | P1 |
| Ask-permission-before-advice | MEDIUM | LOW | P1 |
| Graceful non-accounting return | MEDIUM | LOW | P1 |
| Configurable persona (tone/frequency/pressure) | HIGH | MEDIUM | P2 |
| Variable, signal-driven reinforcement | MEDIUM | MEDIUM | P2 |
| Identity-based framing | LOW-MEDIUM | LOW | P2 |
| Unprompted recall | HIGH | HIGH | P3 |

**Priority key:**
- P1: Best-evidenced, lowest-dependency features — build first
- P2: Real value but depends on P1 infrastructure (model of user, retrieval) being solid, or has weaker/more fragile evidence
- P3: Highest payoff but highest failure cost — defer until foundational retrieval work is proven reliable in daily use

## Competitor / Comparable-Product Feature Analysis

| Feature | Stoic (journaling app) | Duolingo | Woebot / Wysa | This Bot's Approach |
|---------|------------------------|----------|----------------|----------------------|
| Configurable tone/persona | Ships named personas (Coach, Stoic, Sage) reading the same entry differently | No — single brand voice | No — single clinical voice per app | Conversational dial, no menu, set by talking to the bot |
| Streak forgiveness | Not journaling-streak-based | Capped, silent freezes; also monetized as a purchase | N/A | Bounded, silent, never monetized (single user, no monetization incentive) |
| Interaction model | Structured prompts + free text | Lesson-based, minimal open conversation | Scripted decision-tree, clinically authored branches | Fully open conversation, zero buttons, LLM tool-calling already in place |
| Goal-setting | User writes freely | App sets lesson plan for user | App proposes CBT exercises | User-elicited only, per the AI-authored-goals ownership research — bot never authors the plan |
| Recall of past context | Limited, no explicit long-term memory feature marketed | None (lesson history only) | Session-scoped, no persistent behavioral model | Explicit model-of-user + retrieval, already scoped as Active in PROJECT.md |

## Sources

**Implementation intentions / WOOP:**
- [Implementation Intentions and Goal Achievement: A Meta-Analysis of Effects and Processes (Gollwitzer & Sheeran, 2006)](https://www.researchgate.net/publication/37367696_Implementation_Intentions_and_Goal_Achievement_A_Meta-Analysis_of_Effects_and_Processes) — MEDIUM confidence, cross-checked across three independent search queries
- [The When and How of Planning: Meta-Analysis of the Scope and Components of Implementation Intentions in 642 Tests (2024)](https://www.tandfonline.com/doi/abs/10.1080/10463283.2024.2334563) — MEDIUM
- [Protocol for MCII/WOOP trial in VA's MOVE! weight management program](https://www.sciencedirect.com/science/article/abs/pii/S155171442400106X) — MEDIUM
- [Full article: If-then planning](https://www.tandfonline.com/doi/full/10.1080/10463283.2020.1808936) — MEDIUM

**Streak mechanics:**
- [App Teardown: How Duolingo's Streak Mechanic Actually Works](https://apptitude.io/blog/how-duolingos-streak-mechanic-actually-works/) — LOW-MEDIUM (industry blog, but design claims consistent across multiple independent sources)
- [Designing A Streak System: The UX And Psychology Of Streaks — Smashing Magazine](https://www.smashingmagazine.com/2026/02/designing-streak-system-ux-psychology/) — LOW-MEDIUM
- [Duolingo Streaks: How the Mechanic Drives Daily Retention](https://duolingo.deconstructoroffun.com/mechanics/streaks) — LOW-MEDIUM

**Variable reinforcement / dark patterns:**
- [Gamification or Manipulation? Understanding the Ethics of Engagement Loops](https://uxmag.com/articles/gamification-or-manipulation-understanding-the-ethics-of-engagement-loops) — LOW (industry commentary, no citations to primary research)
- [Reinforcement Schedule in the Digital Age (ResearchGate)](https://www.researchgate.net/publication/395115230_Reinforcement_Schedule_in_the_Digital_Age) — MEDIUM (academic, not independently read in full)
- Contingency management literature (operant conditioning applied to substance-use/health-behavior treatment) — MEDIUM, well-established clinical evidence base, but applied here by analogy to consumer software rather than directly tested in that context

**Identity-based framing:**
- [Motivating Voter Turnout by Invoking the Self (Bryan, Walton, Rogers, Dweck, PNAS 2011)](https://www.pnas.org/doi/10.1073/pnas.1103343108) — original effect, MEDIUM
- [A field experiment shows that subtle linguistic cues might not affect voter behavior (Gerber et al., PNAS 2016)](https://www.pnas.org/doi/10.1073/pnas.1513727113) — failed replication, MEDIUM — this pairing is the single most important evidence caveat in this document
- Bem's self-perception theory (1972) — foundational, well-established, cited secondhand via search results (not independently verified against the primary text this session)

**Self-compassion / lapse recovery:**
- [Self-Compassion Research (self-compassion.org, Kristin Neff's own research hub)](https://self-compassion.org/the-research/) — MEDIUM, primary-source-adjacent
- [Dispositional self-compassion and responses to mood challenge in people at risk for depressive relapse (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6221037/) — MEDIUM, peer-reviewed
- [Abstinence Violation Effect (Collins & Witkiewitz, following Marlatt & Gordon 1985)](https://www.researchgate.net/profile/Susan-Collins-6/publication/281298055_Abstinence_Violation_Effect/) — MEDIUM

**Motivational interviewing:**
- [Key Motivational Interviewing Skill: Elicit-Provide-Elicit (UNC Center for AIDS Research)](https://www.med.unc.edu/cfar/2025/11/key-motivational-interviewing-skill-elicit-provide-elicit/) — MEDIUM, institutional source
- [Ask-Offer-Ask! (MI Center for Change)](https://blog.micenterforchange.com/ask-offer-ask/) — MEDIUM

**AI-authored goals:**
- ["Optimized but Unowned: How AI-Authored Goals Undermine the Motivation They Are Meant to Drive" (arXiv preprint, 2026)](https://arxiv.org/pdf/2605.12344) — LOW-MEDIUM, preprint, not peer-reviewed, but directionally consistent with mainstream self-determination-theory literature

**Comparable products:**
- [Stoic app review — customizable companion personas](https://www.mindfulsuite.com/reviews/best-guided-journaling-apps) — LOW-MEDIUM, single review source for the persona-customization claim
- [How AI Mental Health Apps Like Woebot, Wysa & Replika Work](https://sigosoft.com/blog/how-ai-mental-health-apps-like-woebot-wysa-replika-are-attracting-millions-of-users/) — LOW-MEDIUM
- [Woebot shutdown / pivot to enterprise (2025)](https://www.stellalabs.ai/blog/woebot-alternatives-2026) — LOW-MEDIUM, single source, worth independently confirming before citing as a strong claim

**Re-engagement / win-back tone:**
- [Re-Engagement Email Psychology: Why Win-Back Campaigns Fail](https://atticusli.com/blog/posts/re-engagement-email-psychology-win-back-campaigns/) — LOW (marketing-industry source, not habit-specific, used only to corroborate the guilt/approach framing distinction that also appears independently in the AVE literature)

---
*Feature research for: personal AI accountability/companion bot — interaction-depth milestone*
*Researched: 2026-08-01*
