# Psychology Research: Task Decomposition, Initiation, and Untapped Motivational Mechanisms

**Domain:** Personal accountability / behaviour-change conversational agent (Telegram bot, single user, memory-driven relationship model)
**Researched:** 2026-08-01
**Confidence:** MEDIUM overall (see Methodology Note)

**Scope boundary:** This document deliberately does NOT cover implementation intentions (Gollwitzer), mental contrasting/WOOP (Oettingen), streak-freeze mechanics, variable-ratio reinforcement, identity-based framing, or self-compassion after a lapse — a separate researcher owns that ground. This document references it only where a finding here directly depends on it.

## Methodology Note (read before trusting a number)

Findings below come from web search over secondary sources (meta-analyses, RCTs, and reporting on them), not primary-source database access. Under this project's confidence-classification convention, a single web result is LOW confidence; a claim corroborated across multiple independent secondary sources is MEDIUM. No claim in this document reaches HIGH by that convention, regardless of how strong the underlying academic evidence is. So two separate scales are used throughout, and they are not the same thing:

- **Sourcing confidence** (LOW/MEDIUM, per the project's tiering) — how well *this research pass* verified the claim.
- **Evidence strength** (STRONG / MODERATE / WEAK / CONTESTED / FABRICATED) — what the academic literature itself actually shows, stated as plainly as the sources allow, including naming effects that are popular but poorly replicated. This is the scale that matters for design decisions, and it is called out explicitly for every mechanism below.

Treat specific percentages and effect sizes quoted below as illustrative of the direction and rough magnitude of an effect, not as verified exact figures.

---

## PART 1 — Task Decomposition and Task Initiation

### 1.1 Goal-setting theory: subgoals, proximal vs. distal — and where it backfires

**Claim:** Locke & Latham's goal-setting theory (40+ years, one of the most replicated frameworks in organizational psychology) holds that specific, difficult goals produce higher performance than vague or "do your best" goals, and that breaking a distal goal into proximal subgoals raises performance further — mediated by self-efficacy and by the frequent feedback proximal goals provide that a distant goal cannot.

**Evidence strength:** STRONG for simple/well-learned tasks — this is genuinely one of the best-replicated effects in I/O psychology.

**Where it genuinely backfires:** On *complex, unfamiliar* tasks, assigning a specific difficult **performance** goal (e.g., "get X done by Friday") can *reduce* performance relative to "do your best," because people tunnel-vision on the outcome and skip the exploration needed to learn how to do the task at all (Kanfer & Ackerman's air-traffic-controller simulation is the classic demonstration). Latham's own follow-up (Winters & Latham) found the fix isn't abandoning goal-setting — it's changing goal *type*: a specific, difficult **learning** goal ("find three approaches and see what breaks") restores and even exceeds the benefit, on the same complex task, that a performance goal destroys.

**Bot behavior:** When decomposing a task the user hasn't done before, default the subgoals to *learning/exploration* framing rather than *fixed-output* framing, until there's evidence (past completions, user's own words) that the task is familiar. This is a decision rule the bot can apply silently — it doesn't need to explain the theory, just phrase the first attempt at something novel as discovery rather than delivery.

> "Since you haven't scoped one of these before — want the first pass to just be 'figure out what the three ugly parts are,' rather than trying to nail the whole thing today?"

**Conflict with Out of Scope:** None directly, but this reinforces "ask permission before giving advice" — the learning-vs-performance framing should be offered as a question, not silently imposed.

---

### 1.2 Why people fail to *start* — procrastination, aversiveness, and the initiation bottleneck

**Claim:** Steel's 2007 meta-analysis (Psychological Bulletin, ~691 correlations) is the field's benchmark. Its strongest, most consistent predictors of procrastination are **task aversiveness**, low self-efficacy, impulsiveness, and low conscientiousness (specifically self-control, organization, distractibility) — task aversiveness is on that short list of top predictors, ahead of most other candidate causes tested. Temporal Motivation Theory (Steel & König, building on hyperbolic discounting and expectancy theory) formalizes why: motivation ≈ (expectancy × value) / (1 + impulsiveness × delay) — utility of a reward or of avoiding pain collapses non-linearly as it's pushed into the future, which is why "I'll feel bad about this eventually" fails to compete with "I feel bad about starting right now."

**Evidence strength:** STRONG — Steel (2007) is a large, well-cited meta-analysis, and TMT is a synthesis of established sub-literatures (expectancy-value theory, hyperbolic discounting) rather than a novel untested claim.

**Initiation vs. completion are genuinely separable.** Heckhausen's "Rubicon" action-phase model distinguishes a *pre-decisional* phase (deciding whether to pursue a goal at all), a *pre-actional* phase (deciding when/how — this is exactly where implementation intentions live, and is the other researcher's territory), an *actional* phase (doing it), and *post-actional* evaluation. The empirical procrastination literature confirms these are different failure points with different remedies: a person can be fully committed to a goal (past the Rubicon) and still stall indefinitely at the pre-actional/actional boundary because the *first concrete action* is aversive, ambiguous, or effortful — none of which is a motivation-toward-the-goal problem.

**Practical implication for decomposition:** because aversiveness (not difficulty per se, not lack of importance) is the single strongest driver, a bot probing why a task is stalled should ask about the *unpleasant feeling* attached to starting before it asks about steps or scheduling. Decomposition that ignores aversiveness just produces a well-organized list of things the person still doesn't want to touch.

**Bot behavior:**
> "What's the part of this that makes you not want to open it?" — asked *before* "what are the steps," when a task has sat untouched for several days.

**Conflict with Out of Scope:** None. This is consistent with "ask with genuine curiosity" rather than diagnosing from outside.

---

### 1.3 Ambiguity is not difficulty — and decomposition only cures one of them directly

**Claim:** Task-characteristic research on procrastination treats ambiguity/uncertainty about *what* is required, and unstructuredness about *how* to proceed, as distinct from objective difficulty. When a task is vague, there is no clear entry point for goal-directed action at all — this produces avoidance even when the task, once defined, would be easy. Faculty-procrastination field studies link this specifically to perceived clarity about requirements and next steps, separate from how hard the work itself is.

**Evidence strength:** MODERATE — the ambiguity/difficulty distinction is well-established conceptually and shows up consistently in applied procrastination studies, but this is a thinner, more scattered literature than Steel's meta-analysis, without a single canonical large-N synthesis specifically isolating ambiguity as a factor.

**Does decomposition help both, or just one?** Decomposition addresses **ambiguity directly** — the act of naming "step one" *is* the resolution of "what does this even mean." It addresses **difficulty only indirectly**, by shrinking each piece — the task is still hard, just in smaller doses. This distinction matters for how a bot should choose its opening question:

- If the user doesn't know what the first step would look like → the problem is likely ambiguity. The bot's job is narrow: get to *one* concrete next physical action, nothing more. A full multi-step plan is premature and can itself become a stall (see 1.7).
- If the user can describe the first step clearly but still isn't doing it → the problem is likely difficulty/aversiveness/energy, not ambiguity. Decomposition into smaller pieces, or addressing the aversive feeling (1.2), is the more relevant lever — not more clarification of what's already clear.

**Bot behavior — a one-question diagnostic before choosing an approach:**
> "Do you know what the very first move on this would look like, or is that part of what's unclear?"

**Conflict with Out of Scope:** None.

---

### 1.4 Behavioural activation and the "smallest viable step"

**Claim:** Behavioural activation (BA) is a component of CBT for depression that has been isolated and tested on its own; component-analysis and dismantling studies find BA alone performs comparably to full CBT and to antidepressant medication, including for more severe depression. Its core clinical technique is graded task assignment: start with the smallest achievable version of an avoided activity (the canonical example — "put on the walking shoes" before "go for a walk") and let completing it be reinforcing in itself, which is what disrupts the avoidance-low-mood cycle.

**Evidence strength:** STRONG for BA as a treatment package (multiple RCTs, large effect sizes reported). MODERATE specifically for "smallest step" as an isolated ingredient — it is the standard operationalization of BA's graded-task technique within an established, evidence-backed therapy, but it hasn't itself been separately RCT'd apart from the BA package it's embedded in.

**Relationship to decomposition:** This is a different move from goal-setting-theory subgoals (1.1). Subgoals divide a *known* plan into milestones for tracking progress. The BA "smallest step" instead deliberately looks for the version of the task so small it can't be aversive enough to avoid — it's a floor, not a checkpoint. For a stalled task, asking "what are the five steps" and asking "what's the two-minute version" are different interventions, and the second is usually the right one first.

**Bot behavior:**
> "Forget the whole thing for a second — what's the two-minute version of starting this?"

**Conflict with Out of Scope:** None.

---

### 1.5 The progress principle (Amabile) — and the licensing trap that can undo it

**Claim:** Amabile & Kramer's *Progress Principle* comes from a very large qualitative/quantitative diary study (~12,000 daily entries across 238 employees). Its central finding: the single event most associated with a good day at work, across the dataset, was making visible progress on meaningful work — more than recognition, incentives, or interpersonal support. This is the closest thing this literature has to a direct case for "small wins matter."

**Evidence strength:** MODERATE. This is a large, carefully run field study, but it's correlational (diary self-report, not an experimental manipulation) and it comes from a single research program rather than an independently replicated body of work. Treat "progress on meaningful work is the strongest driver of daily motivation" as well-supported *description*, not as an experimentally isolated causal mechanism.

**The caveat that matters — don't build naive small-wins into the bot.** Fishbach & Dhar's "goals as excuses or guides" line of work shows that perceived progress can cut either way: when progress is interpreted as evidence of *commitment* to the goal, it sustains or increases subsequent effort; when it's interpreted as *sufficient* progress already made, it licenses relaxation on the goal (moral-licensing / self-licensing effects — well replicated in the broader licensing literature, e.g. environmental-behavior and health-behavior studies). Which interpretation wins depends on framing, not on the amount of progress itself.

**Bot behavior:** when acknowledging a completed step, tie it explicitly back to *why the task mattered* (commitment framing) rather than treating it as a closed transaction (license framing) — and never phrase it as a tally ("2 of 5 done"), which invites exactly the license reading and also collides with the metric-first exclusion below.

> Instead of: "Nice, one down!" (invites "good enough for now")
> Prefer: "That's the part you said was going to make the rest possible — how'd it feel to actually get into it?"

**Conflict with Out of Scope:** Direct tension avoided deliberately — a naive "small wins" implementation (tally-style acknowledgment, "X of Y complete") would drift straight into **metric-first weekly reports**, which is explicitly excluded. The framing fix above (tie progress to the *why*, not the count) is what keeps this mechanism inside scope.

---

### 1.6 The Zeigarnik effect: mostly a myth, but there's a real effect hiding underneath it

**Claim (popular version):** Unfinished tasks are remembered better than finished ones, and this nagging memory pressure is what "makes you want to go back and finish."

**Evidence strength: WEAK/CONTESTED for the popular version.** Zeigarnik's original 1927 result has a long history of failed replications, and a comprehensive 2025 meta-analysis specifically re-examining both the Zeigarnik effect (memory advantage for incomplete tasks) and the related **Ovsiankina effect** (a behavioural tendency to spontaneously resume an interrupted task, independent of memory) found **no reliable memory advantage for unfinished tasks** — but did find the Ovsiankina resumption tendency to be a real, general effect. In other words: the famous "it will stick in your mind" claim is not well supported; the less-famous "you'll want to go pick it back up when you get the chance" claim is.

**Why this distinction matters for design:** it means the actionable mechanism isn't "leave something incomplete so it haunts them" (weak, don't build on it) — it's "leave an easy, concrete, visible re-entry point, and people have a genuine pull to resume once one exists." This is a decomposition/initiation tool, not a memory trick: the value of breaking a task into pieces isn't just cognitive load reduction, it's that each finished piece leaves behind a legible next piece to resume from.

**Bot behavior:** when a task session ends without finishing, don't rely on "the bot will remind you it's unfinished" as the motivating force — instead, close the loop with a name for the *next concrete piece*, so resumption doesn't require re-deciding what "continuing" even means.

> "Good place to stop — next time you open this, the next thing is just [X]. Not the whole rest of it."

**Conflict with Out of Scope:** None, but be careful not to accidentally reintroduce a guilt-flavored "you left this unfinished" framing while implementing this — that would collide with the "you didn't do X" register exclusion.

---

### 1.7 The planning fallacy and decomposition — get the direction of the effect right

**Claim:** The planning fallacy (Kahneman & Tversky, extensively replicated by Buehler et al. and others — this is one of the most robust biases in judgment-and-decision-making research) is the tendency to underestimate how long tasks will take, driven by focusing on the plan rather than on base rates or past experience.

**What decomposition actually does to estimates — and why this is good news, correctly understood.** Kruger & Evans' "unpacking the planning fallacy" work found that asking people to decompose a task into its procedural components *before* estimating **increases** their time estimate — and this increase moves the estimate *closer to the true (also larger) completion time*, cutting the size of the planning-fallacy bias substantially. This is the finding the question flags as needing care: the estimate going *up* after decomposition is not the bias reasserting itself, it's the bias being *corrected* — holistic, "packed" estimates silently omit steps the person isn't consciously picturing; naming them surfaces the omitted time.

**Boundary conditions (where it stops helping or gets noisy):**
- Unpacking helps less when the task genuinely has few components, when the components are trivial to execute, or when the task is far in the future (people don't unpack distant tasks as concretely).
- The more complex the task, the more decomposition helps — so this is a technique to reach for on multi-step or unfamiliar work, not on a one-line errand.
- There's a separate, subtler risk in the *opposite* direction: unpacking a time interval into components can make each component feel like it deserves its own buffer, and separately, unpacked/decomposed time intervals are perceived as *subjectively longer* than the same interval left whole — so exhaustive decomposition (naming every sub-sub-step) can overshoot into padding rather than correcting. The practical fix used in the literature and in coaching practice is a handful of chunks (roughly 3–5), not an exhaustive breakdown.

**Bot behavior:** when a task looks multi-step and the user offers a time estimate or deadline, ask for the 3–5 major pieces *before* locking in that estimate — but skip this step entirely for short, well-understood, single-step tasks, where it adds friction without correcting anything.

> "Before we pin a date on this — if you had to name the 3 or 4 chunks this actually breaks into, what would they be?"

**Conflict with Out of Scope:** None.

---

### 1.8 When decomposition itself becomes the procrastination

This is the failure mode the question explicitly asks about, and it has a name in the applied literature even though it isn't a single famous study: **"procrasti-planning" / planning-as-avoidance.** Detailed planning activates the same goal-related cognition as doing the task, and produces a mild, partial sense of goal progress (see 1.5's licensing mechanism) *without* the discomfort or risk of actually attempting it — so planning can become a psychologically comfortable substitute for the harder step of starting. This connects directly to the licensing caveat above: repeatedly re-planning a task can function exactly like the "sufficient progress, ease off" license, except the "progress" was never real.

**Evidence strength:** WEAK-MODERATE as a formally named, separately-tested effect (it is described consistently across procrastination and coaching literature, but doesn't have a canonical large-N study of its own the way Steel (2007) or Buehler et al. do — treat it as a well-argued, widely observed pattern rather than a hard number).

**The detectable signature a bot can actually use:** the diagnostic isn't "the user is planning" (planning is often useful and requested) — it's *repeated* planning of the *same* task with *no reported action in between*. That repetition is the tell.

**Bot behavior:** track (in the retrieval/memory layer, not a visible counter) whether a task has been decomposed or discussed multiple times with no evidence of the first step having been attempted. When that pattern shows up, stop offering to decompose it again and shift the question instead.

> "We've talked through how to start this a couple of times now — I don't think another plan is what's missing. What's actually stopping the very first piece?"

**Conflict with Out of Scope:** None — this must be phrased as curious observation, never as "you keep failing to do X," which is the excluded register.

---

### 1.9 Eliciting decomposition conversationally, without an interrogation

Two practical traditions converge on the same answer here: coaching psychology's GROW model (Whitmore) — Goal, Reality, Options, Will, worked through one open question at a time rather than a fixed checklist — and motivational interviewing's insistence on evocation over installation (detailed in Part 2.1). Both converge on the same design rule for a conversational agent:

- **One open question per turn, chosen from what the user's last message left unanswered** — not a fixed sequence (due date → obstacle → first step → time estimate) marched through regardless of what's already been said. A fixed sequence is what makes decomposition feel like a form.
- **Let the user's own words supply the next probe.** If they say "I guess I'd start by outlining it," the next natural question is about *when*, not a generic "what's step two" — the checklist mentality is what turns curiosity into audit.
- **Invite, don't demand.** "What would make this feel more doable?" opens the door; "list the steps" is a chore. The former is also more consistent with the product's autonomy-and-permission stance already in scope.
- **Know when to stop asking.** If the task is genuinely ambiguity-limited (1.3), one clarifying question that produces a concrete first step is enough — continuing to probe for the *whole* plan at that point risks manufacturing exactly the procrasti-planning pattern in 1.8.

**Conflict with Out of Scope:** Directly supports "ask permission before giving advice" and the anti-interrogation instinct behind removing inline keyboards — a fixed decomposition checklist is the conversational equivalent of the button grid the product has already rejected.

---

## PART 2 — Mechanisms Not Already on the List, Ranked by Expected Value for This Product

Ranking criteria: strength of evidence, cheapness of implementation given the existing architecture (Telegram text conversation, long-term per-user memory, scheduler-driven touchpoints, no buttons, no metrics-first reporting), and fit with the product's stated thesis (relationship over metrics, autonomy, curiosity).

| Rank | Mechanism | Evidence strength | Why it ranks here |
|---|---|---|---|
| 1 | Motivational interviewing spirit (change talk, righting reflex, rolling with resistance) | MODERATE-STRONG (meta-analytic support in health/addiction contexts; mechanism generalizes) | Directly attacks the product's single biggest risk — the bot nagging or arguing someone into compliance |
| 2 | Journaling as expressive writing, with rumination-risk-aware prompting | MODERATE-STRONG (multiple meta-analyses) | Feature already exists (`/journal`, `/reflect`) — this is depth, not a new feature |
| 3 | Self-Determination Theory: competence and relatedness (not just autonomy) | MODERATE-STRONG (large workplace/health literature) | Cheap: mostly a phrasing/behavior discipline, not new engineering |
| 4 | Affect labelling | MODERATE (well-replicated neuro + behavioral effect, narrow paradigm) | One-line technique, fits naturally into check-in conversation |
| 5 | Fresh start effect | MODERATE (field/archival evidence, not experimental) | Nearly free — only needs date-awareness the bot already has |
| 6 | Episodic future thinking | MODERATE (strong lab evidence, thinner applied/field evidence) | Powerful fit for "why-anchoring," but harder to prompt naturally in a text medium |
| 7 | Bot-as-accountability-witness (careful framing only) | WEAK-MODERATE (the famous stat here is partly fabricated — see below) | Real mechanism, but must avoid tally-style implementation to stay in scope |
| 8 | Temptation bundling | MODERATE (good field experiments) but LOW fit | Needs a "want" activity to pair with a "should" activity; awkward in a pure-text medium |
| 9 | Habit stacking / cue-based, context-dependent repetition | STRONG evidence, LOW fit today | The bot has no access to physical/environmental cues (no location, no calendar) to hook into |
| — | Rejected outright (see closing section) | — | Gamification/points, financial commitment contracts, literal social/peer accountability |

### 2.1 Motivational interviewing (MI): change talk, the righting reflex, rolling with resistance

**Mechanism:** MI (Miller & Rollnick) is a clinical counseling style built on the observation that people talk themselves into change more durably than they get talked into it. Its core constructs:
- **The righting reflex** — the counselor's/helper's automatic urge to fix, correct, or argue for change, which reliably provokes the opposite: the person defends the status quo out loud (sustain talk), and *arguing your own position out loud strengthens it*.
- **Change talk** — a person's own statements in favor of change (desire, ability, reasons, need); eliciting and reflecting it back, rather than supplying reasons for them, is the mechanism MI is built around.
- **Rolling with resistance** — not contradicting reluctance directly; reflecting it, exploring it, or reframing it, rather than pushing against it.

**Evidence strength:** MODERATE-STRONG. Meta-analyses in addiction, health behavior, and adherence contexts consistently find MI effective, and — importantly — mediation analyses have specifically shown that *client change talk mediates the link between counselor MI-consistent skill and behavior change*, which supports the theorized mechanism, not just the outcome. Most of the strongest trials are in clinical/health domains (substance use, smoking, medication adherence) rather than everyday productivity coaching, so generalization to "get the report done" is plausible but less directly tested.

**Concrete bot behavior:** never argue for the task. If the user expresses ambivalence or resistance ("I don't even know why I'm bothering with this"), reflect it back and ask what's still working in favor of it, rather than supplying reasons or restating the deadline.

> Instead of: "This is important, you said so yourself last week — you should really get started."
> Prefer: "Sounds like the appeal's gone out of it right now. What was it about this that mattered to you when you started?"

This is a direct, structural answer to why this bot must not nag — MI gives a specific, tested technique (reflect + evoke, don't argue) rather than just "be nice about it."

**Conflict with Out of Scope:** None — actively reinforces "ask with genuine curiosity" and the ban on the guilt-inducing register. Worth flagging as a design *principle* (how the bot argues, or rather doesn't) more than a single feature.

### 2.2 Journaling as expressive writing — and the rumination risk the product should design against

**Mechanism:** Pennebaker's expressive writing paradigm (write about a stressful experience and your deepest thoughts/feelings about it) has produced a large body of RCT evidence, meta-analyzed multiple times, generally showing benefits for physical and psychological outcomes (mood, physician visits, processing of difficult events). The mechanism researchers point to is **causal and insight language** — writing that moves from raw venting toward "this happened because..." / "what I realize is..." predicts better outcomes than writing that stays in undifferentiated distress.

**The risk side, explicitly requested:** the broader rumination literature (Trapnell & Campbell's distinction between *rumination* — neurotic, repetitive, anxious self-focus — and *reflection* — curious, exploratory self-focus) shows these produce opposite outcomes despite looking similar on the surface (both are "thinking about yourself"). Journaling prompts that just ask "how do you feel about X" repeatedly, without ever moving toward causal/meaning-making language, risk reinforcing rumination rather than resolving it — this is a real and specifically documented failure mode of unstructured self-focused writing, not a hypothetical concern.

**Concrete bot behavior:** the bot's existing `/journal` AI-reflection and `/reflect` prompts should be biased toward causal/meaning-making follow-ups ("what do you think that was about," "what does that tell you about what you actually want here") rather than repeated affect-mirroring ("that sounds hard," "how did that make you feel") when the same negative theme recurs across entries without any forward movement. A recurring same-toned entry with no causal or forward-looking language over several instances is the detectable trigger to gently shift register — offer a different angle rather than mining the same distress for more detail.

> Instead of (third time this theme appears, same tone): "That sounds really frustrating."
> Prefer: "This is the third time this has come up feeling the same way — what do you think is actually underneath it?"

**Conflict with Out of Scope:** None — this is depth on an existing feature ("smarter, not bigger"), and stays clear of metric-first framing since it's about the *content* of reflection, not a count of entries.

### 2.3 Self-Determination Theory: competence and relatedness, not just autonomy

**Mechanism:** Ryan & Deci's SDT posits three basic psychological needs — autonomy, competence, relatedness — whose satisfaction predicts intrinsic motivation and wellbeing; frustration of any one predicts disengagement. The product's existing design already leans hard on **autonomy** (user-set pressure dial, permission-before-advice, open questions over buttons). **Competence** and **relatedness** are comparatively unused levers:
- Competence is satisfied by a sense of being effective and by *informational* (not controlling) feedback — feedback that helps you see your own effectiveness, versus feedback that judges or pressures.
- Relatedness is satisfied by feeling genuinely known, cared about, and safe to be honest with someone (or something) — psychological safety, not just contact frequency.

**Evidence strength:** MODERATE-STRONG — this is a very large, cross-domain literature (workplace, health, education) with consistent associations between all three needs and engagement/wellbeing outcomes, though as with most social-psychological theory much of the evidence is correlational/field-based rather than tightly controlled experimental isolation of each need separately.

**A notable point in this product's favor:** RCTs of the Woebot conversational agent (delivering CBT-style content entirely via chat, no human in the loop) found measurable reductions in depression/anxiety symptoms and meaningful self-reported working-alliance/engagement with the bot itself. This is direct evidence that a text-only conversational agent *can* satisfy something like relatedness — people do form a real working relationship with an AI interlocutor, not just a human one — which is direct support for this product's core premise that memory-driven warmth from a bot is a real, not merely simulated, motivational lever.

**Concrete bot behavior — competence:** give informational, specific feedback tied to what the person actually did (not evaluative praise, and not statistics) — "the way you handled X" rather than "good job" or a completion percentage.
**Concrete bot behavior — relatedness:** let the bot occasionally initiate genuine, non-instrumental curiosity about the user's life that has no task attached — already partially covered by "off-topic conversation is welcome," but worth extending to *proactive* curiosity, not just tolerance of the user going off-topic.

> Competence: "You caught the edge case that broke it last time before it even shipped — that's the kind of thing that used to slip through for you."
> Relatedness (unprompted, no task framing): "Random thought — how did that thing with [X] end up going? You mentioned it a few weeks back and I never heard the end of it."

**Conflict with Out of Scope:** None — relatedness-driven unprompted check-ins overlap conceptually with the already-planned "unprompted recall" requirement; this section gives it theoretical grounding rather than duplicating it.

### 2.4 Affect labelling

**Mechanism:** Lieberman et al.'s "Putting Feelings Into Words" line of work shows that naming an emotion in words (rather than just experiencing or suppressing it) reduces amygdala reactivity and self-reported distress, functioning as a form of *implicit* emotion regulation — it works even without any explicit reappraisal effort, just from the act of labelling.

**Evidence strength:** MODERATE. This is a well-replicated effect within its own experimental paradigm (viewing negative images, labelling the emotion vs. other encoding tasks), with converging fMRI and self-report evidence from the same research group and some independent replication — but the paradigm is a controlled lab task, and evidence for the effect transferring cleanly to everyday conversational check-ins is thinner (plausible extrapolation, not a directly tested claim).

**Concrete bot behavior:** when a user expresses distress or frustration about a stalled task, the single highest-value move before problem-solving is inviting them to name the feeling, not immediately pivoting to what to do about it.

> "Before we figure out what to do with it — what's the actual feeling when you look at this? Dread, boredom, something else?"

This also functions as free diagnostic information for 1.2 (aversiveness) and 1.3 (ambiguity vs. difficulty) — naming the feeling often reveals which barrier is actually in play.

**Conflict with Out of Scope:** None.

### 2.5 The fresh start effect

**Mechanism:** Dai, Milkman & Riis found (across multiple large archival field studies — Google search trends for "diet," gym visits, goal-commitment behavior) that aspirational behavior spikes right after temporal landmarks: new week, new month, new year, a birthday, a holiday. The proposed mechanism is mental accounting — landmarks let people file past setbacks into a "previous chapter" and adopt a bigger-picture, more aspirational self-view, at least temporarily.

**Evidence strength:** MODERATE. Strong, consistent archival/field evidence across several independent datasets from the same research team; less evidence of it being independently replicated by unrelated labs, and it's correlational (natural landmarks, not randomly assigned) rather than a true experiment.

**Concrete bot behavior:** this is close to free to implement — the bot already has timezone-aware date logic. On the first genuine contact after a natural landmark (Monday morning, first of the month, the user's birthday if known, after a real absence), frame re-engagement around a fresh-chapter feeling rather than picking up exactly where things left off — this also gives a natural, evidence-backed shape to the already-planned "graceful return after an absence" requirement, beyond just "no guilt."

> "New week — anything you want this one to be about, before we look at what's sitting on the list?"

**Conflict with Out of Scope:** None — must stay narrative ("what do you want this chapter to be"), not a checklist review of the prior period, to avoid drifting toward the excluded "you didn't do X" register or a metrics recap.

### 2.6 Episodic future thinking

**Mechanism:** Peters & Büchel's foundational neuroimaging work showed that vividly imagining a specific personal future event (rather than thinking abstractly about "the future") reduces delay discounting — people become measurably more willing to wait for larger later rewards over smaller immediate ones when a concrete future scene is active in mind, with the effect scaling with imagined vividness.

**Evidence strength:** MODERATE-STRONG in the lab (converging behavioral and fMRI evidence, some independent replication in clinical/health populations such as prediabetes and substance-use samples testing it as an intervention). Applied field evidence (does this work reliably delivered by a chatbot in ordinary conversation, outside a lab task) is thinner.

**Why this fits the product's thesis specifically:** the product's core premise is remembering *why* something mattered and surfacing that at the moment motivation dips (why-anchoring, already in scope). Episodic future thinking is the more forward-looking sibling of that: instead of just recalling *why this mattered in the past*, prompting a vivid, specific, personal image of a *future* moment (finishing it, or a related future event it enables) engages the same discounting-reduction mechanism as the lab paradigm, and does so in exactly the conversational, memory-driven format this bot already has.

**Concrete bot behavior:** when motivation is visibly low on a delayed-payoff task, invite a specific, sensory, personal future scene rather than an abstract benefit statement.

> Instead of: "Finishing this will really help you long-term."
> Prefer: "Picture actually sending it — what does that Tuesday look like for you?"

**Conflict with Out of Scope:** None, but note this is easy to do badly (turning into generic "visualize success" self-help patter) — the specificity (a real, personal, sensory scene, ideally using details the bot actually remembers about the user) is what the evidence says matters, not the mere instruction to imagine.

### 2.7 Bot-as-accountability-witness — a mechanism worth flagging, but implement with real caution

**Mechanism:** The general finding that stating a commitment to another party and reporting progress back to them increases follow-through has some genuine backing (Gail Matthews' Dominican University study: participants who wrote goals, formed action commitments, and sent weekly progress reports to a contact reported substantially higher goal achievement than those who kept goals private).

**Evidence strength: WEAK-MODERATE, and this is the section where the "popular but poorly evidenced" instruction matters most.** Two things to say plainly:
1. The widely circulated "95% success rate from a single accountability appointment," attributed to "ASTD," is **fabricated** — there is no traceable published study behind it; it's a well-documented case of a made-up statistic propagating through recursive citation (in the same family as the also-fabricated "1979 Harvard/Yale goal-writing study" myth). If this number shows up in any adjacent reading, do not build on it.
2. The real study (Matthews, ~267 participants, five conditions) is legitimate but is a conference presentation, not a peer-reviewed publication, and does not appear to have an independent replication. Treat the effect ("public commitment + regular reporting improves follow-through") as plausible and consistent with the broader social-psychology literature on commitment and consistency (Cialdini), but treat the specific percentages as unverified.

**Why it's ranked mid-table rather than higher:** the mechanism the bot could exploit here is *already partially active by construction* — the bot is the "someone" the user is implicitly reporting to, every time it asks. The open design question isn't whether to add this mechanism, but whether to make the commitment-and-recall loop *explicit* (naming a stated intention out loud and referencing it later) without letting it collapse into a metric.

**Concrete bot behavior:** when a user states an intention in passing ("I'll sketch it out tonight"), the bot can store and later reference that specific stated commitment narratively — but the follow-up must reference the *content* of the commitment, never a completion count or streak of commitments kept/broken.

> Good: "You mentioned sketching it out tonight — how'd that go?"
> Bad (metric-first, excluded): "You've kept 4 of your last 6 stated commitments."

**Conflict with Out of Scope:** Real tension exists here — the naive version of this mechanism (a running ledger of commitments made vs. kept) is precisely the "metric-first weekly report" the project excludes, and could easily slide toward the excluded "you didn't do X" register if a broken commitment is referenced clumsily. If built, it should reference at most one specific, recent stated intention at a time, conversationally, never as an aggregate.

### 2.8 Temptation bundling — lower fit, noted for completeness

**Mechanism:** Milkman et al.'s field experiments (locking a compelling audiobook so it's only accessible during gym visits) show that restricting an enjoyable "want" activity to only be available alongside a valuable "should" activity increases engagement with the should activity — visits increased substantially in the restricted-access condition, with a follow-up field experiment replicating a smaller but still meaningful effect when *teaching* the technique rather than physically enforcing it.

**Evidence strength:** MODERATE-STRONG for the original gym paradigm (multiple field experiments, one from an independent-ish follow-up team, real behavioral outcome data, not self-report); weaker for whether the effect holds once you remove physical restriction and just rely on the person to self-enforce the bundle (this is closer to this bot's situation, since a chat bot can't lock anything).

**Why it ranks low here:** the mechanism depends on the user having a specific, bundle-able "want" activity to pair with the avoided task, and on some enforcement of the contingency — neither of which a text-based conversational bot naturally provides or can enforce. It's a real technique, but it's a poor structural fit for this medium; it would work better as advice the bot occasionally *suggests* the user set up themselves ("what's something you only let yourself do while working on this?") than as something the bot itself can mechanically deliver.

**Concrete bot behavior (modest, suggestion-only):**
> "Is there something you actually enjoy that you could only let yourself do while this is running — even something small?"

**Conflict with Out of Scope:** None, but not worth prioritizing given the fit problem above.

### 2.9 Habit stacking / cue-based, context-dependent repetition — strong evidence, weak fit today

**Mechanism:** Lally et al.'s field study of real-world habit formation (the source of the popular "66 days" figure, though the true range is wide — roughly 18 to 254 days depending on the person and behavior) and the surrounding habit literature (Wood, Gardner) converge on: habits form through repetition **tied to a stable, salient contextual cue** (a place, a preceding action, a time-of-day anchor), and the cue — not willpower or motivation — is what eventually triggers the behavior automatically.

**Evidence strength:** STRONG — this is a well-established, reasonably replicated area, more so than several others in this document.

**Why it ranks low for this product specifically:** the bot has no access to the physical or environmental cues this mechanism depends on — no location data, no calendar integration, no sensor of "you just sat down at your desk." Without a cue the bot can actually detect or help the user attach to, "habit stacking" advice from a text-only assistant is generic self-help ("do it right after brushing your teeth") that the user has to self-monitor entirely, which is a weak implementation of a strong theory. This is a good candidate to flag as *not worth building now* rather than force into the current architecture — it would become worth revisiting if the product ever gained any contextual/location signal.

**Conflict with Out of Scope:** Consistent with "adding more features for their own sake" being excluded — this is exactly the kind of well-known technique that's genuinely evidence-backed but a poor fit for what this bot can currently sense or enforce.

---

## Explicitly Rejected (well-known, not worth building)

- **Gamification: points, badges, levels.** No meta-analytic evidence that extrinsic token systems outperform intrinsic motivation for sustained behavior change, and considerable SDT-grounded evidence that externally imposed reward systems can *crowd out* intrinsic motivation for tasks a person already found meaningful. Also reads as exactly the kind of "watching the metric rather than the person" behavior the project's Out of Scope explicitly rejects.
- **Financial commitment contracts (e.g., stakes.com-style "pay if you fail").** Some genuine field-experiment support exists for these in other domains (weight loss, smoking), but they require a payment/stakes infrastructure this single-user, no-monetization product has no reason to build, and they reintroduce exactly the punitive framing ("you didn't do X, now you pay") the project's tone constraints rule out.
- **Literal social/peer accountability partner (a second human in the loop).** The underlying mechanism (2.7) is real but requires an actual second person; this product is explicitly single-user today and not being designed for scale. The Woebot evidence (2.3) suggests the bot itself can partially substitute for this via a genuine (if asymmetric) working alliance — that's the more relevant and buildable version of this idea, already captured above.

---

## Summary for Roadmap Use

**Highest-leverage, cheapest-to-build items:** motivational interviewing's core discipline (reflect and evoke, never argue), causal-language-biased journal follow-ups, competence/relatedness phrasing habits, affect labelling as a standard first move on stalled tasks, and fresh-start-aware re-engagement — none of these require new data sources or architecture, only phrasing and prompt-construction discipline in `build_system_prompt` and the check-in/follow-up logic.

**Highest-leverage decomposition-specific behaviors:** diagnose ambiguity-vs-difficulty with one question before choosing an approach; default new/unfamiliar work to learning-goal framing; ask for the "two-minute version" before asking for a full plan; cap decomposition at 3–5 chunks and skip it entirely for short tasks; detect and interrupt repeated re-planning of the same task with no attempted first step.

**Weakest, most over-claimed popular ideas surfaced in this pass:** the Zeigarnik "unfinished tasks nag at your memory" claim (real effect is resumption tendency, not memory), and the "95% success from an accountability appointment" statistic (fabricated). Both are worth naming explicitly in any downstream requirements doc so they aren't accidentally cited as justification later.

**Lowest-fit-for-now, evidence-strong techniques to defer rather than force:** habit stacking/cue-based automaticity (no contextual cue source), temptation bundling (no enforcement mechanism in a chat medium).
