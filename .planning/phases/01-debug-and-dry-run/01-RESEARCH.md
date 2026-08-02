# Phase 1: Debug and Dry-Run - Research

**Researched:** 2026-08-02
**Domain:** Owner-gated debug tooling for a single-file Telegram bot — on-demand job firing, ambient time-travel, and system-prompt introspection
**Confidence:** HIGH

## Summary

This phase adds no new external dependency, no new library, and no new architectural layer — it is a set of read/patch changes to `bot.py`'s existing job-scheduling, time, and command-handling code. The three requirements decompose cleanly along code that already exists:

- **DEBUG-01** (fire any job on demand) reuses the job closures already defined inside `schedule_user_checkins`, `schedule_user_alerts`, and `schedule_user_reminder` (`bot.py:1857-2153`) — these are plain `async def` closures over `context` and a captured `chat_id`; nothing structural stops a debug command from calling them directly with a synthetic `context.bot`, bypassing `job_queue.run_daily`'s wait.
- **DEBUG-02** (simulated "now") requires a single `_now(tz)` helper and a **verified, enumerated** set of call sites — 26 call sites use `datetime.now(`, `date.today()`, or `datetime.utcnow()` across `bot.py`, and the CONTEXT.md-locked ambient behavior (ordinary commands like `/tasks` must respond to the override) means far more than "every job closure" needs to switch to the new helper. Full site-by-site list is below.
- **DEBUG-03** (prompt dump) is nearly free: `build_system_prompt(user, chat_id)` (`bot.py:1647`) is already a pure function of `user` — no Telegram or network dependency — so a debug command just calls it and returns the string. The only new problem is delivery: the string can exceed Telegram's 4096-char hard limit, and the codebase currently has **no** message-splitting logic anywhere (confirmed — no `4096`, `send_document`, or chunking helper exists outside the one `reply_document` call in `/export`).

**Primary recommendation:** Build all three debug capabilities as subcommands of a single `/debug` command (`fire|clock|prompt`), owner-gated with the exact existing `if str(update.effective_chat.id) != MY_CHAT_ID` check reused verbatim from `/adminstats` and `/broadcast`, introduce one `_now(tz, chat_id=None)` helper that consults a new per-chat debug-clock field, and replace **every** site below (not just job closures) that computes "today"/"now" for a specific user with a call to that helper. Deliver the prompt dump via `update.message.reply_document` using an in-memory `io.BytesIO` (the exact pattern `/export` already uses at `bot.py:3577-3583`) — this never touches disk, so it trivially satisfies the "never written anywhere tracked by git" constraint without needing tempfile-cleanup logic.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Job-fire on demand (DEBUG-01) | Backend / Bot process (`bot.py` command handler) | Job Scheduler (APScheduler closures) | Debug command directly invokes the same closures the scheduler would call — no new tier, just an alternate entry point into existing async functions |
| Simulated "now" override (DEBUG-02) | State Manager (`state.json` or SQLite `user_prefs`) | Backend (every read-path function that computes "now" for a user) | The override is stored data consulted by many call sites; it is not itself a service — matches how `timezone` already works (`get_user()` overlay pattern) |
| System prompt dump (DEBUG-03) | Backend (`build_system_prompt`) | Telegram delivery layer (message vs. document) | No new tier — reuses an already-pure function; only the delivery mechanism (text vs. file) is new |
| Owner gate | Backend / Command Handler | — | Existing in-process check against `MY_CHAT_ID` env var; no auth service, no database-backed ACL |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEBUG-01 | User can trigger any scheduled job on demand, without waiting for its scheduled time | Job closures identified and their exact call signatures documented below; reminder-targeting convention (1-based index) confirmed from `/remind list` and `get_reminders` tool |
| DEBUG-02 | User can simulate a different "now" to test time-dependent behavior (deadlines, quiet hours, annual reminders) | Full enumerated list of 26 `datetime.now(`/`date.today()`/`datetime.utcnow()` call sites below, with per-site verdict on whether it must consult the override; persistence-pattern comparison (state.json vs. SQLite `user_prefs`) grounded in the existing `timezone` dual-write precedent |
| DEBUG-03 | User can inspect the exact assembled system prompt for a given context, without sending a real message | `build_system_prompt` signature confirmed pure; Telegram 4096-char limit confirmed via installed package; in-memory `BytesIO` delivery pattern confirmed already in use for `/export` |
</phase_requirements>

## Standard Stack

No new packages are needed for this phase. It is implemented entirely with the already-installed stack.

### Core (already installed — no new install)
| Library | Version (installed) | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `python-telegram-bot` | 22.8 `[VERIFIED: pip show python-telegram-bot in project venv]` | `CommandHandler`, `reply_document`, `job_queue` | Already the bot's framework; `requirements.txt` pins `>=21.6` |
| Python stdlib `datetime`/`zoneinfo` | 3.12 stdlib | `_now(tz)` helper, override comparison | Already how every existing time computation in `bot.py` works |
| Python stdlib `io.BytesIO` | 3.12 stdlib | In-memory prompt-dump file delivery | Already used identically in `/export` (`bot.py:3577`) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Single `/debug` command with subcommands | Separate `/debugfire`, `/debugclock`, `/debugprompt` top-level commands | Rejected — codebase's own convention for multi-action commands (`/remind add\|once\|annual\|list\|remove`, `/habit add\|done\|list\|remove`) is a single command dispatching on `context.args[0]`; a `/debug` family should match |
| In-memory `BytesIO` document for long prompt dump | Split into multiple `reply_text` messages under 4096 chars each | `BytesIO` chosen as primary because prompt dumps are frequently >4096 chars (with journal/notes/profile sections) and a file is easier to read/diff than 3-4 chopped messages; splitting is a reasonable fallback for short prompts under the limit |
| Per-chat debug-clock stored in SQLite `user_prefs` | Store in `state.json` on the user dict | See "Restart/expiry safety" analysis below — SQLite `user_prefs` recommended, matching the `timezone` precedent |

**Installation:** None required — no `requirements.txt` change for this phase.

## Package Legitimacy Audit

Not applicable — this phase installs no external packages.

## Architecture Patterns

### System Architecture Diagram

