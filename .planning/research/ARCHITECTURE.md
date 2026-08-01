# Architecture Research

**Domain:** Adding memory/retrieval/persona/debug capabilities to an existing single-file Telegram bot
**Researched:** 2026-08-01
**Confidence:** HIGH for codebase-grounded design (verified against `.planning/codebase/ARCHITECTURE.md`, `STRUCTURE.md`, `CONCERNS.md`); MEDIUM for external library claims (sqlite-vec, `Job.run()`), each flagged inline.

## Standard Architecture

### System Overview

This is not a greenfield architecture — it is four additive components wired into the existing single-process bot at well-defined seams. The design goal is deliberately **not** "what would this look like built fresh" but "how do four new capabilities attach to a 4,240-line monolith without forcing every plan to edit the same lines."

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      Telegram Bot process (bot.py, unchanged shape)      │
│                                                                          │
│  TOOLS = BASE_TOOLS + retrieval.TOOLS_EXTRA + persona.TOOLS_EXTRA + …   │
│  _execute_tool(): existing if/elif chain, one new elif per new tool     │
│  build_system_prompt(): refactored into promptkit sections (see below) │
│  main(): existing handler registration + a handful of new commands     │
│  restore_all_jobs(): existing + one new schedule_observation_job() call │
└───────────────┬───────────────┬───────────────┬───────────────┬────────┘
                │               │               │               │
   ┌────────────▼───┐ ┌─────────▼──────┐ ┌──────▼───────┐ ┌─────▼────────┐
   │ debug_tools.py │ │  retrieval.py  │ │  persona.py  │ │observations.py│
   │ new file       │ │  new file      │ │  new file    │ │  new file     │
   │ - fire job now │ │ - index_upsert │ │ - schema     │ │ - schema      │
   │ - simulate now │ │ - index_delete │ │ - render()   │ │ - synth job   │
   │ - dump prompt  │ │ - search()     │ │ - tool CRUD  │ │ - tool CRUD   │
   └────────────────┘ └───────┬────────┘ └──────┬───────┘ └───────┬───────┘
                               │                 │                 │
                               ▼                 ▼                 ▼
                    ┌────────────────────────────────────────────────────┐
                    │              promptkit.py (new file)                │
                    │  Ordered, budgeted section registry replacing the   │
                    │  string-concatenation body of build_system_prompt() │
                    └────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌────────────────────────────────────────────────────┐
                    │        bot_memory.db (SQLite) — existing file       │
                    │  + memory_index / memory_vectors (retrieval)        │
                    │  + observations (new table)                         │
                    │  state.json unchanged in shape; persona is a new    │
                    │  key on the existing per-user dict                  │
                    └────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|-------------------------|
| `promptkit.py` | Own the *order, precedence and token budget* of every system-prompt section; replace ad hoc string concatenation in `build_system_prompt()` | Ordered list of `(priority, name, render_fn, max_chars)`; hard-guaranteed slot for persona, soft-truncated slots for everything variable-length |
| `retrieval.py` | Index and semantically search journal/notes/tasks/reminders; the substrate for "find by meaning" | New SQLite table(s) in `bot_memory.db`, embeddings via the bot's own OpenAI key, exposed only as a tool (`search`), never pre-injected |
| `persona.py` | Store and render per-user tone/frequency/pressure/never-do rules; shape *every* generated message | New `persona` dict on the existing per-user state object (state.json) + a Tier-0 `promptkit` section that cannot be truncated |
| `observations.py` | Periodic synthesis of durable, evidenced, confidence-scored statements about the user; visible/editable/invalidatable | New `observations` table in `bot_memory.db`; a new APScheduler job following the existing `schedule_user_*` pattern; a Tier-3 `promptkit` section |
| `debug_tools.py` | Fire any scheduled job on demand, simulate a different "now," dump the assembled prompt without sending it | Wraps `telegram.ext.Job.run()` (MEDIUM confidence — python-telegram-bot docs) and existing `get_jobs_by_name()`; admin-gated commands |

## Recommended Project Structure

