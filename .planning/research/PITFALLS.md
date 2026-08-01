# Pitfalls Research

**Domain:** AI companion/accountability bot with long-term memory, semantic retrieval, and synthesized behavioral modeling of a real person (plus a second, separate OAuth-retrofit workstream on the MCP server)
**Researched:** 2026-08-01
**Confidence:** MEDIUM overall — cross-checked against Anthropic's persona-vector research, the official MCP 2025-06-18/2025-11-25 authorization spec, published memory-agent benchmarks (Mem0/Letta/Zep), and multiple independent OAuth account-takeover case studies. Individual UX claims (button-removal failure mode, YAGNI-for-solo-devs) are LOW confidence — general software wisdom, not domain-verified — and are flagged as such below.

## Critical Pitfalls

### Pitfall 1: The model of the user asserts a pattern that isn't there

**What goes wrong:**
Asked to "synthesize behavioral regularities," an LLM will produce a confident narrative even when the underlying signal is thin, coincidental, or a single data point dressed up as a trend ("You always skip meditation after a bad night's sleep" from three data points, two of which don't actually correlate). This is not the model lying — confabulation research on personal-sensing data describes it as the model filling low-signal gaps with the most statistically plausible-sounding story, delivered with the same confident register as a well-supported claim. The failure is invisible to the user in the worst way: an incorrect observation about your own life is *harder* to reject than an incorrect fact about the world, because you have no independent ground truth to check it against except your own contested memory — and the bot has just told you it remembers better than you do.

**Why it happens:**
The synthesis prompt (`_check_milestones`-style periodic job, or whatever generates "the model of the user") asks the LLM to find patterns. Pattern-finding over a small, noisy personal corpus (a handful of journal entries, some tracker logs) has no natural stopping condition — the model doesn't have a way to say "n=4, this could easily be noise" unless the prompt and the downstream renderer force it to. Overconfidence on generated claims is a documented, general property of LLM outputs, not specific to careless prompting.

**How to avoid:**
- Every synthesized observation must carry (a) the literal evidence it's based on — specific journal entries/dates/tracker readings, not a paraphrase — and (b) a mechanically computed confidence tier (e.g. "how many independent occasions support this" → LOW/MEDIUM/HIGH), not an LLM-self-reported confidence, which is exactly as unreliable as the claim itself.
- Set a minimum evidence count before an observation is allowed to surface at all (e.g. 3+ independent occasions, spanning more than one week) — small-n patterns get logged internally but not surfaced to the user.
- Present observations as hypotheses the user can confirm or reject, not verdicts — the PROJECT.md requirement "the model of the user is visible and editable by the user" is the correct mechanism; make disagreement cheap and expected, and treat a user correction as strong negative evidence, not a UI dismissal.
- Never let the observation-generation step and the evidence-retrieval step be the same LLM call with no verification in between — retrieve evidence first (structured query, not vibes), then generate narrative *only* from what was retrieved, then check that every claim in the narrative traces to a retrieved item before storing it.

**Warning signs:**
- Observations reference vague timeframes ("often," "usually") instead of specific dates.
- The user pushes back on an observation and the bot doubles down or reframes rather than retracting.
- Two consecutive synthesis runs produce confidently different "regularities" from mostly overlapping data.

**Phase to address:**
The phase that builds "model of the user" synthesis — this is the single highest-consequence pitfall in the milestone and should have its own explicit design pass (evidence schema, confidence computation, surfacing threshold) before any prompt-writing.

---

### Pitfall 2: Insight delivery reads as surveillance instead of care

**What goes wrong:**
The exact same underlying observation — "you tend to skip habits after poor sleep" — lands as either attentive or invasive depending entirely on framing, timing, and whether the user asked for it. Research on AI companion apps (Replika, Character.AI) finds that the privacy backlash concentrates specifically around *unprompted* probing and the sense that the app "knows too much" without the user having volunteered it in that moment; a CHI 2025 taxonomy of harmful companion-app behaviors names this as a distinct harm category, separate from factual accuracy. This project's own population is a mental-health-adjacent use case by design (accountability, motivation, habits) — the same literature notes heavy companion-app users skew toward people already dealing with health/emotional difficulty, which raises the cost of getting this wrong for exactly the audience the bot is trying to help.

**Why it happens:**
Once a "model of the user" and unprompted recall both exist, there is a structural temptation to *use* the modeling — to prove the bot remembers, per the project's own Core Value ("the bot must know you, and prove it at the moment it matters"). But "proving it" by volunteering a psychological observation is a different act than proving it by referencing a fact the user told the bot directly (e.g. "how's the thing with your manager going" is warm; "I've noticed your motivation drops on days after conflict with authority figures" is clinical, even if both are "true").

**How to avoid:**
- Distinguish two categories of recall in the design itself: *factual recall* ("you mentioned X last week") is always safe to volunteer; *pattern/psychological observation* ("I've noticed a pattern...") should default to something the bot offers permission to explore, not asserts — consistent with the project's own "ask permission before giving advice" requirement, which should extend to "ask permission before naming a pattern."
- Never surface a synthesized behavioral observation as an unprompted opener (e.g. not as the first line of a morning check-in). Let it surface in response to relevant context, or when the user is already discussing the topic it bears on.
- Keep the register descriptive of the *behavior*, never diagnostic of the *person* — "you've mentioned tiredness before low-motivation days a few times" is data; "you struggle when tired" is a character judgment. The project's existing "never say 'you didn't do X'" tone constraint is the right instinct; extend it explicitly to pattern-naming.
- Give the user a standing way to say "don't go there" per-topic (extends the existing "never-do rules" persona configuration) — sleep, weight, mental health, and relationships are the highest-sensitivity domains and should be opt-in for pattern commentary even if the bot has the data.

**Warning signs:**
- A synthesized observation is delivered with no context in the same message (i.e. it's the whole message, not embedded in a relevant conversation).
- The bot references a pattern about a topic the user has never explicitly invited comment on.
- User responses to a delivered observation trend toward short/deflecting rather than elaborating — an early, cheap signal to check for periodically.

**Phase to address:**
Same phase as Pitfall 1 (model-of-user synthesis) plus the persona-configuration phase (never-do rules, permission-asking) — these two requirements need to be designed together, not sequentially, because the delivery mechanism is what makes the underlying data safe or not.

---

### Pitfall 3: Memory contradicts itself and nobody notices until the user does

**What goes wrong:**
The user says in January "I'm trying to quit coffee" and in June "I've been drinking coffee again, it's fine now." Naive retrieval (semantic or substring) can surface the January fact as if current, and a model asked to "use retrieved memory" has no innate mechanism to know one supersedes the other — it will happily reference the stale fact, or worse, hold both simultaneously and produce an incoherent response. Published benchmarks for exactly this problem (memory-conflict resolution in long-term agent memory systems — Mem0, Letta/MemGPT, Zep/Graphiti) show industry-standard frameworks resolve stale-vs-current conflicts correctly only 7%–28% of the time. This is not a solved problem to casually adopt a library for; it needs explicit handling.

**Why it happens:**
Retrieval systems are built to find *relevant* content, not *current* content — relevance and recency are different axes, and semantic similarity search has no concept of time at all unless it's explicitly added. The literature's converging recommendation is specific: do not ask the LLM to adjudicate which fact is current at generation time (it will guess, same failure mode as Pitfall 1); resolve conflicts deterministically before the fact ever reaches the prompt, using timestamps.

**How to avoid:**
- Every piece of retrievable memory (journal entry, synthesized observation, profile fact) needs a timestamp that participates in retrieval, not just in storage — retrieval ranking should weight recency alongside semantic similarity, not semantic similarity alone.
- For durable "profile" facts specifically (the existing `save_memory(profile)` tool, which the CLAUDE.md documents as permanent) — add explicit supersession: when a new profile fact conflicts with an old one, mark the old one invalidated with a timestamp rather than silently accumulating both. Don't delete it (it's useful history — "used to want X, now wants Y" is itself a fact worth having), but never let a live retrieval surface an invalidated fact as current without labeling it as past.
- When multiple retrieved memories touch the same topic with different timestamps, the system prompt assembly (`build_system_prompt`) should present them in chronological order with dates attached, so the model at least has the information needed to notice the contradiction itself, rather than presenting them as an undifferentiated bag of "relevant context."
- Treat this as a retrieval-layer responsibility, not a prompting trick — the fix belongs in how memory is stored and fetched, not in a "please check for contradictions" instruction to the LLM.

**Warning signs:**
- The bot references something the user explicitly said had changed, in a context where the change was itself journaled.
- `/insights` or `/reflect` output cites two contradictory facts as if both are current without acknowledging the discrepancy.
- Retrieval results returned to the prompt have no visible date ordering.

**Phase to address:**
The retrieval/semantic-search phase (already identified in PROJECT.md as foundational) — timestamp-aware ranking and profile-fact supersession should be part of the retrieval design, not bolted on after the model-of-user phase discovers it needs them.

---

### Pitfall 4: Over-retrieval floods the prompt and the model can't tell memory from the live conversation

**What goes wrong:**
Once semantic retrieval exists, the natural next mistake is retrieving too generously "to be safe" — pulling in 10-15 loosely-related journal entries for every message. Two things go wrong: cost (this bot already runs on a tight rate-limit budget — 30 calls/hour on Groq's free tier — and stuffing retrieved context into every call multiplies token cost for marginal benefit), and worse, attribution confusion — the model can start treating something said in a retrieved January journal entry as if the user just said it in the current turn, especially since (per CLAUDE.md) check-in prompts are already injected into history as user messages rather than system messages, a "known quirk" that already blurs this line once.

**Why it happens:**
Retrieval systems are typically tuned by raising `top_k` until recall "feels good," without a matching discipline for precision or for how retrieved content is labeled once it's in the prompt. There's no natural pressure toward retrieving *less* until the failure (a confused or bloated response) is actually observed.

**How to avoid:**
- Retrieved memory must be wrapped in explicit framing in the prompt ("Past context, for your reference — dated [X]" as a clearly separate block), never interleaved into conversation history as if it were dialogue. This is the single highest-leverage fix and should be a hard rule from day one of the retrieval phase, given the existing check-in-as-history-message quirk already shows this codebase is willing to blur the line.
- Cap retrieval count aggressively for a personal corpus this size (a single user's journal is small — dozens to low hundreds of entries, not millions) — favor precision (top 3-5 most relevant) over recall; this is a low-volume corpus where retrieving too little costs a follow-up question, but retrieving too much costs coherence and money on every single call.
- Budget retrieval token cost as its own line item against the 30-call/hour ceiling — periodic "model of the user" synthesis jobs are a fundamentally different cost shape than reactive retrieval (per-message) and should not share an unexamined budget.

**Warning signs:**
- Responses reference retrieved content with present-tense confidence ("you're currently...") when the source was weeks old.
- Token usage per call climbs noticeably after retrieval ships, with no corresponding quality improvement.

**Phase to address:**
The retrieval/semantic-search foundational phase.

---

### Pitfall 5: The configurable persona collapses into agreeing with everything

**What goes wrong:**
A bot whose tone and accountability pressure are "set by talking to it" is, by construction, a bot that treats user feedback as a tuning signal. Left unconstrained, this is exactly the mechanism sycophancy research describes: models trained/steered toward pleasing the user progressively mirror whatever stance or mood the user brings, and Anthropic's persona-vector work found that even models given an explicit persona in the system prompt drift measurably back toward a generic, agreeable baseline as a conversation lengthens (most models start diverging from an assigned persona within roughly 100 turns). For an accountability bot specifically, this is not a cosmetic problem — sycophantic drift is the mechanism by which "warmer" becomes "will tell you what you want to hear," which is the opposite of what an accountability product is for.

**Why it happens:**
"Configurable via conversation" and "avoid sycophancy" pull in opposite directions unless persona state is architected as data, not as accumulated conversational pressure. If persona adjustment happens by the LLM inferring from the flow of chat that the user wants a softer tone and just... being softer, there is no boundary between "adapting as requested" and "caving because the user pushed back."

**How to avoid:**
- Persona parameters (tone, contact frequency, accountability pressure, never-do rules) must be explicit, stored state — the same pattern the codebase already uses for `llm.model`/`llm.api_key` in `state.json` — set via a recognized *intent to change settings*, not inferred from general conversational drift. "Change my accountability pressure to gentle" is a tool call with a persisted effect; "ugh don't remind me about this again" mid-conversation is not the same speech act and should not silently update persisted state.
- Re-inject the full persona configuration (including never-do rules) into every `build_system_prompt()` call, not just at the start of a conversation — given persona drift is a measured property of long-context conversations, the fix is the same one the literature converges on: active reinforcement every turn, not a one-time instruction.
- Treat "never-do rules" as harder constraints than tone — tone can flex per persona setting, but rules like "never use the you-didn't-do-X register" or a user's per-topic opt-outs (Pitfall 2) should be enforced independent of how agreeable the current conversation has made the model.
- Distinguish adjustability from agreeableness explicitly in whatever spec covers persona: adjustability is the user deliberately turning a dial; sycophancy is the model turning the dial itself because pushback felt easier than holding a position. Only the former should be able to change persisted state.

**Warning signs:**
- Accountability pressure trends toward "gentle" over time with no explicit setting change, purely from a run of conversations where the user brushed off nudges.
- Never-do rules get violated more often as a single conversation gets longer.
- The bot never holds a position the user visibly disagrees with.

**Phase to address:**
The persona-configuration phase — persisted, explicit persona state should be designed before or alongside variable reinforcement and pressure-dial work, not retrofitted after the model has been observed drifting.

---

### Pitfall 6: Removing buttons pushes all the ambiguity onto intent inference, and the fallback path was never built

**What goes wrong:**
Structured input (buttons) exists specifically to make intent unambiguous to the backend; free text does not carry that guarantee. The most consistent documented failure across chatbot redesigns that drop structured input is optimizing the happy-path parse and treating clarification/fallback handling as a later addition — the system works in every demo and breaks on the first real, oddly-phrased message. For this bot specifically, the check-in flow used to resolve to one of 4 known states via button tap; after removal, "how was your day" can be answered with anything from one word to a rambling tangent, and the tool-calling loop (already capped at 5 rounds, already falling back to a generic error message on any API failure mid-loop per the existing `chat()` behavior) has to correctly infer which of several tools (if any) apply, with no fallback path currently designed for "the model wasn't sure what the user meant."

**Why it happens:**
The decision to remove buttons (already made — "Removed, not merely avoided," per PROJECT.md) is justified on the product's own terms (a closed vocabulary pre-empts the information the product depends on) but that justification doesn't automatically supply the replacement mechanism for the disambiguation buttons used to provide for free.

**How to avoid:**
- Design an explicit clarification path as part of *this* phase, not as a bug fix afterward: when the model is uncertain what a free-text reply implies (e.g. an ambiguous check-in answer that could map to several tools or none), it should ask a short follow-up rather than guess-and-call-a-tool or silently do nothing.
- Because the check-in flow specifically becomes a probing conversation (implementation-intentions: "what exactly and when exactly"), design that probe sequence explicitly as a mini conversation contract, not as a single open question expecting a single structured answer — this is exactly the domain where naive free-text parsing breaks first.
- Test with intentionally unhelpful/oblique replies during whatever debug-mode work happens first (PROJECT.md already calls this out as needed early) — "idk," a one-word answer, an off-topic tangent, and a reply that answers a different question than the one asked should all be exercised before this ships.

**Warning signs:**
- The bot calls a tool based on a misread of an ambiguous message (e.g. marks a habit complete from a sarcastic or hedged reply).
- Off-topic replies get steered back to the check-in question instead of being welcomed (the PROJECT.md explicitly requires off-topic conversation never be steered back — this is a good acceptance test for the clarification design).
- Users start giving terser answers over time — often a sign the free-text interface is perceived as effortful rather than natural.

**Phase to address:**
The check-in redesign / button-removal phase, paired with debug mode so behavior can actually be iterated on before the daily-cadence scheduler makes each miss cost a day.

---

### Pitfall 7: Cross-lingual retrieval silently fails on the Russian/English journal

**What goes wrong:**
The retrieval foundational phase exists specifically because of a logged real failure: a query for "weather" (English) found nothing because the journal entry was in Russian ("жаркий день") with no shared substring. Swapping substring matching for embeddings does not automatically fix this — published cross-lingual RAG research shows retrieval quality drops 30-50% when the query language differs from the stored content's language, even with embedding models specifically marketed as multilingual, and generation quality degrades further when the retrieved context mixes languages with the query. A model that queries "what did I journal about the weather" in English against a mixed Russian/English corpus can retrieve nothing relevant, or retrieve Russian content it then translates loosely/wrongly when it responds.

**Why it happens:**
"Multilingual embedding model" is often read as "solves cross-lingual search," but multilingual just means the model *can* represent many languages in one space — it doesn't mean same-meaning phrases in different languages land equally close together as same-language paraphrases do. This is a known, measured gap, not an edge case.

**How to avoid:**
- Pick an embedding model with demonstrated cross-lingual retrieval performance (e.g. models trained explicitly for cross-lingual alignment such as BAAI/bge-m3-class models), and treat this as a selection criterion, not an assumption — verify with the two languages actually in play (a handful of hand-picked Russian query → English entry and English query → Russian entry test pairs) before committing to a model in the stack decision.
- Because journal entries are short, avoid the generic RAG chunking defaults (e.g. ~200 tokens with 100-token overlap) that assume long source documents — a single journal entry is very likely to be one chunk; chunking logic should key on the entry as the natural unit rather than a fixed token window that could span multiple days' entries together or split one entry awkwardly.
- Since the corpus is small (a personal journal, not a document store), consider retrieving in the original language and doing language-aware reranking or a translation-normalization step (translate query and corpus into one canonical representation, e.g. English, purely for the similarity comparison) rather than relying on raw embedding-space alignment to carry the whole burden.
- Test explicitly with the same failure case that motivated this work (an English query that should surface a Russian-language entry) as a concrete acceptance criterion for the retrieval phase — this bug is already reproduced and documented; use it as the regression test.

**Warning signs:**
- A query in one language returns zero results when relevant entries exist in the other language.
- Retrieved entries in Russian get summarized or quoted in an English response with a subtly wrong translation.

**Phase to address:**
The retrieval/semantic-search foundational phase — embedding model choice and chunking strategy should be validated against real bilingual data before the rest of Milestone A is built on top of it, since PROJECT.md already identifies retrieval as the dependency for nearly every other requirement.

---

### Pitfall 8: Embedding model changes silently corrupt the whole retrieval index

**What goes wrong:**
Different embedding models produce vectors in incompatible coordinate spaces — a vector from model A cannot be meaningfully compared to a vector from model B, even if both claim to represent "similar" text similarly. If the embedding model is ever swapped (a new model version, a provider change, or simply a routing change consistent with this bot's existing multi-provider LLM routing pattern — Groq vs OpenAI), and old vectors are left in the store while new content is embedded with the new model, the index becomes silently mixed: some fraction of stored vectors are meaningless relative to new queries, with no error thrown anywhere — retrieval quality just degrades in a way that looks like "the feature doesn't work well" rather than "the index is half-corrupt."

**Why it happens:**
Embeddings are typically stored once and treated as durable data (much like the rest of this bot's persisted state), but embedding models are not a stable foreign key the way, say, a task's due date is — a provider or model swap invalidates every previously stored vector at once, and nothing about the storage schema naturally prevents mixing old and new vectors in the same searchable index.

**How to avoid:**
- Store the embedding model identifier (name + version) alongside every stored vector, from the very first implementation — this is cheap now and expensive to retrofit later once vectors of unknown provenance exist.
- Given this is a small personal corpus (not a billion-document production index), a full re-embed on model change is cheap and should be the default policy — this project does not have the scale problem that forces partial/lazy re-embedding strategies discussed in the broader literature; use the simplest correct approach (re-embed everything, verify no old-model vectors remain) rather than adopting complexity designed for a scale this project explicitly rejects.
- Retain the original journal/note text as the source of truth (already true here — SQLite already stores the text), never treat the vector as the durable artifact; the vector is a derived, disposable index.

**Warning signs:**
- Retrieval quality degrades after any change to the LLM/embedding provider routing.
- Some entries never surface in any retrieval result regardless of query, while others surface constantly.

**Phase to address:**
The retrieval/semantic-search foundational phase — bake model-identifier tracking into the schema at creation, not as a later migration.

---

### Pitfall 9: Encryption at rest gives a false sense of security for the journal + inferred psychological profile

**What goes wrong:**
This bot already Fernet-encrypts API keys in SQLite with the key stored in an `env` file on the same host. Extending "the same pattern" to the journal or the synthesized psychological profile would be a meaningful downgrade in what "encrypted" actually protects against: if the host is compromised, the same `MASTER_KEY` that legitimately decrypts data for the bot's own runtime is available to whoever compromised the host — encryption-at-rest with a co-located key defends against someone stealing a database file in isolation (a backup left somewhere, a misconfigured share), not against a compromised server, which is the more realistic threat for a bot run under `nohup` with no process isolation, no systemd hardening, and (per the codebase concerns doc) no monitoring of unusual access patterns.
The journal plus a synthesized "model of the user" is qualitatively more sensitive than tasks or trackers — it's the kind of content that would, under frameworks like GDPR, be treated as special-category (health/psychological) data, meaning the bar for "we encrypted it" as an adequate control is higher than it is for a to-do list.

**Why it happens:**
Extending existing patterns (this project's explicit stated preference — "new work should follow the existing patterns") is usually right, but the API-key encryption pattern was designed for a threat model (don't leave plaintext third-party credentials sitting in a database) that doesn't automatically generalize to "protect a personal journal and psychological profile from a compromised host."

**How to avoid:**
- Name the actual threat model explicitly before deciding this is "good enough": is the goal defending against (a) casual file exposure/backup leakage, or (b) a compromised host? The current Fernet-with-co-located-key pattern only addresses (a). If (b) matters for a real personal journal, the mitigations are architecturally different (e.g., a passphrase-derived key never persisted on the host, entered at process start, held only in memory) and should be a deliberate decision, not an inherited default.
- At minimum, treat the journal/profile data with the same "never enters planning artifacts, gitignored" discipline PROJECT.md already applies to `state.json`/`bot_memory.db`, and extend that discipline explicitly to any exported/debug artifacts the new debug-mode tooling produces (a dumped prompt used to iterate on tone will contain real journal content and synthesized profile data — make sure debug output paths get the same exclusion treatment, not just the primary data files).
- Given this is explicitly a single-user, non-scale project, the simplest correct answer may be to accept the current threat model consciously (host compromise is already catastrophic for this bot regardless of journal encryption, since the LLM API keys and Telegram token are equally exposed) rather than build disproportionate cryptographic machinery — but that should be a stated decision, not a default nobody examined.

**Warning signs:**
- New sensitive data (profile synthesis, evidence for observations) gets added to `bot_memory.db` without an explicit check of whether it should follow the same at-rest treatment as existing tables.
- Debug-mode output (prompt inspection) gets logged to a file or console history that isn't covered by the same gitignore/handling discipline as the primary data stores.

**Phase to address:**
Should be an explicit decision recorded in Key Decisions before or during the model-of-user/retrieval phases — not a technical phase on its own, given the project's anti-scale-engineering stance, but a conscious call rather than silence.

---

### Pitfall 10: OAuth retrofit reproduces the shared-secret problem with extra steps

**What goes wrong:**
The MCP server today authenticates with one shared token in a URL query parameter (`?key=<token>`) — a single ambient credential that works for anyone who has it, with no per-user scoping. The single most common mistake when "adding OAuth" to a system like this is not actually eliminating ambient authority — it's replacing one static shared secret with another (a long-lived service JWT, or a single OAuth app credential used the same way for every caller) while believing the problem is solved because the new credential *looks* more sophisticated. A long-lived, non-revocable, unscoped token is the same vulnerability wearing OAuth's clothing.
A second, distinct trap sits specifically at the account-linking step: the dominant real-world OAuth account-takeover bug class is trusting an OAuth/OIDC provider's `email` claim without checking `email_verified` — an attacker who controls an email address the victim doesn't yet own (or exploits a lapsed/reassigned domain) can get a backend to link a Google identity to the wrong internal account, silently merging into or hijacking existing data. For this project, "existing data" means someone else's tasks, journal, and psychological profile.

**Why it happens:**
OAuth is often adopted as "the standard, more secure thing" without unpacking what specifically it replaces the shared-secret problem with: OAuth's actual security properties (short-lived, revocable, audience-scoped tokens; verified identity claims) only apply if they're deliberately implemented — the protocol permits building something just as broken as a shared token if the implementation skips scoping and verification. This is a known enough failure mode that the current MCP authorization spec (2025-06-18 / 2025-11-25) explicitly calls out "ambient authority" MCP servers as the named anti-pattern to avoid, and mandates PKCE, RFC 8707 resource/audience binding, and strict audience validation specifically because MCP servers acting as proxies had been exploited via exactly this class of confused-deputy bug.

**How to avoid:**
- Explicitly reject "one OAuth app credential, one long-lived server-side token used for all requests" as the design — every authenticated caller needs their own token, scoped to their own `chat_id`, and every MCP tool call must check that the authenticated identity matches the data being accessed (per-user data scoping, already an explicit Milestone B requirement — treat it as load-bearing, not aspirational).
- Follow the current MCP spec's authorization requirements directly rather than hand-rolling: OAuth 2.1 + PKCE, RFC 8707 resource indicators binding a token to this specific MCP server (preventing a token issued for this server being replayed elsewhere), and audience validation on every request.
- At the account-linking step, explicitly check Google's `email_verified` claim before trusting the email to link/create a `chat_id` mapping — do not link purely on email string match. Given this bot's existing users are identified by Telegram `chat_id` (not email), the safer design is almost certainly an explicit, one-time linking action initiated by the already-authenticated Telegram user (e.g. "run `/linkgoogle` in Telegram, get a short-lived one-time code, enter it in the Google-authenticated web flow") rather than inferring the mapping from any claim the external identity provider hands over — this avoids the unverified-email class of bug entirely by never trusting external identity as sufficient proof of internal identity on its own.
- Rotate/invalidate the existing shared query-param token as part of this migration, not alongside it indefinitely — a transition period where both the old shared token and new per-user OAuth both grant access is exactly the "reproduces the old problem" trap; ship both changes as one atomic cutover for the remote transport.

**Warning signs:**
- The new auth design still has exactly one long-lived credential that, if leaked, grants access to all users' data (same blast radius as the current query-param token).
- Account linking trusts an external claim (email, name) as sufficient to determine which internal `chat_id` gets access, without an internally-initiated confirmation step.
- The old `?key=` query-param path is left reachable "for compatibility" after the OAuth path ships.

**Phase to address:**
Milestone B is entirely about this; the account-linking design specifically (how a Google identity maps to a `chat_id`) deserves its own explicit design discussion before implementation, given it's the step where the most common real-world bug class lives.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|------------------|
| Let the LLM self-report confidence on a synthesized observation | Fast to implement, no extra logic | Confidence numbers are as unreliable as the claim they describe — false precision | Never for user-facing confidence; fine as an internal secondary signal only |
| Store embeddings without a model-identifier column | Simpler schema initially | Silent index corruption on any future model swap, expensive retrofit once vectors of unknown provenance exist | Never — this is a one-line addition now vs. a full data audit later |
| Reuse the API-key Fernet-with-co-located-key pattern for journal/profile data without reassessing the threat model | Consistent with existing code patterns | False sense of security against the realistic threat (host compromise), for the most sensitive data in the system | Acceptable only if the threat model is explicitly named and the tradeoff consciously accepted, not inherited by default |
| Ship a transition period where the old `?key=` token and new OAuth both work | Smoother migration, no downtime | Recreates exactly the ambient-authority problem OAuth was meant to fix, for the duration of the "transition" | Never — cut over atomically |
| Fixed generic RAG chunk size (e.g. ~200 tokens) applied to short journal entries | Reuse of a common default, less design work | Merges unrelated short entries into one chunk or splits entries oddly, degrading retrieval precision on exactly the content this feature exists for | Never for this corpus — chunk by natural entry boundary instead |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|-----------------|-------------------|
| Embedding/vector search library | Treating "multilingual" model support as solving cross-lingual retrieval | Validate cross-lingual query/result pairs explicitly with real Russian/English examples before committing to a model |
| Google OAuth (Milestone B) | Trusting the `email` claim to link/create the internal account | Check `email_verified`; prefer an internally-initiated linking flow over trusting the external claim alone |
| MCP remote transport auth | Adding OAuth as a second option alongside the existing `?key=` token | Cut over atomically; don't run both simultaneously |
| Any future vector DB / embedding provider | Assuming stored vectors remain valid across a provider or model version change | Tag every vector with model identity; re-embed fully on any change (corpus is small enough this is cheap) |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|-----------------|
| Over-generous `top_k` retrieval on every message | Rising per-call token cost, no quality improvement, occasional confused/bloated responses | Cap retrieval to 3-5 highest-relevance items; budget retrieval cost against the existing 30-calls/hour ceiling explicitly | Breaks the rate-limit budget quickly for keyless Groq users given synthesis jobs are a separate, additional cost on top |
| Periodic "model of the user" synthesis treated as free background work | Unexpected spikes in API usage that eat into the per-user hourly call budget | Design synthesis cadence and cost as a first-class budget line, separate from reactive chat calls | Becomes visible the first time a synthesis run collides with a user's own active conversation and both compete for the hourly cap |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Shared static token in a URL query param (current MCP state) | Full account/data access to whoever obtains the token (logs, browser history, URL sharing) — already flagged in CONCERNS.md | Replace with per-user, audience-scoped, revocable tokens per current MCP OAuth spec; don't just add TLS/log-redaction on top of the existing shared secret |
| Trusting OAuth `email` claim without checking `email_verified` for account linking | Account takeover / cross-user data exposure — the dominant real-world OAuth bug class | Verify `email_verified`; prefer internally-initiated linking over external-claim-based linking |
| Treating encryption-at-rest with a co-located key as sufficient for the journal + psychological profile | False sense of security against the realistic threat (compromised host), for the most sensitive data in the system | Explicitly name the threat model; consider a key not persisted on the host if defending against host compromise is actually the goal |
| No per-user data scoping check on individual MCP tool calls (ambient authority) | One compromised credential exposes every user's data, not just one | Enforce identity-to-data-scope check on every tool invocation, not just at the connection/auth boundary |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-------------------|
| Synthesized observation delivered as an unprompted opener | Feels surveilled rather than known; erodes the trust the whole product depends on | Surface pattern observations only in context, never as a cold open; ask permission before naming a pattern |
| Free-text check-in with no clarification fallback | User's ambiguous or oblique answer gets misread into the wrong tool call, or ignored | Design an explicit "I'm not sure what you mean" follow-up as part of the check-in redesign, not an afterthought |
| Persona pressure drifting gentle from unspoken conversational cues | Accountability bot stops holding any position, defeats its own purpose | Persona parameters change only via explicit, recognized setting-change intent; re-inject full persona every turn |
| Retrieved memory presented without dates or as if it were live conversation | Bot references stale facts as current, or user can't tell what's memory vs. what they just said | Explicitly label and date retrieved context as a separate block in the prompt |

## "Looks Done But Isn't" Checklist

- [ ] **Model-of-user synthesis:** Often missing an evidence-count threshold before surfacing — verify low-n patterns are suppressed, not just generated with a confidence label nobody enforces.
- [ ] **Cross-lingual retrieval:** Often only tested with same-language query/entry pairs — verify with the actual reproduced failure case (English query, Russian entry) as a named regression test.
- [ ] **Persona configuration:** Often implemented as a system-prompt addition tested once at conversation start — verify persona and never-do rules still hold after 50+ turns in one conversation, not just in a fresh session.
- [ ] **OAuth account linking:** Often verified only for the "new user signs up with Google" happy path — verify the case where an email is claimed by an existing internal account and where `email_verified` is false.
- [ ] **Embedding storage:** Often missing a model-identifier column — verify it exists and is populated before the corpus grows large enough that a silent migration becomes painful.
- [ ] **Free-text check-in fallback:** Often only tested with clear, on-topic answers — verify against a one-word reply, a sarcastic reply, and a fully off-topic tangent.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|-----------------|------------------|
| A false pattern was already surfaced to the user and damaged trust | MEDIUM | Explicit retraction mechanism already implied by "visible and editable model of the user" — make correction visibly registered ("noted, removing that") rather than silently edited |
| Embedding index found to be mixed-model / corrupted | LOW (small corpus) | Full re-embed from source text (already retained in SQLite); this project's corpus size makes this cheap, unlike large-scale systems |
| Shared MCP token already leaked before OAuth ships | LOW | Rotate the token immediately (already documented as the mitigation in CONCERNS.md); treat as urgent, not deferred to the OAuth milestone |
| A synthesized profile fact turns out stale/wrong and was acted on (e.g. bot gave advice based on it) | MEDIUM | Requires both a data fix (invalidate/timestamp the fact) and a conversational acknowledgment — silently fixing the data without acknowledging the wrong inference repeats Pitfall 1's trust cost |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|----------------|
| False-pattern assertion (Pitfall 1) | Model-of-user synthesis phase | Evidence-count threshold enforced in code; every surfaced observation traceable to specific dated entries on inspection |
| Creepy delivery (Pitfall 2) | Model-of-user + persona-configuration phases (joint) | Manual review: no synthesized observation is ever the first line of an unprompted message |
| Memory contradictions (Pitfall 3) | Retrieval foundational phase | Test: journal a fact, journal its contradiction weeks later, verify retrieval/response surfaces both with dates rather than the stale one alone |
| Over-retrieval / prompt confusion (Pitfall 4) | Retrieval foundational phase | Retrieved context appears in a labeled, dated block in the assembled prompt (inspectable via debug mode); token cost per call tracked |
| Persona/sycophancy drift (Pitfall 5) | Persona-configuration phase | Long-conversation test (50+ turns) confirms never-do rules and pressure setting hold without explicit re-setting |
| Free-text ambiguity (Pitfall 6) | Check-in redesign phase | Test suite includes oblique/off-topic/one-word replies; off-topic replies confirmed never steered back |
| Cross-lingual retrieval failure (Pitfall 7) | Retrieval foundational phase | The originally-logged weather/«жаркий день» failure case passes as a named regression test |
| Embedding model drift (Pitfall 8) | Retrieval foundational phase | Schema includes model-identifier column from first migration; no vector exists without it |
| Data-at-rest false confidence (Pitfall 9) | Decision recorded before/during model-of-user or retrieval phase | Threat model explicitly stated in Key Decisions, not left implicit |
| OAuth retrofit mistakes (Pitfall 10) | Milestone B, account-linking design step specifically | Per-user scoping enforced on every MCP tool call; `email_verified` checked; old shared-token path removed same day new auth ships |

## Sources

- [Causal Stories from Sensor Traces: Auditing Epistemic Overreach in LLM-Generated Personal Sensing Explanations (arXiv 2605.08590)](https://arxiv.org/pdf/2605.08590)
- [The Dark Side of AI Companionship: A Taxonomy of Harmful Algorithmic Behaviors in Human-AI Relationships (CHI 2025)](https://dl.acm.org/doi/10.1145/3706598.3713429)
- [Tracing Users' Privacy Concerns Across the Lifecycle of a Romantic AI Companion](https://arxiv.org/html/2603.21106v2)
- [Don't Ask the LLM to Track Freshness: A Deterministic Recipe for Memory Conflict Resolution (arXiv 2606.01435)](https://arxiv.org/html/2606.01435v1)
- [STALE: Can LLM Agents Know When Their Memories Are No Longer Valid? (arXiv 2605.06527)](https://arxiv.org/pdf/2605.06527)
- [MemConflict: Evaluating Long-Term Memory Systems Under Memory Conflicts (arXiv 2605.20926)](https://arxiv.org/html/2605.20926)
- [How AI Agents Actually Remember: Inside Mem0, Supermemory, and Letta](https://kenhuangus.substack.com/p/how-ai-agents-actually-remember-inside)
- [Anthropic AI Introduces Persona Vectors to Monitor and Control Personality Shifts in LLMs](https://www.marktechpost.com/2025/08/05/anthropic-ai-introduces-persona-vectors-to-monitor-and-control-personality-shifts-in-llms/)
- [Understanding Persona Drift in LLMs](https://www.emergentmind.com/topics/persona-drift)
- [The hidden functions of sycophancy in AI systems: steering, consistency, and cognitive dependency (AI & Society)](https://link.springer.com/article/10.1007/s00146-026-02993-z)
- [How to Find the Best Multilingual Embedding Model for Your RAG](https://towardsdatascience.com/how-to-find-the-best-multilingual-embedding-model-for-your-rag-40325c308ebb/)
- [The Cross-Lingual Cost: Retrieval Biases in RAG over Arabic-English Corpora (arXiv 2507.07543)](https://arxiv.org/pdf/2507.07543)
- [Migrate to a New Embedding Model — Qdrant docs](https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/)
- [Different Embedding Models, Different Spaces: The Hidden Cost of Model Upgrades](https://medium.com/data-science-collective/different-embedding-models-different-spaces-the-hidden-cost-of-model-upgrades-899db24ad233)
- [Is It Safe to Use an Online Journaling App? Risks and Solutions](https://writediary.com/guide/how-to-keep-your-journal-private-physical-and-digital-security/is-it-safe-to-use-an-online-journaling-app-risks-and-solutions/)
- [BUG: Google OAuth login does not check email_verified (account takeover vector)](https://github.com/ronisarkarexe/story-spark-ai/issues/1527)
- [nOAuth: How Microsoft OAuth Misconfiguration Can Lead to Full Account Takeover](https://www.descope.com/blog/post/noauth)
- [The confused deputy problem — AWS IAM documentation](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)
- [Authorization — Model Context Protocol specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [Understanding Model Context Protocol Security (MCP) in 2026 — Wiz](https://www.wiz.io/academy/ai-security/model-context-protocol-security)
- [How to Fix Your Chatbot UI and UX (and Why It Costs You to Wait)](https://clutch.co/resources/fix-your-chatbot-ux)
- [Understanding the YAGNI Principle: A Key to Efficient Software Development](https://poisonedyouth.github.io/YAGNI_principle)
- Project-internal sources: `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md` (existing shared-token MCP auth, non-atomic dual-write state, Fernet key co-location, retrieval-is-substring-matching finding)

---
*Pitfalls research for: AI companion/accountability bot with long-term memory (Milestone A) and MCP OAuth retrofit (Milestone B)*
*Researched: 2026-08-01*