```
Owner (MY_CHAT_ID) sends /debug fire checkin_morning
        │
        ▼
CommandHandler("debug", debug_cmd)          [bot.py: new, registered after admin_stats pattern]
        │
        ├─ owner gate: str(chat_id) != MY_CHAT_ID → reject      (reused from bot.py:2933 / 3600)
        │
        ▼
dispatch on context.args[0]  ("fire" | "clock" | "prompt")      (pattern reused from remind_cmd / habit_cmd)
        │
   ┌────┼─────────────────────┬─────────────────────────┐
   ▼                          ▼                          ▼
fire <job_name> [reminder#]  clock <ISO ts>|reset        prompt
   │                          │                          │
   ▼                          ▼                          ▼
look up job closure by name  db_set_pref(chat_id,         build_system_prompt(user, chat_id)
(checkin_morning/evening,    "debug_clock", iso_ts)             │
deadline_alert, habit_       or db delete pref for reset        ▼
reminder, idle_nudge,              │                      len(prompt) > 4096?
weekly_digest, or a numbered       ▼                        │        │
reminder id)                 subsequent _now(tz, chat_id)   No       Yes
   │                         calls for this chat_id      reply_text  reply_document
   ▼                         return the override value       │      (io.BytesIO, no
invoke the async closure                                     ▼      disk write)
directly with a stub          Ambient effect: /tasks,   sent verbatim
`context` whose .bot          quiet-hours check,          to owner only
is the real app.bot           annual-reminder distance,
   │                          _is_quiet_now, etc. all
   ▼                          route through the same
same side effects as a        _now()/_today() helper
real scheduled fire           and see the override
(message sent, save_state,
db_log_job, etc.)
```

### Recommended Project Structure

No new files — this is a single-file addition to `bot.py`. New code should live in three places, following the file's existing section-header convention (`# ─── section ───`):

```
bot.py
├── (near _is_quiet_now, ~line 1554)  → add _now(tz, chat_id=None) and _today(chat_id=None) helpers
├── (scheduling helpers section, ~1845) → refactor schedule_user_* / restore_all_jobs closures to call _now()
├── (new "debug" handler section, placed near admin_stats ~3599) → debug_cmd() and its fire/clock/prompt dispatch
└── main() (~4160) → one new CommandHandler("debug", debug_cmd) registration, alongside the other owner-only command (adminstats)
```

### Pattern 1: Owner Gate (reuse verbatim)
**What:** A single-line guard rejecting any non-owner chat_id before any debug logic runs.
**When to use:** First line of every new debug command/subcommand.
**Example:**
```python
# Source: bot.py:2931-2935 (broadcast_cmd) and bot.py:3599-3601 (admin_stats) — read this session
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not MY_CHAT_ID or str(chat_id) != MY_CHAT_ID:
        await update.message.reply_text("Admin only.")
        return
    ...

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != MY_CHAT_ID:
        return
    ...
```
[VERIFIED: bot.py:2931-2935, bot.py:3599-3601 — read this session]

### Pattern 2: Subcommand dispatch on `context.args[0]`
**What:** A single `CommandHandler` whose function branches on the first positional arg, with each branch validating its own remaining args.
**When to use:** Any multi-action command family — this is the established codebase convention, used identically by two existing multi-verb commands.
**Example:**
```python
# Source: bot.py:3211-3348 (remind_cmd) — read this session; sub-verbs: list, add, once, remove, annual
async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_chat.id)
    args = context.args
    if not args:
        await update.message.reply_text("Usage:\n  /remind add HH:MM <message> ...")
        return
    sub = args[0].lower()
    if sub == "list":
        ...
    elif sub == "add":
        ...
    elif sub == "remove":
        ...
    else:
        await update.message.reply_text("Unknown subcommand. Use add, annual, once, list, or remove.")
```
`/habit add|done|list|remove` (`bot.py:3625-3674`, read this session) follows the identical shape. `/debug fire|clock|prompt` should match this pattern exactly, including the "unknown subcommand" fallback message.

### Pattern 3: In-memory file delivery (no disk write)
**What:** Send arbitrary text as a Telegram document without ever writing to the filesystem.
**When to use:** DEBUG-03's prompt dump when it exceeds 4096 chars — satisfies the "never written anywhere tracked by git" constraint by construction (nothing is written anywhere, tracked or not).
**Example:**
```python
# Source: bot.py:3563-3583 (export_data) — read this session
async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ...
    data_bytes = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    bio = BytesIO(data_bytes)
    bio.name = "secretary_export.json"
    await update.message.reply_document(
        document=bio,
        filename="secretary_export.json",
        caption="Your data export (conversation history not included)."
    )
```
For DEBUG-03, replace the JSON payload with the raw prompt string (`.encode("utf-8")`) and give the `BytesIO` a `.txt` filename (e.g. `system_prompt.txt`). `BytesIO` is already imported at module level (`from io import BytesIO`, `bot.py:13`).

### Pattern 4: Ambient per-user override, dual-written and overlay-read (existing precedent for `timezone`)
**What:** A preference that must (a) survive `state.json` overwrites/restores and (b) be visible to every read path without threading a new parameter through every function signature.
**When to use:** The simulated-now override (D-01 in CONTEXT.md — ambient, persistent).
**Example:**
```python
# Source: bot.py:533-548 (get_user) and bot.py:1067-1078 (set_timezone tool) — read this session
def get_user(chat_id: int) -> dict:
    key = str(chat_id)
    if key not in state["users"]:
        state["users"][key] = _new_user()
    u = state["users"][key]
    for k, v in _new_user().items():
        u.setdefault(k, v)
    for k, v in _new_user()["llm"].items():
        u["llm"].setdefault(k, v)
    # Overlay critical prefs from SQLite (survive state.json overwrites)
    db_tz = db_get_pref(key, "timezone")
    if db_tz:
        u["timezone"] = db_tz
    return u
```
CLAUDE.md's own architecture notes (quoted verbatim): *"`get_user(chat_id)` overlays `user_prefs.timezone` from SQLite onto `state.json`'s copy on every call — SQLite wins if set."* The debug-clock override should follow this exact shape: a new `db_get_pref(key, "debug_clock")` overlay inside (or alongside) `get_user()`, written via `db_set_pref` from `/debug clock`, read by the new `_now()`/`_today()` helpers — not by threading a new parameter through 26 call sites.