```
secretary-bot/
├── bot.py                 # UNCHANGED shape: imports the modules below,
│                           # touches only append-only seams (TOOLS concat,
│                           # _execute_tool fallthrough, handler registration,
│                           # restore_all_jobs)
├── promptkit.py            # Section registry + budget/precedence logic
├── retrieval.py             # Index write/delete hooks + search()
├── persona.py               # Persona schema, render, tool handlers
├── observations.py          # Observation schema, synthesis job, tool handlers
├── debug_tools.py           # Job introspection, now-override, prompt dump
├── mcp_server.py            # unchanged for this milestone
└── tests/
    └── test_bot.py          # existing suite + one test module per new file,
                              # following the existing "stub Telegram, redirect
                              # STATE_FILE/DB_FILE" pattern
```

### Structure Rationale

- **One new file per capability, not one shared "extensions.py":** each file owns a disjoint concern (index, persona, observations, debug) and a disjoint set of new lines. This is the direct answer to the parallel-edit-collision requirement below — new logic lives in new files that different plans can create/edit independently, and `bot.py` is touched only at small, append-only points.
- **`promptkit.py` is separate from `persona.py`/`observations.py`** even though it's small, because it is the one piece every other module depends on for injection order and truncation guarantees. It must exist and be stable before persona/observations wire into it (see Sequencing).
- **No new top-level package/`src/` layout.** The project is a single flat directory by convention (`STRUCTURE.md`); introducing a package hierarchy here would be the "parallel architecture" the constraints explicitly warn against. New files sit next to `bot.py` and `mcp_server.py`, imported with plain `import retrieval` etc.

## Architectural Patterns

### Pattern 1: Sectioned, budgeted prompt assembly (replaces string concatenation)

**What:** `build_system_prompt()` currently concatenates strings for context, trackers, habits, deadlines, and memory in a fixed, ad hoc order (`bot.py:1647-1750`). Replace the *body* of that function (not its signature or call sites) with a small registry: each contributor registers `(priority, name, render_fn(user) -> str, max_chars)`. The builder renders in priority order, subtracting from a running character budget, and *never* truncates priority-0 sections.

**When to use:** Any time a prompt is assembled from more than 2-3 independent sources with different importance and different worst-case sizes — exactly the situation created by adding persona + observations + why-anchors on top of the four sources already there.