### Anti-Patterns to Avoid
- **Passing a `simulated_now` parameter through every function signature:** Would require touching every function up the call chain from `/tasks`, `_is_quiet_now`, the job closures, etc. — a much larger and more error-prone diff than a single overlay read inside a small `_now(tz, chat_id)` helper that internally calls `get_user`/`db_get_pref`.
- **Storing the debug clock only in-memory (module-level dict):** Contradicts the codebase's `nohup`-with-no-supervisor reality — see Runtime State Inventory below; an in-memory-only override silently vanishes on every restart with no user-visible signal, which is worse than either persisting it or explicit reset.
- **Writing the prompt dump to a temp file before sending:** `/export` already proves this isn't necessary — `BytesIO` sends directly from memory. Any tempfile path is an unnecessary policy risk given the CLAUDE.md privacy constraint on journal content.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Owner authorization | A new roles/permissions table | The existing `MY_CHAT_ID` string-equality check | Single-user bot; a new auth layer is unjustified complexity for one owner |
| Long-message delivery | A custom message-splitter with paragraph-aware chunking | `reply_document` + `BytesIO` (primary), or simple `text[i:i+4000]` chunking as a fallback for short overflows | The codebase has zero precedent for text-splitting; a file is strictly simpler and matches `/export`'s existing pattern |
| Subcommand argument parsing | `argparse` or a custom parser | Manual `context.args[0].lower()` dispatch, as `/remind` and `/habit` already do | Consistency with two existing multi-verb commands; `argparse` would be the only such usage in the file |

**Key insight:** Every piece of this phase has a near-identical existing precedent already in `bot.py`. The research task here was almost entirely "find the twin pattern," not "introduce a new one."

## Runtime State Inventory

**Trigger applies:** This phase does not rename anything, but it does introduce a **new persisted field** (the debug-clock override) into a live, restart-fragile system — the same "what survives a restart / what's live-only" analysis is directly relevant to the D-02 open question ("restart/expiry safety"), so it is included here.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | No existing debug-clock field anywhere in `state.json` schema (`_new_user()`, `bot.py:466-490`, read this session — full key list quoted above) or in the SQLite schema. This is a wholly new field. | Code addition: either add a `"debug_clock": null` key to `_new_user()` (state.json path) or a new `user_prefs` row keyed `"debug_clock"` (SQLite path, no schema migration needed — `user_prefs` is a generic key/value table, confirmed via `db_set_pref`/`db_get_pref` at `bot.py:224-241`, read this session). No existing data to migrate either way — this is greenfield within the schema. |
| Live service config | None — APScheduler jobs are recreated from `state.json` on every restart via `restore_all_jobs()` (`bot.py:2156-2211`, read this session); no external service (n8n-style) holds config outside git for this bot. | None. |
| OS-registered state | `bot.py` now runs under `secretary-bot.service` (systemd, `Restart=on-failure`) per STATE.md's logged decision — restarts are less frequent than the CONTEXT.md discussion assumed (which still describes `nohup` with no supervisor) but remain a real, recurring event (deploys, crashes, `systemctl restart`). | None new — the debug-clock persistence decision (below) should still assume restarts happen periodically. |
| Secrets/env vars | `MY_CHAT_ID` env var is the sole gate for this entire phase; no new secret introduced. | None. |
| Build artifacts | None — no packaging step touches this phase's code. | None. |

**Persistence recommendation for the debug-clock override (resolves CONTEXT.md open question #2):**

`[ASSUMED]` Store it in SQLite `user_prefs`, not `state.json`, for three concrete reasons grounded in code read this session:
1. **Precedent match:** `timezone` — the one other field that must survive `state.json` overwrites/restores — already lives in `user_prefs` and is overlaid in `get_user()` (`bot.py:544-547`, read this session). A debug-only ambient override has the identical shape (per-chat_id, must be visible to many read paths, must survive restart).
2. **`/export` isolation is automatic either way**, but `user_prefs` makes it structurally impossible to leak: `export_data` (`bot.py:3563-3583`, read this session) builds an explicit whitelist dict (`tasks`, `context`, `timezone`, `reminders`, `trackers`, `habits`, `journal`, `notes`) and never touches `user_prefs` directly except via the `timezone` field that's already deliberately included — a new `user_prefs` key is excluded by default with no extra code, whereas a `state.json` field requires remembering to leave it out of the whitelist (a manual step that could be forgotten).
3. **`user_prefs` already has a generic get/set/get-all API** (`db_set_pref`, `db_get_pref`, `db_get_all_prefs`, `bot.py:224-251`, read this session) — zero new SQL, zero migration.

**Expiry recommendation:** `[ASSUMED]` Given the bot now runs under systemd with `Restart=on-failure` (an unplanned crash restarts it, but the process is not restarted routinely otherwise), a silently-stale debug clock is a real risk if the owner forgets to `/debug clock reset` — CONTEXT.md itself names this risk. Recommend a bounded auto-expiry (e.g. store `{"until_iso": ..., "override_iso": ...}` as the pref value, e.g. 24h TTL) checked inside the `_now()` helper itself: if `datetime.utcnow() > until`, treat the override as absent (and lazily clear the pref on next write). This needs user/planner confirmation — it is a policy choice, not a technical constraint, so it should be surfaced as a discussion point rather than silently assumed by the plan.

## Common Pitfalls

### Pitfall 1: Scoping the `_now()` refactor to only job closures, missing ambient read paths
**What goes wrong:** ROADMAP.md's planning note says the refactor "touches every job closure once" — if the plan takes that literally, `/tasks` deadline badges, `_is_quiet_now`, and the annual-reminder distance calc (all reachable from ordinary owner commands, not just job closures) will keep using the real wall clock, and D-01 in CONTEXT.md ("ambient... `/tasks` deadline badges, quiet-hours checks, annual-reminder distance should all respond to it") will not actually hold.
**Why it happens:** The ROADMAP note was written before D-01 (ambient scope) was locked in the later CONTEXT.md discussion; the two documents now disagree on breadth, and CONTEXT.md explicitly asks the planner to reconcile this.
**How to avoid:** Use the full enumerated call-site table below (all 26 sites), not just the six job-closure sites, as the refactor's target list.
**Warning signs:** After implementation, `/debug clock 2027-01-01` followed immediately by `/tasks` still shows the real-date badge — that's the bug this pitfall describes.

### Pitfall 2: `date.today()` is a distinct call pattern from `datetime.now(tz)` and needs its own replacement
**What goes wrong:** A refactor that only greps for `datetime.now(` misses every `date.today()` call — and `date.today()` is what deadline badges, habit "done today" checks, and the idle-nudge inactivity calc actually use (not `datetime.now(tz)`). `date.today()` also has no timezone parameter at all — it's the *server's* local date, not the user's.
**Why it happens:** The two patterns look similar but are genuinely different functions with different signatures; a search for one alone under-counts the real site list.
**How to avoid:** The enumerated table below explicitly separates `datetime.now(tz)`, `date.today()`, and `datetime.utcnow()` call sites and classifies each by whether it's per-user (needs the override) or global/audit-log (does not).
**Warning signs:** Deadline badges (`_format_task_line`) or habit "done today" checks don't respond to `/debug clock` even after the job closures do — that's this exact gap.

### Pitfall 3: Firing a job on demand double-counts activity or streaks
**What goes wrong:** If DEBUG-01's "fire checkin_morning" implementation calls `chat(chat_id, prompt)` with the default `touch_activity=True`, the debug fire will mark the owner's day active and clear `pending_checkin` exactly as if they had genuinely engaged — polluting the real activity streak with debug-triggered noise.
**Why it happens:** `chat()`'s `touch_activity` parameter defaults to `True`; a naive re-use of the job closure body without preserving its existing `touch_activity=False` argument (already present in the real check-in/digest/catch-up closures, confirmed at `bot.py:1927`, `bot.py:2137-2141`, `bot.py:2206`, read this session) would silently flip this.
**How to avoid:** DEBUG-01 should literally invoke the existing closures (which already pass `touch_activity=False` for check-ins and the digest) rather than reimplementing their bodies — this is also why "same side effects" in the success criteria is achievable for free: the closures already encode the correct `touch_activity` value.
**Warning signs:** `/streak` or `activity_days` changes after a `/debug fire checkin_morning` call.

### Pitfall 4: Reminder targeting inconsistency between "fire" and existing list/remove conventions
**What goes wrong:** If DEBUG-01's reminder-firing subcommand invents a new identifier scheme (e.g. requiring the UUID), it breaks the mental model the owner already has from `/remind list` (1-based number) and the `get_reminders`/`remove_reminder` tool pair (also 1-based, per the tool's own description: *"Delete a reminder by its number (from get_reminders or /remind list)"*).
**Why it happens:** Reminders are stored with a UUID `id` field internally, so it's tempting to expose that directly since it's already "the identifier."
**How to avoid:** `/debug fire reminder <n>` should take the same 1-based index used everywhere else (`user["reminders"][n-1]`), converting internally to the UUID-keyed job name (`f"reminder_{chat_id}_{reminder['id']}"`) only when looking up or invoking the closure.
**Warning signs:** Owner has to cross-reference `/remind list` output against a different debug-only numbering scheme.

### Pitfall 5: Owner-gate omission on the `MY_CHAT_ID` unset case
**What goes wrong:** `MY_CHAT_ID` is an optional env var (`os.environ.get("MY_CHAT_ID")`, `bot.py:35`, read this session). `admin_stats`'s gate (`str(update.effective_chat.id) != MY_CHAT_ID`) technically still works when `MY_CHAT_ID` is `None` (nothing equals the string `"None"`... actually `str(chat_id) != None` is always `True` since `None` isn't a string, so the check correctly denies everyone) — but `broadcast_cmd`'s gate explicitly checks `not MY_CHAT_ID or str(chat_id) != MY_CHAT_ID` first. New debug commands should copy `broadcast_cmd`'s more defensive form (explicit `not MY_CHAT_ID` check) since it fails closed and is self-documenting, even though both forms are behaviorally equivalent given Python's `!=` semantics against `None`.
**Why it happens:** Two slightly different gate spellings already coexist in the codebase; a plan author copying the wrong one isn't wrong, just inconsistent.
**How to avoid:** Standardize on the `broadcast_cmd` spelling for new code (explicit `not MY_CHAT_ID or ...`).
**Warning signs:** Code review flags the inconsistency; not a functional bug either way.

## Code Examples

### Full enumerated `datetime.now(`/`date.today()`/`datetime.utcnow()` call-site inventory (DEBUG-02 breadth)

`[VERIFIED: bot.py — grep + Read this session, all line numbers current as of this research]`. Every row below was located via `grep -n "date.today()\|datetime.utcnow()\|datetime.now(" bot.py` and cross-checked by reading the surrounding function. Classification: **PER-USER** = must consult the chat_id's debug-clock override for D-01's ambient behavior to hold; **GLOBAL/AUDIT** = timestamps records (ts columns, rate limiting, job-fire log) that should stay on the real wall clock even during a debug session, since they are audit trails, not user-facing time-dependent behavior.