**Trade-offs:** Slightly more indirection than string concatenation; pays for itself the moment two people want to add sections independently (this project's stated constraint — plans run in parallel).

**Example (illustrative, not literal code to paste):**
```python
# promptkit.py
_SECTIONS = []  # (priority: int, name: str, render_fn, max_chars: int|None)

def register(priority, name, render_fn, max_chars=None):
    _SECTIONS.append((priority, name, render_fn, max_chars))

def build(user, budget_chars=6000):
    remaining = budget_chars
    parts = []
    for priority, name, render_fn, max_chars in sorted(_SECTIONS, key=lambda s: s[0]):
        text = render_fn(user)
        if not text:
            continue
        if priority > 0 and max_chars and len(text) > max_chars:
            text = text[:max_chars] + " …(truncated)"
        if priority > 0 and len(text) > remaining:
            continue  # drop, never truncate priority-0
        parts.append(text)
        remaining -= len(text)
    return "\n\n".join(parts)
```
Persona registers at priority 0 (never dropped/truncated); time/identity at priority 1; task/habit/tracker state (today's existing sections, moved as-is) at priority 2; observations and why-anchors at priority 3 with a `max_chars` cap (e.g. top 3-5 observations, one line each).

### Pattern 2: Retrieval as a tool, not pre-injected context

**What:** Journal and notes are unbounded corpora. Only cheap, almost-always-relevant summaries (top observations, a why-anchor tied to a task the user just mentioned) belong in the pre-injected prompt. Anything requiring "search the whole history for X" belongs behind a tool call the model invokes when it decides it needs it.

**When to use:** Whenever the candidate context source grows without bound over the life of the user (journal, notes, full conversation history) as opposed to sources with a small, bounded current state (today's tasks, active habits, current persona).

**Trade-offs:** An extra LLM round-trip when the model does call the tool (already budgeted for — `chat()` already runs up to 5 tool-call rounds); in exchange, avoids paying retrieval cost on every single turn and avoids duplicating information that observations already summarize.

**Concrete application:** The existing `search` tool (currently `... WHERE lower(entry) LIKE '%q%'`, per PROJECT.md's own retrieval finding) is the natural target to upgrade in place — same tool name, same call sites, new implementation backed by `retrieval.search()`. This avoids TOOLS-list churn entirely for the highest-value fix.

### Pattern 3: Index-alongside-persist, not diff-based reconciliation

**What:** Rather than periodically diffing `state.json`/`bot_memory.db` against the retrieval index, call `retrieval.index_upsert(...)` / `retrieval.index_delete(...)` at the exact point each source record is created, edited, or removed — the same call sites that already call `save_state()` or a `db_add_*`/`db_remove_*` helper.

**When to use:** Any time you're adding a derived index over data that already has well-defined, already-instrumented write paths (which this codebase has for every tool: `add_task`, `complete_task`, `remove_task`, `add_reminder`, `remove_reminder`, `add_journal_entry`, `add_note`, `remove_note`).

**Trade-offs:** Requires touching each of those ~8 call sites once (small, mechanical, low-collision diffs) instead of one central diffing job; in exchange, the index is never stale for anything written through `bot.py`, and there is no need to reconstruct "what changed" from two full-state snapshots.

**Precedent already in the codebase:** this is the same shape as the existing timezone dual-write (`set_timezone` writes both `state.json` and `user_prefs` in SQLite, documented in CLAUDE.md) — "write the primary record, then write the derived record, in the same handler" is already an established pattern here, not a new one.

## Data Flow

### Retrieval index: write path

```
Tool handler (add_journal_entry / add_note / add_task / add_reminder / …)
    │
    ├─► existing write: db_add_journal_entry() / save_state() / etc.  (unchanged)
    │
    └─► retrieval.index_upsert(chat_id, kind, source_id, text)
            │
            ├─► embed(text) via bot's OpenAI key (small, cheap call)
            └─► INSERT/UPDATE into memory_vectors (SQLite, bot_memory.db)
```

### Retrieval index: read path (tool call, not pre-injection)

```
User message → chat() → LLM decides it needs history → calls `search` tool
    │
    └─► _execute_tool("search", {...}) → retrieval.search(chat_id, query, top_k)
            │
            ├─► embed(query)
            ├─► KNN over memory_vectors filtered by chat_id
            └─► return [{kind, source_id, snippet, date, score}, …] to the LLM
```

### Observation synthesis: write path (scheduled, not request-driven)

```
APScheduler job (new, alongside schedule_user_checkins() etc.)
    │
    ├─► pull recent evidence: retrieval.search() + direct reads of journal/
    │   notes/habit/tracker tables over a lookback window
    ├─► one LLM call: "propose candidate observations with evidence pointers"
    ├─► score confidence from evidence count/spread (not the LLM's self-report)
    ├─► compare against existing active observations:
    │     - contradicted → mark old status=invalidated, link superseded_by
    │     - reinforced → bump confidence / update last_confirmed_at
    │     - new → INSERT with status=active
    └─► observations table (bot_memory.db)
```

### Observation read path (prompt injection, budgeted)

```
promptkit.build(user)
    │
    └─► observations.render_section(user)
            │
            ├─► SELECT active, non-hidden observations ORDER BY confidence*recency
            ├─► take top 3-5, one line each
            └─► returned string, subject to priority-3 truncation budget
```

### Debug/dry-run path

```
/debug fire <job_name> (admin only)
    │
    └─► app.job_queue.get_jobs_by_name(job_name)[0].run(application)
            │  (executes the exact scheduled closure immediately — MEDIUM
            │   confidence: telegram.ext.Job.run() per python-telegram-bot docs)
            └─► real side effects: sends message, sets pending_checkin, db_log_job()

/debug asof <chat_id> <ISO datetime>  → sets an in-memory override consulted by
    a single `_now(tz)` helper that every job closure calls instead of
    `datetime.now(tz)` directly (one mechanical refactor, done once, early)

/debug prompt <chat_id> ["simulated message"]
    │
    └─► calls promptkit.build(get_user(chat_id)) directly, replies with the
        exact string, never calls the LLM (true dry run)
```

## Answers to the Four Framing Questions

### 1. Composing a crowded system prompt: budget + precedence, and when retrieval is a tool

Use the sectioned/budgeted registry in Pattern 1, with this precedence, high to low:

1. **Persona directives (tone, pressure, never-do rules)** — priority 0, never truncated. These are behavior constraints, not information; losing them under truncation silently breaks the product's core promise ("shapes every generated message").
2. **Identity/time context** (today's date, timezone, pending check-in) — priority 1, tiny, always kept.
3. **Situational state** (task deadlines, habit streaks, stale trackers) — today's existing sections, moved into the registry unchanged — priority 2.
4. **Observations and why-anchors** — priority 3, capped to a handful of short lines, dropped first if the budget is tight.
5. **Full journal/notes/history retrieval** — never pre-injected; exposed only through the `search` tool, called at the model's discretion. Unbounded corpora do not belong in every turn's prompt regardless of budget headroom — the cost and staleness-of-relevance problem gets worse as the journal grows, not better.

Rule of thumb for the tool-vs-injection boundary: pre-inject only sources whose *current* size is bounded by construction (today's tasks, active habits, current persona, top-N observations); put anything whose size grows with the user's history behind a tool call.

### 2. Where the retrieval index lives, and sync

**In `bot_memory.db` (SQLite), as new tables, not a separate index file or a new datastore.** This matches the existing convention that durable, queryable, cross-cutting data lives in SQLite while `state.json` stays the fast/simple mutable-object store, and it keeps a single backup/restore surface. Concretely: a `memory_vectors` virtual table (MEDIUM confidence recommendation: the `sqlite-vec` extension, `vec0` virtual tables, paired with a normal metadata table holding `chat_id, kind, source_id, text, updated_at` since vector virtual tables have limited column support) plus embeddings computed via the bot's own OpenAI key — note the bot's `OPENAI_API_KEY` is required and present regardless of which model a given user chats with, so embedding cost and availability is decoupled from the Groq/OpenAI *chat* routing split.

Sync is index-alongside-persist (Pattern 3), not periodic reconciliation: hook `retrieval.index_upsert`/`index_delete` into the ~8 existing tool handlers that create/edit/remove journal/notes/tasks/reminders — the same call sites that already call `save_state()` or a `db_add_*`/`db_remove_*` helper. Add a slow, optional nightly `reindex_all()` purely as drift insurance, not as the primary sync mechanism.

**Known, accepted gap:** `mcp_server.py` writes tasks/notes directly to `state.json`/`bot_memory.db` without going through `bot.py`'s tool handlers (already flagged in `CONCERNS.md` as a non-atomic-write risk). Anything written via MCP will not hit the index hooks and will only be picked up by the nightly reindex. This is out of scope to fix in this milestone (MCP identity/auth is Milestone B) — document it, do not attempt to solve it here.

### 3. Modelling observations: evidence, confidence, editability, invalidation

New `observations` table, one row per observation, never hard-deleted (mirrors the existing `reminder_log`/archived-tasks convention of append-and-mark-status rather than delete):

| Column | Purpose |
|---|---|
| `id`, `chat_id` | identity |
| `statement` | the observation text shown to the user |
| `evidence` | JSON array of `{kind, source_id, snippet, date}` — pointers into journal/notes/trackers, not copies, so evidence stays linked to its source of truth |
| `confidence` | derived from evidence count/consistency/spread over time, **not** the LLM's self-reported confidence (self-reported confidence from LLMs is not a reliable signal; count-and-consistency is checkable and cheap) |
| `status` | `active \| user_edited \| user_deleted \| invalidated` |
| `superseded_by` | nullable self-FK, set when a contradicted observation is replaced rather than silently overwritten |
| `created_at`, `updated_at`, `last_confirmed_at` | lifecycle timestamps |

- **Visible/editable:** expose via the same tool-triad shape already used for notes (`get_observations`/`edit_observation`/`remove_observation`) plus a `/observations` command mirroring `/notes`. A user edit sets `status=user_edited`, which freezes the row from further automatic invalidation — user correction outranks synthesis.
- **Invalidation:** each synthesis run re-evaluates existing `active` rows against new evidence; a threshold of consistent counter-examples flips `status=invalidated` and links a fresh row via `superseded_by`, rather than deleting — preserves a debuggable history of how the model of the user changed over time, which is valuable given the product's own premise that it must "know you and prove it."

### 4. Debug/dry-run mode shape

Three orthogonal capabilities, all admin-gated the same way `/broadcast`/`/adminstats` already are:

- **Fire a job now:** `telegram.ext.Job.run(application)` executes the job callback immediately with the same side effects and persistence hooks as the real scheduled fire (MEDIUM confidence — python-telegram-bot docs). Resolve the target via the existing `get_jobs_by_name()` convention (job names already follow `{type}_{chat_id}_{optional_id}`), so no new job registry is needed.
- **Simulate a different "now":** requires one small, one-time refactor — replace scattered `datetime.now(tz)` calls inside job closures with a single `_now(tz)` helper that checks a per-`chat_id` in-memory override before falling back to real time. This must land *before* other work touches those same job-closure lines, or it becomes a recurring collision point.
- **Dump the assembled prompt without sending it:** once `promptkit.build(user)` exists as a pure function, a debug command can call it directly and reply with the literal string — no LLM call, true dry run. This capability is *blocked on* the promptkit refactor (question/pattern 1) existing first.

## Scaling Considerations

Not meaningful in the conventional sense — PROJECT.md is explicit that this serves one user today and is not being engineered for scale. The only scale-shaped design decision here is **cost shape**, called out as a constraint: periodic per-user synthesis (observations) is a new, recurring LLM-call cost distinct from the existing request-driven `chat()` cost. Mitigate by running synthesis on a coarse cadence (e.g. daily or every few days, not per-message) and keeping the lookback window bounded, the same way tracker logs are already capped at 5,000 entries and history at 20 messages.

| Scale | Approach |
|-------|----------|
| 1 user (today) | Everything above, as designed — SQLite is more than sufficient for one user's vectors and observations |
| A handful of users (explicitly possible per PROJECT.md, not a target) | No architectural change needed; per-`chat_id` filtering already present throughout; synthesis job cost scales linearly and stays cheap at this size |
| Larger scale | Explicitly out of scope; not a design input |

## Anti-Patterns

### Anti-Pattern 1: Growing `build_system_prompt()`'s string concatenation further

**What people do:** Keep appending `if condition: prompt += "..."` blocks for persona, observations, and why-anchors directly into the existing function body, the same way trackers/habits/memory were added historically.

**Why it's wrong:** No precedence, no budget, no truncation guarantee — the exact "unmanageable prompt and context bloat" risk the milestone context calls out. It also guarantees every future feature that touches the prompt collides on the same function body in parallel work.

**Do this instead:** The sectioned/budgeted registry (Pattern 1) — do this refactor once, early, as its own isolated phase.

### Anti-Pattern 2: Pre-injecting full retrieval results into every prompt "to be safe"

**What people do:** Since retrieval is the foundational fix, it's tempting to always inject the top-K search results for the user's last message into every system prompt.

**Why it's wrong:** Cost scales with conversation length regardless of relevance; duplicates information observations should already be summarizing; and the corpus is unbounded so "top-K of everything" degrades in precision as history grows. It also defeats the point of giving the model a `search` tool it can invoke only when it judges it necessary.

**Do this instead:** Keep retrieval strictly tool-gated (Pattern 2); let observations carry the small amount of "always relevant" distilled knowledge instead.

### Anti-Pattern 3: Reconciling the retrieval index via periodic full-state diffing

**What people do:** Write a job that walks all of `state.json`/`bot_memory.db` on a schedule and re-syncs the index from scratch, avoiding the need to touch existing write call sites.

**Why it's wrong:** Expensive as data grows, introduces a lag window during which retrieval is silently wrong, and duplicates logic that the existing write call sites already have (they know exactly what changed).

**Do this instead:** Index-alongside-persist (Pattern 3), with a nightly full reconcile only as drift insurance, not as the primary mechanism.

### Anti-Pattern 4: Letting multiple in-flight plans edit `_execute_tool()`'s if/elif chain or `TOOLS` list concurrently

**What people do:** Each new tool (search upgrade, persona CRUD, observation CRUD) adds its `elif` branch and its TOOLS-list entry directly inline into the existing 450-line function and giant list, the way every tool has been added so far.

**Why it's wrong:** With multiple phases planned to run in parallel against the same single file, several plans editing near the same lines in `TOOLS` and `_execute_tool()` produces avoidable merge conflicts even when the changes are semantically independent.

**Do this instead:** Each new module (`retrieval.py`, `persona.py`, `observations.py`) exports its own `TOOLS_EXTRA` list and its own dispatch function; `bot.py` does `TOOLS = BASE_TOOLS + retrieval.TOOLS_EXTRA + persona.TOOLS_EXTRA + observations.TOOLS_EXTRA` and tries each module's dispatcher in `_execute_tool()` before falling through to the legacy chain. This turns N potential collisions into N independent files plus one shared, trivially-mergeable concatenation line.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| OpenAI embeddings API | Call at index-write time only (not per chat turn) via the bot's own `OPENAI_API_KEY`, which is already a required env var independent of user's chat model | Cheap, bounded call volume (writes, not reads); avoids adding a heavy local ML dependency (e.g. `sentence-transformers`/`torch`) to a bot that today only depends on thin API clients |
| `sqlite-vec` extension (MEDIUM confidence) | Loaded into the existing `bot_memory.db` connection via `conn.load_extension()`; vectors in a `vec0` virtual table, metadata in a normal table joined by rowid | Zero new infrastructure — stays inside the existing single SQLite file, consistent with "hybrid storage across state.json and SQLite" constraint; if unavailable in the deploy environment, FTS5 (built into stdlib `sqlite3`) is a lexical-only fallback, but note it will **not** fix the confirmed cross-lingual/paraphrase failure that motivated this work (FTS5 is still substring/token based, not meaning-based) |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|----------------|-------|
| `bot.py` ↔ `retrieval.py` | Direct function calls (`index_upsert`, `index_delete`, `search`) inserted at existing tool-handler call sites | No new IPC; same process, same import style as the rest of the codebase |
| `bot.py` ↔ `persona.py` / `observations.py` | Direct function calls; new state key (`persona`) forward-filled by `_new_user()` the same way every other schema field is; new SQLite table for observations | Follows the existing "add key to `_new_user()`, `get_user()` forward-fills it" convention documented in `STRUCTURE.md` |
| `promptkit.py` ↔ everything else | A `register(priority, name, render_fn, max_chars)` call made once at import time by each module; `promptkit.build(user)` called once by `build_system_prompt()` | This is the seam that lets persona/observations be added or removed without touching each other's code |
| `debug_tools.py` ↔ APScheduler/JobQueue | Wraps `app.job_queue.get_jobs_by_name()` and `Job.run()`; reads the same job-name convention already used by `restore_all_jobs()` | No new job bookkeeping; debug mode is a read/trigger layer over the existing scheduler, not a parallel one |
| `mcp_server.py` ↔ retrieval index | **None, intentionally, this milestone.** MCP writes bypass the index hooks entirely | Documented gap, mitigated only by the optional nightly `reindex_all()`; real fix (routing MCP writes through the same handlers, or an IPC layer) is out of scope until Milestone B's identity model exists |

## Sequencing (Build Order and Dependencies)

1. **Debug/dry-run infrastructure** (`debug_tools.py` + the `_now(tz)` seam) — first, and for a reason stated directly in the project's own context: scheduler-driven behavior can't be iterated at one attempt per day. It depends on nothing new. Building it first also means every subsequent phase gets to verify its own work via `/debug fire` and (once available) `/debug prompt` instead of waiting on the real clock or real journal accumulation.
2. **Retrieval index** (`retrieval.py`, the ~8 write-site hooks, the `search` tool upgrade) — second, because PROJECT.md itself identifies retrieval as the foundation nearly every other Milestone A requirement reduces to. Depends only on step 1 for easy verification, not for correctness.
3. **Prompt assembly refactor** (`promptkit.py`, `build_system_prompt()`'s body rewritten to use it, existing sections ported over with no behavior change) — third. This must be a solo, non-parallel phase: it is the one place persona and observations both need to attach, so it should land and merge completely before either of those phases starts, or they will collide inside the same function body. Verify with `/debug prompt` from step 1.
4. **Persona configuration** and **observations/synthesis** — fourth, and can proceed **in parallel with each other** once step 3 has merged, because by then each only needs to (a) add its own state/table, (b) add its own tool file, and (c) call `promptkit.register(...)` once — disjoint files, one-line registration each. Observations additionally depends on step 2 (it reads the same corpus and should reuse retrieval's evidence pointers) and is the only one of the four capabilities carrying a new recurring LLM-call cost, so it should not be rushed ahead of the cheaper foundational pieces.

### Collision Summary for Roadmap Phasing

- **Safe to parallelize:** persona and observations, once the prompt refactor (step 3) is merged — they touch disjoint new files and add one line each to `promptkit`'s registration and to `TOOLS`/`_execute_tool`'s fallthrough.
- **Must be solo, blocking phases:** the `promptkit` refactor (step 3) — every later prompt-touching phase depends on it being finished and stable first; and the `_now(tz)` mechanical refactor inside step 1 — it touches every job closure once and should not be revisited concurrently by another phase.
- **Low-risk append points, fine to interleave loosely:** `TOOLS` list concatenation, `_execute_tool()` fallthrough, `main()` handler registration, `restore_all_jobs()` — all append-only; conflicts here are small, textual, and easy to resolve with a rebase rather than semantic collisions, provided each new capability's `TOOLS_EXTRA`/dispatcher lives in its own file per Anti-Pattern 4.
- **Not to be conflated with an MCP fix:** none of this milestone's work makes `mcp_server.py`'s parallel-write staleness (flagged in `CONCERNS.md`) any better or worse; the retrieval index simply inherits that existing gap.

## Sources

- Existing codebase analysis (authoritative, HIGH confidence): `/home/ec2-user/secretary-bot/.planning/codebase/ARCHITECTURE.md`, `STRUCTURE.md`, `CONCERNS.md`, and `/home/ec2-user/secretary-bot/.planning/PROJECT.md`
- [SQLite as a Vector Database for Similarity Search](https://www.sqliteforum.com/p/sqlite-as-a-vector-database) — MEDIUM confidence, general web
- [How sqlite-vec Works for Storing and Querying Vector Embeddings](https://medium.com/@stephenc211/how-sqlite-vec-works-for-storing-and-querying-vector-embeddings-165adeeeceea) — MEDIUM confidence, general web
- [Implementing vector search in SQLite](https://tpoe.dev/blog/vector-search-sqlite) — MEDIUM confidence, general web
- [JobQueue - python-telegram-bot v22.6 docs](https://docs.python-telegram-bot.org/telegram.ext.jobqueue.html) — MEDIUM confidence (official docs, not verified against installed version in this repo)
- [Job - python-telegram-bot v21.8 docs](https://docs.python-telegram-bot.org/en/v21.8/telegram.ext.job.html) — MEDIUM confidence

---
*Architecture research for: secretary-bot Milestone A (memory, retrieval, persona, debug mode)*
*Researched: 2026-08-01*