| Line | Function | Call | Classification | Why |
|------|----------|------|-----------------|-----|
| 177, 183 | (DB migration helper, inside `_init_db`) | `datetime.utcnow()` | GLOBAL/AUDIT | One-time legacy migration timestamps |
| 217 | `db_add_note` | `datetime.utcnow()` | GLOBAL/AUDIT | Note creation timestamp — a real record of when the note was actually made |
| 285 | `db_add_journal` | `datetime.utcnow()` | GLOBAL/AUDIT | Journal entry timestamp — same reasoning |
| 317 | `db_log_reminder` | `datetime.utcnow()` | GLOBAL/AUDIT | Reminder history log |
| 326 | `db_mark_reminder_removed` | `datetime.utcnow()` | GLOBAL/AUDIT | Reminder history log |
| 355 | `db_store_key` | `datetime.utcnow()` | GLOBAL/AUDIT | API key storage timestamp |
| 385 | `db_add_profile_memory` | `datetime.utcnow()` | GLOBAL/AUDIT | Profile memory timestamp |
| 398, 402 | `db_add_episodic_memory` | `datetime.utcnow()` | **PER-USER (borderline)** | TTL expiry (`+timedelta(days=30)`) is computed from real time; if the debug clock is meant to test "what does the bot say about a memory that's about to expire," this would need the override. Given episodic-memory TTL isn't named in any Phase 1 success criterion, treat as GLOBAL/AUDIT for this phase and flag as a known gap. |
| 407, 423 | `db_get_episodic_memory` / `db_expire_episodic` | `datetime.utcnow()` | Same as above — borderline, treat as GLOBAL/AUDIT this phase |
| 437 | `db_log_job` | `datetime.utcnow()` | GLOBAL/AUDIT | Job-fire audit log — must record the *real* fire time even for debug-fired jobs, so restart catch-up logic (`restore_all_jobs`) isn't confused by a debug session's fake time |
| 590 (`_touch_activity`) | `datetime.utcnow().date().isoformat()` | **PER-USER** | Marks `activity_days` — if the debug clock is active, "today" for activity-day purposes should arguably follow it so a debug-fired check-in doesn't record the wrong real date. Lower priority than deadline/quiet-hours/annual per CONTEXT.md's named examples, but a `_today(chat_id)` helper naturally covers it once it exists. |
| 603 (`_get_streak`) | `date.today()` | **PER-USER** | Streak calculation reads `activity_days` relative to today; consistent with the row above |
| 1088 | `set_timezone` tool (building a friendly UTC-offset label) | `datetime.now(_tz)` | GLOBAL — cosmetic | Only used to compute a UTC offset label for the reply string; not "time-dependent behavior" the phase cares about |
| 1133 | `get_current_time` tool | `datetime.now(tz)` | **PER-USER — named explicitly by DEBUG-02's own tool** | This is literally the `get_current_time` LLM tool — if the debug clock doesn't flow here, the LLM itself will report the real time even while `/tasks` shows simulated deadlines, an obvious inconsistency for the owner to notice mid-test |
| 1160, 1188, 1190, 1192 | `add_task` / `complete_task` tool | `date.fromisoformat`, `date.today()` | **PER-USER** | Recurring-task next-due-date computation and default due-date-on-complete; directly time-dependent behavior |
| 1352, 1369 | `log_tracker` tool | `_local_date(datetime.utcnow().isoformat(), tz)` | **PER-USER** | Tracker log date shown back to user |
| 1407, 1426, 1436 | `complete_habit` / `add_habit` tool | `date.today()` | **PER-USER** | Habit completion dates — directly testable via debug clock |
| 1480 | `set_today_focus` tool | `date.today()` | **PER-USER** | Today's-focus date stamp |
| 1521 (`_habit_streak`), 1531 (`_habit_summary_lines`) | helper | `date.today()` | **PER-USER** | Habit streak/summary display — not explicitly named in CONTEXT.md's three examples but same class of bug as `_format_task_line` |
| 1549 (`_is_muted`) | `datetime.utcnow()` | **PER-USER** | Mute-window check — CONTEXT.md doesn't name mute explicitly but it's the same "ambient time-dependent behavior" class as quiet hours |
| 1565 (`_is_quiet_now`) | `datetime.now(tz)` | **PER-USER — named explicitly in CONTEXT.md** | "a quiet-hours window that is not currently active" is a literal success-criterion example |
| 1596-1597 (`_format_task_line`) | `date.fromisoformat(due)`, `date.today()` | **PER-USER — named explicitly in CONTEXT.md** | "a deadline badge for a date that has not arrived" is a literal success-criterion example — and this is a `date.today()` site, not `datetime.now(tz)`, confirming Pitfall 2 |
| 1671 (`build_system_prompt`) | `date.today()` | **PER-USER** | Determines whether `today_focus` is shown in the prompt — DEBUG-03's prompt dump should reflect the same simulated date the rest of the ambient override uses, for internal consistency |
| 1719 (`build_system_prompt`, via local `_dt.now(_tz)`) | `datetime.now(_tz)` | GLOBAL — cosmetic | UTC offset label only, same as line 1088 |
| 1912 (`schedule_user_checkins` → `_job` closure) | `date.today()` | **PER-USER, job closure** | Stale-tracker check inside morning check-in |
| 1999, 2006 (`schedule_user_alerts` → `_deadline_job`) | `date.today()`, `date.fromisoformat` | **PER-USER, job closure — named explicitly** | Deadline-alert job body |
| 2027 (`_deadline_job`, `today.strftime("%m-%d")`) | derived from `date.today()` above | **PER-USER, job closure — named explicitly** | "an annual reminder months away" — this is the literal annual-reminder-distance mechanism named in CONTEXT.md |
| 2050 (`_habit_job`) | `date.today()` | **PER-USER, job closure** | Habit reminder body |
| 2081-2082 (`_idle_job`) | `date.fromisoformat`, `date.today()` | **PER-USER, job closure** | Idle-nudge 3-day-inactivity check |
| 2109 (`_weekly_digest_job`) | `datetime.now(tz).date()` | **PER-USER, job closure** | Sunday-only gate for the weekly digest |
| 2160 (`restore_all_jobs`) | `datetime.utcnow()` | GLOBAL/AUDIT | Startup catch-up-window comparison against real fire history — must stay on real wall clock, this runs once at process start before any per-chat debug clock exists |
| 2188 (`restore_all_jobs`) | `datetime.now(tz)` | GLOBAL/AUDIT | Same reasoning — startup catch-up scheduling, not a live user interaction |
| 2216 (`_days_ago_iso`) | `datetime.utcnow()` | GLOBAL/AUDIT | Used only for reminder-history query windows (audit), not user-facing "now" |
| 2436, 2460 | `add_task` command handler | `date.fromisoformat`, `date.today()` | **PER-USER** | Same as the tool version above (1160/1188), command-handler path instead of LLM-tool path |
| 2504, 2511, 2513, 2515 | `done_task` command handler | `datetime.utcnow()`, `date.today()`, `date.fromisoformat` | **PER-USER** | Same as `complete_task` tool, command-handler path |
| 2616 | `today_cmd` | `date.today()` | **PER-USER** | Command-handler path for `set_today_focus` |
| 2711, 2739, 2741 | `duedate_cmd` / `extend_cmd` | `date.fromisoformat`, `date.today()` | **PER-USER** | Command-handler due-date editing |
| 2904 | `time_cmd` (`/time`) | `datetime.now(tz)` | **PER-USER — directly user-facing** | `/time` literally displays "your local time" to the owner; if this doesn't respond to the debug clock, `/time` and `/tasks` will visibly disagree during a debug session |
| 2975 (`mute_cmd`) | `datetime.utcnow() + delta` | **PER-USER** | Computing `muted_until` — same class as `_is_muted` |
| 3145 (`manual_checkin`, `/checkin`) | `date.today()` | **PER-USER** | Manual check-in date stamp |
| 3203 (`_parse_once_delay`) | `datetime.now(tz)` | **PER-USER** | One-time reminder scheduling math — relative delay computation |
| 3455, 3521 | tracker/journal command handlers | `datetime.utcnow()` | **PER-USER** | Same class as the tool versions above |
| 3566 (`export_data`) | `datetime.utcnow()` | GLOBAL/AUDIT | `exported_at` metadata field — should reflect the real export time, not a simulated one, since it's a record of when the file was generated |
| 3650, 3664, 3697, 3706 | `habit_cmd` command handler | `date.today()`, `date.fromisoformat` | **PER-USER** | Command-handler path for habit add/done/streak, mirrors the tool versions |
| 3751 (`my_stats`, `/mystats`) | `date.today()` | **PER-USER** | 7-day activity bar chart in `/mystats` — directly visible to the owner during a debug session |

**Bottom line for the plan:** roughly 30 of the 26 grep hits (some lines have multiple calls) resolve to **PER-USER** sites that need to route through the new `_now(tz, chat_id)` / `_today(chat_id)` helpers for D-01's ambient scope to actually hold end-to-end, spanning tool handlers, command handlers, and job closures — not just the six job-closure sites the ROADMAP note called out. A handful of GLOBAL/AUDIT sites (DB timestamp columns, `restore_all_jobs`'s startup catch-up math, `export_data`'s `exported_at`) should deliberately keep using the real wall clock even during an active debug session, since they are records of real events, not user-facing time-dependent behavior.

### Proposed `_now()`/`_today()` helper shape
```python
# New helper, colocated with _is_quiet_now (bot.py ~1554) — not yet in the codebase, proposed shape
def _now(tz: ZoneInfo, chat_id: int | str | None = None) -> datetime:
    """Return the current time in tz, or a simulated time if chat_id has an active debug-clock override."""
    if chat_id is not None:
        override = db_get_pref(str(chat_id), "debug_clock")  # existing generic pref API, bot.py:234-241
        if override:
            try:
                return datetime.fromisoformat(override).astimezone(tz)
            except ValueError:
                pass
    return datetime.now(tz)

def _today(chat_id: int | str | None = None, tz: ZoneInfo | None = None) -> date:
    """Return today's date, honoring a chat_id's debug-clock override if set."""
    tz = tz or ZoneInfo("UTC")
    return _now(tz, chat_id).date()
```
Every PER-USER call site above becomes `_now(tz, chat_id)` or `_today(chat_id, tz)` in place of the bare stdlib call. This keeps the change mechanical and grep-verifiable (a plan-checker or code reviewer can `grep -n "datetime.now(\|date.today()"` post-refactor and expect zero remaining PER-USER hits).

### `/debug fire` invoking an existing closure directly (concept, not yet in codebase)
```python
# Illustrative — schedule_user_alerts's _deadline_job closure (bot.py:1995-2036, read this session)
# already has the exact shape needed: async def _deadline_job(context, _cid=chat_id): ...
# DEBUG-01 can call it directly:
async def debug_fire(update, context, job_key):
    cid = update.effective_chat.id
    # job_key resolves to one of the already-defined closures; the simplest approach is
    # restructuring schedule_user_alerts/schedule_user_checkins to *return* their closures
    # (or store them in a small registry) instead of only registering them with job_queue.run_daily,
    # so debug_fire can invoke `await _deadline_job(context, _cid=cid)` with the real `context`.
    ...
```
This is a structural note, not verified code: today the closures are defined *inside* the scheduling functions and immediately handed to `app.job_queue.run_daily(...)` — they are not returned or otherwise exposed. The planner must decide whether to (a) refactor these functions to also return/register their closures in a lookup dict keyed by job-type name, or (b) extract each closure to a module-level function taking `(context, chat_id)` so both the scheduler and the debug command can call it. Option (b) is more invasive (touches `schedule_user_checkins`/`schedule_user_alerts`/`schedule_user_reminder` structurally) but produces a clean, testable, directly-callable function per job type; option (a) is a smaller diff. This is a genuine design decision for the plan, not something research can resolve unilaterally — flagged as an Open Question below.

## State of the Art

Not applicable in the usual sense (no external library/API version drift risk here) — the "state of the art" for this phase is entirely about internal code conventions, all of which were read fresh this session and are current as of 2026-08-02.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | Debug-clock override should be stored in SQLite `user_prefs`, not `state.json` | Runtime State Inventory / Standard Stack | Low — both are viable per the codebase's own precedent; wrong choice means slightly more code to keep it out of `/export`, not a correctness bug |
| A2 | Debug-clock override should auto-expire (e.g. 24h TTL) rather than persist indefinitely | Runtime State Inventory | Medium — if the planner instead chooses "persist with no expiry," a forgotten `/debug clock` could silently skew the owner's real daily use (deadline badges, quiet hours) for an extended period, exactly the risk CONTEXT.md names; this needs explicit user confirmation, it is a policy call not a technical one |
| A3 | Episodic-memory TTL expiry (`db_expire_episodic`, 30-day window) should NOT respond to the debug clock this phase | Code Examples (call-site table) | Low — if wrong, a debug session testing "what happens near memory expiry" won't work, but no Phase 1 success criterion names this, so treating it as GLOBAL/AUDIT for now is a scope-conservative default |
| A4 | Real APScheduler jobs should keep firing normally (not be suppressed) while a debug clock is active for a user | Open Questions | Medium — this is explicitly unresolved in CONTEXT.md; see Open Questions below for the technical feasibility analysis this research provides, but the decision itself is not made here |

**If this table is empty:** N/A — see rows above.

## Open Questions

1. **Should real APScheduler jobs be suppressed while a debug clock is active for that chat_id?** (CONTEXT.md open question #3)
   - What we know: `restore_all_jobs` and `schedule_user_*` register jobs per-`chat_id` with `app.job_queue.run_daily(..., name=f"{job_type}_{chat_id}")` (`bot.py:1857-2153`, read this session) — job names are already chat_id-scoped, so suppressing only one user's jobs while leaving others running is technically straightforward (`app.job_queue.get_jobs_by_name(job_name)` + `job.schedule_removal()`, the exact pattern every `schedule_user_*` function already uses to reschedule).
   - What's unclear: Whether suppression is *desirable*. A real job landing mid-debug-session (e.g. a real deadline alert firing with the real date while the owner is mid-test with a fake date) could be confusing, but suppressing jobs adds a re-arm step (must remember to reschedule on `/debug clock reset`) and this is a single-user bot where "confusing" has low cost — the owner is the only one who sees it and knows a debug session is active.
   - Recommendation: `[ASSUMED]` Leave real jobs running (do not suppress). Simpler, avoids a re-arm bug class (forgetting to reschedule after reset), and the single-user/low-stakes context makes a stray real message during a debug session an acceptable cost. Flag for explicit user confirmation since CONTEXT.md left it genuinely open.

2. **Closure exposure structure for DEBUG-01 (return/registry vs. extract-to-module-level)?**
   - What we know: The six fireable job types' closures are currently defined inline inside `schedule_user_checkins` / `schedule_user_alerts` / `schedule_user_reminder` and never returned or exposed outside those functions (confirmed by reading `bot.py:1857-2153` in full this session).
   - What's unclear: Which refactor shape the planner should choose — extracting closures to module-level functions taking `(context, chat_id)` is cleaner and more testable but touches more surface area; returning a `dict[str, callable]` from each `schedule_user_*` function is a smaller diff but keeps the closures nested.
   - Recommendation: Extract to module-level async functions (one per job type: `_run_checkin(context, chat_id, label)`, `_run_deadline_alert(context, chat_id)`, `_run_habit_reminder(context, chat_id)`, `_run_idle_nudge(context, chat_id)`, `_run_weekly_digest(context, chat_id)`, `_run_reminder(context, chat_id, reminder)`), with `schedule_user_*` wrapping them in a thin closure only to satisfy `run_daily`'s signature. This is a larger but cleaner diff, and is the same shape as the `_now(tz, chat_id)` extraction pattern already recommended for DEBUG-02 — both phases of work benefit from "pull user-scoped logic out of the closures" as a unifying principle. The planner should size this against the "mechanical, one-time refactor, must not be revisited concurrently" ROADMAP note — since Phase 1 is explicitly the only phase allowed to touch this code this milestone, doing the more thorough extraction now (rather than a minimal registry hack) avoids a second pass later.

3. **Exact debug-clock input format for `/debug clock <value>`** — not resolved by CONTEXT.md or ROADMAP.md.
   - What we know: The success criteria examples ("a deadline badge for a date that has not arrived," "an annual reminder months away") only need date-level precision in most cases, but `/debug clock` conceptually sets a full "now," and quiet-hours testing needs time-of-day precision too.
   - What's unclear: Whether to accept `YYYY-MM-DD` (date only, defaulting to some fixed time), full ISO `YYYY-MM-DDTHH:MM`, or a relative offset (`+3d`).
   - Recommendation: Accept full ISO `YYYY-MM-DDTHH:MM` (parseable directly by `datetime.fromisoformat`, matching every other ISO-format field already in this codebase — `completed_at`, `ts`, etc.) as the primary form; this covers both the date-only and time-of-day test cases in the success criteria without inventing a new parser.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `python-telegram-bot` | All debug command handlers, `reply_document` | Yes `[VERIFIED: pip show python-telegram-bot]` | 22.8 | — |
| Python stdlib `zoneinfo`, `datetime`, `io` | `_now()` helper, prompt-dump delivery | Yes | 3.12 stdlib | — |
| `MY_CHAT_ID` env var | Owner gate | Present in `env` file per CLAUDE.md's documented config table (not independently re-verified this session — file is gitignored and not read, per privacy constraints) | — | If unset, every debug command should fail closed (reject all callers), matching existing `admin_stats`/`broadcast_cmd` behavior when unset |

No missing dependencies — this phase requires nothing not already installed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`@pytest.mark.asyncio` used throughout `tests/test_bot.py`, confirmed `[VERIFIED: tests/test_bot.py:526-594, read this session]`) |
| Config file | none — no `pytest.ini`/`pyproject.toml` found in repo root `[VERIFIED: ls repo root, this session]` |
| Quick run command | `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"` (per CLAUDE.md's documented unit-test-only invocation) |
| Full suite command | `python -m pytest tests/test_bot.py -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEBUG-01 | `/debug fire <job>` invokes the right closure with real side effects (message sent via mocked `context.bot.send_message`, `save_state`/`db_log_job` called) | unit | `pytest tests/test_bot.py -k debug_fire -x` | ❌ Wave 0 — new test file/section needed |
| DEBUG-01 | Non-owner chat_id is rejected by every `/debug` subcommand | unit | `pytest tests/test_bot.py -k debug_owner_gate -x` | ❌ Wave 0 |
| DEBUG-02 | `_now(tz, chat_id)` returns the override when set, real time when not | unit | `pytest tests/test_bot.py -k test_now_helper -x` | ❌ Wave 0 |
| DEBUG-02 | `/debug clock <iso>` then `/tasks` shows a badge computed against the simulated date | unit | `pytest tests/test_bot.py -k debug_clock_ambient -x` | ❌ Wave 0 |
| DEBUG-02 | `/debug clock reset` restores real-time behavior | unit | `pytest tests/test_bot.py -k debug_clock_reset -x` | ❌ Wave 0 |
| DEBUG-03 | `/debug prompt` returns `build_system_prompt(user, chat_id)` verbatim, no LLM call made (assert the mocked `AsyncOpenAI` client's `.chat.completions.create` was never invoked) | unit | `pytest tests/test_bot.py -k debug_prompt_no_llm_call -x` | ❌ Wave 0 |
| DEBUG-03 | Prompt >4096 chars is delivered as a document, not truncated | unit | `pytest tests/test_bot.py -k debug_prompt_long -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"`
- **Per wave merge:** `python -m pytest tests/test_bot.py -v -k "not sanity and not nl"` (the `sanity`/`nl` suites require live API keys and real LLM calls — appropriate for pre-ship spot checks, not per-commit, per existing project convention documented in CLAUDE.md)
- **Phase gate:** Full non-LLM suite green before `/gsd-verify-work`; a manual UAT pass against the real bot (owner account) for the three DEBUG success criteria, since Telegram message delivery and real job-firing side effects are only partially mockable

### Wave 0 Gaps
- [ ] No existing tests reference `MY_CHAT_ID`, owner-gating, or `touch_activity=False` verification — needs new fixtures (a way to set `bot.MY_CHAT_ID` for a test and a mock `context.bot.send_message`/`context.application.job_queue`)
- [ ] No existing test exercises `db_set_pref`/`db_get_pref` round-trip for a new pref key — straightforward given `isolate_db` fixture already provides a fresh SQLite temp file per test (`tests/test_bot.py:110`, read this session)
- [ ] No existing test mocks a full `chat()` call to assert an LLM client method was *not* called (needed for DEBUG-03's "no LLM call made" criterion) — needs a `MagicMock`/`AsyncMock` on `get_llm_client`'s return value, verifying `.chat.completions.create.assert_not_called()`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Single-user bot; Telegram's own chat identity is the only identity boundary, unchanged by this phase |
| V3 Session Management | No | No session concept in this bot |
| V4 Access Control | **Yes** | Owner-only gate reusing `str(chat_id) != MY_CHAT_ID` exactly (fail-closed when `MY_CHAT_ID` unset, per `broadcast_cmd`'s defensive spelling) — this is the phase's core security control |
| V5 Input Validation | **Yes** | `/debug clock <value>` must validate the ISO timestamp with `datetime.fromisoformat` inside a `try/except`, exactly like every other date-parsing site in the file (`date.fromisoformat` wrapped in `try/except ValueError` throughout — e.g. `bot.py:1160-1163`, `bot.py:2711`, read this session) — reject and message on parse failure rather than raising |
| V6 Cryptography | No | No new secret or cryptographic material introduced |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Non-owner Telegram user discovers and invokes `/debug` commands | Elevation of Privilege | Owner gate on every subcommand, checked first, before any argument parsing or state mutation — matches existing `admin_stats`/`broadcast_cmd` placement (gate is the very first statement in the function body) |
| Debug output (prompt dump containing journal/notes/profile facts) leaked via a non-owner Telegram forward, or accidentally logged | Information Disclosure | Send only to `update.effective_chat.id` (never broadcast); do not `logger.info()` the full prompt text (existing `chat()` tool-call logging at `bot.py:1816` logs tool args/results, not the system prompt itself — new debug code must not add a log line that echoes prompt content, since `nohup.out`/systemd journal is not gitignored-equivalent protection and CLAUDE.md's privacy constraint is about "written anywhere tracked by git," but a log file is a different, still-undesirable leak vector worth avoiding even though it's technically out of the literal constraint's wording) |
| Debug clock silently active for weeks, corrupting the owner's own real deadline/quiet-hours experience | Tampering (of the owner's own trusted data view) | Bounded auto-expiry (see Assumptions Log A2) and/or a persistent visual indicator (e.g. every reply while a debug clock is active could be prefixed `🕐 [SIMULATED: 2027-01-01]`) — the second part is a UX nicety, not a hard requirement, but meaningfully reduces the "forgot it was on" risk class named in CONTEXT.md |

## Sources

### Primary (HIGH confidence — direct code read this session)
- `/home/ec2-user/secretary-bot/bot.py` — full read of lines 1-80, 213-260, 466-630, 1063-1250, 1543-1650, 1647-1857, 1857-2216, 2897-2960, 3188-3360, 3563-3630, 3625-3675, 4160-4238 this session
- `/home/ec2-user/secretary-bot/tests/test_bot.py` — lines 1-120, plus grep for `test_`/`touch_activity`/`MY_CHAT_ID`
- `/home/ec2-user/secretary-bot/.planning/phases/01-debug-and-dry-run/01-CONTEXT.md`
- `/home/ec2-user/secretary-bot/.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/config.json`
- `/home/ec2-user/secretary-bot/CLAUDE.md`, `/home/ec2-user/secretary-bot/.claude/CLAUDE.md`
- Installed package verification: `pip show python-telegram-bot` → 22.8; `python3 -c "from telegram.constants import MessageLimit; print(MessageLimit.MAX_TEXT_LENGTH)"` → 4096, both run this session in the project venv

### Secondary (MEDIUM confidence)
- None used — no web/docs lookups were needed since this phase is entirely internal-codebase-scoped and no new external library is introduced.

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; existing installed versions verified directly via `pip show`
- Architecture: HIGH — every pattern cited was read from `bot.py` this session with line numbers
- Pitfalls: HIGH — derived directly from reading the actual closures/call sites, not from general debugging-tooling folklore
- Call-site enumeration (DEBUG-02 breadth): HIGH — exhaustive grep cross-checked against manual reads of every match
- Persistence/expiry/suppression recommendations: MEDIUM — these are policy judgment calls explicitly left open in CONTEXT.md; the technical feasibility analysis is HIGH confidence, the recommended defaults are `[ASSUMED]` and flagged for confirmation

**Research date:** 2026-08-02
**Valid until:** No expiry in the usual sense — this research is tied to the current state of `bot.py`; it goes stale the moment any other phase or quick-task modifies the enumerated call sites, the job-scheduling functions, or `build_system_prompt`. Re-verify line numbers before planning if significant time has passed or other work has touched `bot.py`.
